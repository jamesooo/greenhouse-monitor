from __future__ import annotations

import argparse
import base64
import html
import logging
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen


logger = logging.getLogger("greenhouse-analyzer")
DEFAULT_ENV_FILE = Path("/etc/greenhouse-analyzer/greenhouse-analyzer.env")


class Agent(Protocol):
    def invoke(self, input: dict[str, Any]) -> dict[str, Any]: ...


def load_environment_file(
    path: Path, environ: dict[str, str] | None = None
) -> None:
    values = os.environ if environ is None else environ
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return
    except OSError as error:
        raise RuntimeError(f"Could not read environment file {path}: {error}") from error

    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise ValueError(f"Invalid environment entry in {path}:{line_number}")
        name, value = stripped.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name or not name.replace("_", "a").isalnum() or name[0].isdigit():
            raise ValueError(f"Invalid environment name in {path}:{line_number}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values.setdefault(name, value)


@dataclass(frozen=True)
class Config:
    grafana_url: str
    grafana_dashboard_uid: str
    grafana_dashboard_slug: str
    grafana_token: str | None
    grafana_time_range: str
    grafana_width: int
    grafana_height: int
    grafana_timezone: str
    image_url: str
    ollama_base_url: str
    ollama_model: str
    ollama_temperature: float
    ollama_num_predict: int
    prompt_file: Path
    skills_directory: Path
    site_content_directory: Path
    site_output_directory: Path
    pelican_settings: Path
    request_timeout: float
    max_image_bytes: int

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Config:
        values = os.environ if environ is None else environ
        config = cls(
            grafana_url=values.get("GREENHOUSE_GRAFANA_URL", "").rstrip("/"),
            grafana_dashboard_uid=values.get(
                "GREENHOUSE_GRAFANA_DASHBOARD_UID", "greenhouse-climate"
            ),
            grafana_dashboard_slug=values.get(
                "GREENHOUSE_GRAFANA_DASHBOARD_SLUG", "greenhouse-climate"
            ),
            grafana_token=values.get("GREENHOUSE_GRAFANA_TOKEN") or None,
            grafana_time_range=values.get("GREENHOUSE_GRAFANA_TIME_RANGE", "now-24h"),
            grafana_width=int(values.get("GREENHOUSE_GRAFANA_WIDTH", "1600")),
            grafana_height=int(values.get("GREENHOUSE_GRAFANA_HEIGHT", "1200")),
            grafana_timezone=values.get("GREENHOUSE_GRAFANA_TIMEZONE", "browser"),
            image_url=values.get(
                "GREENHOUSE_IMAGE_URL", "https://datastore.tail63be5a.ts.net/"
            ),
            ollama_base_url=values.get(
                "GREENHOUSE_OLLAMA_BASE_URL", "http://127.0.0.1:11434"
            ).rstrip("/"),
            ollama_model=values.get("GREENHOUSE_OLLAMA_MODEL", "qwen3-vl:8b"),
            ollama_temperature=float(
                values.get("GREENHOUSE_OLLAMA_TEMPERATURE", "0.1")
            ),
            ollama_num_predict=int(
                values.get("GREENHOUSE_OLLAMA_NUM_PREDICT", "700")
            ),
            prompt_file=Path(
                values.get(
                    "GREENHOUSE_ANALYSIS_PROMPT_FILE",
                    "/etc/greenhouse-analyzer/prompt.txt",
                )
            ),
            skills_directory=Path(
                values.get(
                    "GREENHOUSE_ANALYSIS_SKILLS_DIRECTORY",
                    "/etc/greenhouse-analyzer/skills",
                )
            ),
            site_content_directory=Path(
                values.get(
                    "GREENHOUSE_SITE_CONTENT_DIRECTORY",
                    "/var/lib/greenhouse-analyzer/content",
                )
            ),
            site_output_directory=Path(
                values.get(
                    "GREENHOUSE_SITE_OUTPUT_DIRECTORY",
                    "/var/lib/greenhouse-analyzer/output",
                )
            ),
            pelican_settings=Path(
                values.get(
                    "GREENHOUSE_PELICAN_SETTINGS",
                    "/etc/greenhouse-analyzer/pelicanconf.py",
                )
            ),
            request_timeout=float(
                values.get("GREENHOUSE_ANALYSIS_REQUEST_TIMEOUT", "120")
            ),
            max_image_bytes=int(
                values.get("GREENHOUSE_ANALYSIS_MAX_IMAGE_BYTES", "10485760")
            ),
        )
        config.validate()
        return config

    def validate(self) -> None:
        for name, value in (
            ("GREENHOUSE_GRAFANA_URL", self.grafana_url),
            ("GREENHOUSE_IMAGE_URL", self.image_url),
            ("GREENHOUSE_OLLAMA_BASE_URL", self.ollama_base_url),
        ):
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"{name} must be an absolute HTTP(S) URL")
        if not self.grafana_dashboard_uid:
            raise ValueError("GREENHOUSE_GRAFANA_DASHBOARD_UID must not be empty")
        if not self.ollama_model:
            raise ValueError("GREENHOUSE_OLLAMA_MODEL must not be empty")
        if self.grafana_width <= 0 or self.grafana_height <= 0:
            raise ValueError("Grafana render dimensions must be positive")
        if self.ollama_num_predict <= 0:
            raise ValueError("GREENHOUSE_OLLAMA_NUM_PREDICT must be positive")
        if self.request_timeout <= 0 or self.max_image_bytes <= 0:
            raise ValueError("request timeout and image size limit must be positive")


