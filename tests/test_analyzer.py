from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from greenhouse_analyzer.analyzer import (
    Config,
    analyze_dashboard,
    load_environment_file,
    publish_analysis,
    read_prompt,
    render_dashboard,
)


class FakeResponse:
    def __init__(self, body: bytes, content_type: str = "application/json") -> None:
        self.body = body
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self.body if size < 0 else self.body[:size]


class FakeAgent:
    def __init__(self, content: object, trailing_user_message: bool = False) -> None:
        self.content = content
        self.trailing_user_message = trailing_user_message
        self.request: dict[str, object] | None = None

    def invoke(self, input: dict[str, object]) -> dict[str, object]:
        self.request = input
        messages = [SimpleNamespace(type="ai", content=self.content)]
        if self.trailing_user_message:
            messages.append(SimpleNamespace(type="human", content="Echoed prompt"))
        return {"messages": messages}


def make_config(directory: Path, **overrides: str) -> Config:
    values = {
        "GREENHOUSE_GRAFANA_URL": "https://grafana.example.test",
        "GREENHOUSE_GRAFANA_TOKEN": "grafana-secret",
        "GREENHOUSE_OLLAMA_BASE_URL": "http://ollama.example.test:11434",
        "GREENHOUSE_OLLAMA_MODEL": "vision-tools:latest",
        "GREENHOUSE_ANALYSIS_PROMPT_FILE": str(directory / "prompt.txt"),
        "GREENHOUSE_SITE_CONTENT_DIRECTORY": str(directory / "content"),
        "GREENHOUSE_SITE_OUTPUT_DIRECTORY": str(directory / "output"),
        "GREENHOUSE_PELICAN_SETTINGS": str(directory / "pelicanconf.py"),
    }
    values.update(overrides)
    return Config.from_env(values)


class ConfigTests(unittest.TestCase):
    def test_environment_file_loads_defaults_without_replacing_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            environment_file = Path(temporary_directory) / "analyzer.env"
            environment_file.write_text(
                "# service settings\n"
                "GREENHOUSE_GRAFANA_URL=https://grafana.example.test\n"
                "GREENHOUSE_SITE_NAME=Greenhouse Daily\n"
                'GREENHOUSE_OLLAMA_MODEL="qwen3-vl:8b"\n'
            )
            values = {"GREENHOUSE_GRAFANA_URL": "https://override.example.test"}

            load_environment_file(environment_file, values)

            self.assertEqual(
                values["GREENHOUSE_GRAFANA_URL"], "https://override.example.test"
            )
            self.assertEqual(values["GREENHOUSE_SITE_NAME"], "Greenhouse Daily")
            self.assertEqual(values["GREENHOUSE_OLLAMA_MODEL"], "qwen3-vl:8b")

    def test_requires_absolute_service_urls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaisesRegex(ValueError, "GREENHOUSE_GRAFANA_URL"):
                make_config(Path(temporary_directory), GREENHOUSE_GRAFANA_URL="grafana")

    def test_reads_nonempty_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            (directory / "prompt.txt").write_text("  Inspect the last day.  \n")
            self.assertEqual(read_prompt(make_config(directory)), "Inspect the last day.")

class GrafanaTests(unittest.TestCase):
    def test_renders_configured_dashboard_as_png(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            config = make_config(Path(temporary_directory))
            captured = {}

            def open_request(request: object, timeout: float) -> FakeResponse:
                captured["request"] = request
                captured["timeout"] = timeout
                return FakeResponse(b"png-data", "image/png")

            with patch(
                "greenhouse_analyzer.analyzer._urlopen", side_effect=open_request
            ):
                image = render_dashboard(config)

            request = captured["request"]
            parsed = urlsplit(request.full_url)
            self.assertEqual(parsed.path, "/render/d/greenhouse-climate/greenhouse-climate")
            self.assertEqual(parse_qs(parsed.query)["from"], ["now-24h"])
            self.assertEqual(request.get_header("Authorization"), "Bearer grafana-secret")
            self.assertEqual(image, b"png-data")

    def test_rejects_non_image_response(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            config = make_config(Path(temporary_directory))
            with patch(
                "greenhouse_analyzer.analyzer._urlopen",
                return_value=FakeResponse(b"login page", "text/html"),
            ):
                with self.assertRaisesRegex(RuntimeError, "expected image/png"):
                    render_dashboard(config)


class AgentTests(unittest.TestCase):
    def test_pairs_prompt_with_base64_dashboard(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            config = make_config(Path(temporary_directory))
            agent = FakeAgent([{"type": "text", "text": "Plants look stable."}])

            result = analyze_dashboard(config, "Check temperature trends.", b"png", agent)

            self.assertEqual(result, "Plants look stable.")
            content = agent.request["messages"][0]["content"]
            self.assertEqual(content[0]["text"], "Check temperature trends.")
            self.assertEqual(
                content[1]["image_url"], "data:image/png;base64,cG5n"
            )

    def test_returns_ai_output_instead_of_trailing_human_message(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            config = make_config(Path(temporary_directory))
            agent = FakeAgent("Plants look stable.", trailing_user_message=True)

            result = analyze_dashboard(config, "Inspect the dashboard.", b"png", agent)

            self.assertEqual(result, "Plants look stable.")

    def test_rejects_empty_ai_output_instead_of_returning_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            config = make_config(Path(temporary_directory))
            agent = FakeAgent("", trailing_user_message=True)

            with self.assertRaisesRegex(RuntimeError, "empty analysis"):
                analyze_dashboard(config, "Inspect the dashboard.", b"png", agent)


class PublishingTests(unittest.TestCase):
    def test_publishes_dated_article_and_image_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            config = make_config(directory)
            published_at = datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc)

            with patch(
                "greenhouse_analyzer.analyzer.build_site"
            ) as build_site:
                first_path = publish_analysis(
                    config, "## Status\n<script>alert(1)</script>", b"first", published_at
                )
                second_path = publish_analysis(
                    config,
                    "## Updated\nStable <script>alert(2)</script> conditions.",
                    b"second",
                    published_at,
                )

            self.assertEqual(first_path, second_path)
            self.assertEqual(
                list(config.site_content_directory.glob("greenhouse-analysis-*.md")),
                [first_path],
            )
            article = first_path.read_text()
            self.assertIn("Title: Greenhouse Analysis - September 12, 2026", article)
            self.assertIn(
                "({static}/images/greenhouse-dashboard-2026-09-12.png)", article
            )
            self.assertIn("## Updated", article)
            self.assertNotIn("<script>", article)
            self.assertIn("&lt;script&gt;alert(2)&lt;/script&gt;", article)
            self.assertEqual(
                (
                    config.site_content_directory
                    / "images"
                    / "greenhouse-dashboard-2026-09-12.png"
                ).read_bytes(),
                b"second",
            )
            self.assertEqual(build_site.call_count, 2)


if __name__ == "__main__":
    unittest.main()