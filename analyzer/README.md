# Greenhouse Analyzer

`greenhouse-analyzer` renders the previous 24 hours of the greenhouse Grafana
dashboard, pairs that PNG with an administrator-owned prompt, asks a Deep Agent
backed by Ollama for an analysis, and publishes the result as a dated Pelican
article with the dashboard image.

Each invocation performs one analysis. The Debian package installs a systemd
timer that runs it daily at 08:00 local time with up to 10 minutes of randomized
delay. A persistent timer runs a missed invocation after `homelab01` starts. A
second service serves the generated static site at `http://127.0.0.1:8081/`.

## Prerequisites

### Grafana image rendering

Grafana must have its supported standalone Image Renderer service configured.
The analyzer calls:

```text
GET /render/d/greenhouse-climate/greenhouse-climate
```

Configure `GF_RENDERING_SERVER_URL`, `GF_RENDERING_CALLBACK_URL`, and matching
renderer tokens on Grafana and the renderer. The older in-process renderer
plugin is deprecated. The renderer is relatively resource intensive; current
Grafana guidance recommends 16 GiB of memory and four CPU cores for its host.

When anonymous dashboard access is disabled, create a Grafana service account
with Viewer access and put its token in `GREENHOUSE_GRAFANA_TOKEN`. Verify the
configured URL and token return `Content-Type: image/png` before testing the
analyzer.

### Ollama

The configured Ollama endpoint must be reachable from `homelab01`. Its model
must support both image input and tool calling because Deep Agents supplies a
tool-capable agent harness. The packaged default is `qwen3-vl:8b`, which requires
Ollama 0.12.7 or newer:

```sh
ollama pull qwen3-vl:8b
```

The model name, API URL, temperature, and maximum generated tokens are all
configurable. A larger Qwen3-VL variant can be selected without rebuilding the
package.

## Configuration

The Debian conffiles are:

- `/etc/greenhouse-analyzer/greenhouse-analyzer.env` for service endpoints,
  credentials, model settings, render dimensions, site paths, and listener
  settings;
- `/etc/greenhouse-analyzer/pelicanconf.py` for Pelican URLs, feeds, and other
  site-generation settings;
- `/etc/greenhouse-analyzer/prompt.txt` for the daily analysis request.

All three files are preserved across package upgrades. Generated state lives
under `/var/lib/greenhouse-analyzer`:

- `content/greenhouse-analysis-YYYY-MM-DD.md` retains the source articles;
- `content/images/greenhouse-dashboard-YYYY-MM-DD.png` retains daily renders;
- `output/` contains the rebuilt static site.

A repeated run on the same local date replaces that date's article and image
instead of creating duplicates. Model-produced HTML is escaped before Markdown
rendering, while headings and lists remain available. Back up `content/` to
retain the report history; `output/` is reproducible.

The packaged theme shows the newest report in full on the home page and keeps a
dated archive. Set `GREENHOUSE_SITE_NAME`, `GREENHOUSE_SITE_URL`, and
`GREENHOUSE_SITE_TIMEZONE` in the environment file as needed. Leave the site URL
empty when publishing at the domain root.

## Debian Package

Build from the repository root:

```sh
bash packaging/analyzer/build-deb.sh
```

Install or upgrade it on `homelab01`:

```sh
sudo apt install ./greenhouse-analyzer_1.0.0-4_all.deb
```

Package configuration downloads Python dependencies into
`/opt/greenhouse-analyzer/venv`, so it requires network access. The package
creates an unprivileged `greenhouse-analyzer` account, builds an initial empty
site, and enables both `greenhouse-analyzer.timer` and
`greenhouse-analysis-site.service`.

After editing the conffiles, validate them and publish the first report. Direct
CLI invocations load `/etc/greenhouse-analyzer/greenhouse-analyzer.env` by
default; use `--env-file PATH` to select another file:

```sh
sudo -u greenhouse-analyzer \
  /opt/greenhouse-analyzer/venv/bin/greenhouse-analyzer --check-config
sudo systemctl start greenhouse-analyzer.service
sudo journalctl -u greenhouse-analyzer.service -n 100 --no-pager
```

For a model run that prints the analysis without changing the site, run:

```sh
sudo systemctl stop greenhouse-analyzer.timer
sudo -u greenhouse-analyzer \
  /opt/greenhouse-analyzer/venv/bin/greenhouse-analyzer --dry-run
sudo systemctl start greenhouse-analyzer.timer
```

Open the loopback site on `homelab01` at `http://127.0.0.1:8081/`. To expose it
to authenticated devices on the tailnet without changing the bind address:

```sh
sudo tailscale serve --bg --yes 8081
sudo tailscale serve status
```

Alternatively, point an existing reverse proxy at `127.0.0.1:8081`. Binding
`GREENHOUSE_SITE_HOST=0.0.0.0` exposes the plain HTTP server directly and should
only be used on a trusted, firewalled network. Restart
`greenhouse-analysis-site.service` after changing listener settings.

Inspect the next scheduled run with:

```sh
systemctl list-timers greenhouse-analyzer.timer
```

Change the schedule with a systemd override rather than editing the packaged
unit:

```sh
sudo systemctl edit greenhouse-analyzer.timer
```

For example:

```ini
[Timer]
OnCalendar=
OnCalendar=*-*-* 07:30:00
RandomizedDelaySec=0
```

Then apply it with `sudo systemctl daemon-reload && sudo systemctl restart
greenhouse-analyzer.timer`.