def _urlopen(request: Request, timeout: float) -> Any:
    return urlopen(request, timeout=timeout)


def render_dashboard(config: Config) -> bytes:
    query = urlencode(
        {
            "from": config.grafana_time_range,
            "to": "now",
            "width": config.grafana_width,
            "height": config.grafana_height,
            "tz": config.grafana_timezone,
        }
    )
    path = "/render/d/{}/{}".format(
        quote(config.grafana_dashboard_uid, safe=""),
        quote(config.grafana_dashboard_slug, safe=""),
    )
    headers = {"Accept": "image/png", "User-Agent": "greenhouse-analyzer/1.0"}
    if config.grafana_token:
        headers["Authorization"] = f"Bearer {config.grafana_token}"
    request = Request(f"{config.grafana_url}{path}?{query}", headers=headers)
    try:
        with _urlopen(request, config.request_timeout) as response:
            content_type = response.headers.get_content_type()
            image = response.read(config.max_image_bytes + 1)
    except (HTTPError, URLError, TimeoutError) as error:
        raise RuntimeError(f"Grafana dashboard render failed: {error}") from error
    if content_type != "image/png":
        raise RuntimeError(f"Grafana render returned {content_type}, expected image/png")
    if not image:
        raise RuntimeError("Grafana render returned an empty image")
    if len(image) > config.max_image_bytes:
        raise RuntimeError("Grafana render exceeded GREENHOUSE_ANALYSIS_MAX_IMAGE_BYTES")
    return image


def fetch_greenhouse_image(config: Config) -> bytes:
    request = Request(
        config.image_url,
        headers={"Accept": "image/jpeg", "User-Agent": "greenhouse-analyzer/1.0"},
    )
    try:
        with _urlopen(request, config.request_timeout) as response:
            content_type = response.headers.get_content_type()
            image = response.read(config.max_image_bytes + 1)
    except (HTTPError, URLError, TimeoutError) as error:
        raise RuntimeError(f"Latest greenhouse image request failed: {error}") from error
    if content_type != "image/jpeg":
        raise RuntimeError(
            f"Latest greenhouse image returned {content_type}, expected image/jpeg"
        )
    if not image:
        raise RuntimeError("Latest greenhouse image request returned an empty image")
    if len(image) > config.max_image_bytes:
        raise RuntimeError(
            "Latest greenhouse image exceeded GREENHOUSE_ANALYSIS_MAX_IMAGE_BYTES"
        )
    return image


def read_prompt(config: Config) -> str:
    try:
        prompt = config.prompt_file.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise RuntimeError(f"Could not read analysis prompt: {error}") from error
    if not prompt:
        raise RuntimeError("Analysis prompt is empty")
    return prompt


def validate_skills_directory(config: Config) -> None:
    try:
        entries = list(config.skills_directory.iterdir())
    except OSError as error:
        raise RuntimeError(
            f"Could not read skills directory {config.skills_directory}: {error}"
        ) from error

    for entry in entries:
        if entry.name.startswith(".") or not entry.is_dir():
            continue
        skill_file = entry / "SKILL.md"
        if not skill_file.is_file():
            raise RuntimeError(f"Skill directory {entry} does not contain SKILL.md")
        try:
            if not skill_file.read_text(encoding="utf-8").strip():
                raise RuntimeError(f"Skill file {skill_file} is empty")
        except OSError as error:
            raise RuntimeError(f"Could not read skill file {skill_file}: {error}") from error


def _create_agent(config: Config) -> Agent:
    from deepagents import create_deep_agent
    from deepagents.backends.filesystem import FilesystemBackend
    from langchain_ollama import ChatOllama

    validate_skills_directory(config)
    model = ChatOllama(
        model=config.ollama_model,
        base_url=config.ollama_base_url,
        reasoning=False,
        temperature=config.ollama_temperature,
        num_predict=config.ollama_num_predict,
    )
    return create_deep_agent(
        model=model,
        tools=[],
        skills=["/"],
        backend=FilesystemBackend(root_dir=config.skills_directory),
        system_prompt=(
            "You analyze a greenhouse operations dashboard. Base every conclusion on "
            "visible evidence, distinguish observations from uncertainty, call out "
            "conditions needing attention, and never claim to have taken action. "
            "Return a concise report in Markdown with useful headings and lists."
        ),
    )


def _message_text(message: Any) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts).strip()
    return ""


def analyze_dashboard(
    config: Config,
    prompt: str,
    dashboard_image: bytes,
    greenhouse_image: bytes,
    agent: Agent | None = None,
) -> str:
    dashboard_base64 = base64.b64encode(dashboard_image).decode("ascii")
    greenhouse_base64 = base64.b64encode(greenhouse_image).decode("ascii")
    request = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"{prompt}\n\n"
                            "The first image is the rendered monitoring dashboard. "
                            "The second image is the latest full-resolution greenhouse "
                            "camera capture; use it for detailed visual observations."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": f"data:image/png;base64,{dashboard_base64}",
                    },
                    {
                        "type": "image_url",
                        "image_url": f"data:image/jpeg;base64,{greenhouse_base64}",
                    },
                ],
            }
        ]
    }
    result = (agent or _create_agent(config)).invoke(request)
    messages = result.get("messages")
    if not isinstance(messages, list):
        raise RuntimeError("Deep Agent returned no messages")
    for message in reversed(messages):
        if getattr(message, "type", None) != "ai":
            continue
        analysis = _message_text(message)
        if analysis:
            return analysis
    raise RuntimeError("Deep Agent returned an empty analysis")


def write_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
        temporary_path.replace(path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def publish_analysis(
    config: Config,
    analysis: str,
    dashboard_image: bytes,
    greenhouse_image: bytes,
    published_at: datetime | None = None,
) -> Path:
    timestamp = published_at or datetime.now().astimezone()
    date_slug = timestamp.date().isoformat()
    image_name = f"greenhouse-dashboard-{date_slug}.png"
    greenhouse_image_name = f"greenhouse-full-view-{date_slug}.jpg"
    article_path = config.site_content_directory / f"greenhouse-analysis-{date_slug}.md"
    image_path = config.site_content_directory / "images" / image_name
    greenhouse_image_path = (
        config.site_content_directory / "images" / greenhouse_image_name
    )
    safe_analysis = html.escape(analysis, quote=False)
    article = (
        f"Title: Greenhouse Analysis - {timestamp:%B %-d, %Y}\n"
        f"Date: {timestamp.isoformat(timespec='seconds')}\n"
        f"Slug: greenhouse-analysis-{date_slug}\n"
        "Category: Daily Analysis\n"
        "Tags: greenhouse, climate, plants\n"
        "Summary: Daily greenhouse dashboard and AI-assisted review.\n\n"
        f"![Greenhouse dashboard for {date_slug}]({{static}}/images/{image_name})\n\n"
        f"![Full greenhouse view for {date_slug}]"
        f"({{static}}/images/{greenhouse_image_name})\n\n"
        f"{safe_analysis}\n"
    )
    write_file(image_path, dashboard_image)
    write_file(greenhouse_image_path, greenhouse_image)
    write_file(article_path, article.encode("utf-8"))
    build_site(config)
    return article_path


def build_site(config: Config) -> None:
    config.site_content_directory.mkdir(parents=True, exist_ok=True)
    config.site_output_directory.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "pelican",
        str(config.site_content_directory),
        "--settings",
        str(config.pelican_settings),
        "--output",
        str(config.site_output_directory),
    ]
    try:
        subprocess.run(command, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError(f"Pelican site build failed: {error}") from error


def run(config: Config, dry_run: bool = False) -> str:
    prompt = read_prompt(config)
    logger.info("Rendering Grafana dashboard %s", config.grafana_dashboard_uid)
    dashboard_image = render_dashboard(config)
    logger.info("Fetching latest full-resolution greenhouse image")
    greenhouse_image = fetch_greenhouse_image(config)
    logger.info("Analyzing dashboard with Ollama model %s", config.ollama_model)
    analysis = analyze_dashboard(
        config, prompt, dashboard_image, greenhouse_image
    )
    if dry_run:
        print(analysis)
    else:
        article_path = publish_analysis(
            config, analysis, dashboard_image, greenhouse_image
        )
        logger.info("Published greenhouse analysis from %s", article_path)
    return analysis


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_FILE,
        help=f"load defaults from an environment file (default: {DEFAULT_ENV_FILE})",
    )
    parser.add_argument(
        "--check-config", action="store_true", help="validate configuration and exit"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="analyze and print without publishing"
    )
    args = parser.parse_args()
    try:
        load_environment_file(args.env_file)
    except (OSError, RuntimeError, ValueError) as error:
        logging.basicConfig(
            level="ERROR", format="%(asctime)s %(levelname)s %(name)s: %(message)s"
        )
        logger.error("%s", error)
        raise SystemExit(1) from error
    logging.basicConfig(
        level=os.environ.get("GREENHOUSE_ANALYSIS_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = Config.from_env()
        if args.check_config:
            read_prompt(config)
            validate_skills_directory(config)
            logger.info("Configuration is valid")
            return
        run(config, dry_run=args.dry_run)
    except (OSError, RuntimeError, ValueError) as error:
        logger.error("%s", error)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()