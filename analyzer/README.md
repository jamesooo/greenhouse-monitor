# Greenhouse Analyzer

`greenhouse-analyzer` renders the previous 24 hours of the greenhouse Grafana
dashboard, downloads the latest full-resolution greenhouse capture, pairs both
images with an administrator-owned prompt, asks a Deep Agent backed by Ollama
for an analysis, and publishes the result as a dated Pelican article with both
images.

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

### Latest greenhouse image

The analyzer downloads `GREENHOUSE_IMAGE_URL` immediately after rendering the
dashboard. The endpoint must return the latest full-resolution capture as
`image/jpeg`. The deployed value is
`https://datastore.tail63be5a.ts.net/`, which exposes the consumer's loopback
latest-image API to authenticated tailnet clients.

The model receives the dashboard first and the full-resolution capture second,
with their roles stated in the text request. This lets it use dashboard charts
for environmental trends and the original capture for detailed visual plant
observations.

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

## Skills

Deep Agents loads administrator-managed skills from
`/etc/greenhouse-analyzer/skills`. Skills use progressive disclosure: the model
first sees each skill's name and description, then reads the full instructions
when the skill is relevant. The filesystem backend is rooted at that directory,
so agent file tools cannot traverse into the rest of the host filesystem. The
service account has read-only access to the skill tree.

The package includes an editable `greenhouse-interpretation` skill covering
basic chart and full-view evidence handling. Add greenhouse-specific chart
semantics, target ranges, plant identities, bed locations, and visual landmarks
to:

```text
/etc/greenhouse-analyzer/skills/greenhouse-interpretation/SKILL.md
```

Additional skills use one immediate subdirectory per skill:

```text
/etc/greenhouse-analyzer/skills/
└── plant-inventory/
  ├── SKILL.md
  └── reference-notes.md
```

Each `SKILL.md` requires YAML frontmatter whose `name` matches its directory:

```markdown
---
name: plant-inventory
description: Identify plants and locations visible in the greenhouse camera
---

# Plant Inventory

- The left foreground bed contains ...
- The hanging pot above the center aisle contains ...
```

Names use lowercase letters, digits, and hyphens. Descriptions should state
precisely when the model should load the skill. Supporting files can live in the
same skill directory and be referenced from `SKILL.md`. Run `--check-config`
after changes; it rejects unreadable skill roots, empty skill files, and skill
directories missing `SKILL.md`. Deep Agents logs and skips invalid frontmatter.
No service restart is needed because each daily invocation creates a new agent
and reloads skill metadata.

To use a different root, set `GREENHOUSE_ANALYSIS_SKILLS_DIRECTORY`. Keep that
directory readable but not writable by the `greenhouse-analyzer` service user.

## Configuration

The Debian conffiles are:

- `/etc/greenhouse-analyzer/greenhouse-analyzer.env` for service endpoints,
  credentials, model settings, render dimensions, site paths, and listener
  settings;
- `/etc/greenhouse-analyzer/pelicanconf.py` for Pelican URLs, feeds, and other
  site-generation settings;
- `/etc/greenhouse-analyzer/prompt.txt` for the daily analysis request.
- `/etc/greenhouse-analyzer/skills/greenhouse-interpretation/SKILL.md` for
  reusable greenhouse interpretation knowledge.

All four files are preserved across package upgrades. Administrator-created
skill directories are also left in place. Generated state lives
under `/var/lib/greenhouse-analyzer`:

- `content/greenhouse-analysis-YYYY-MM-DD.md` retains the source articles;
- `content/images/greenhouse-dashboard-YYYY-MM-DD.png` retains daily renders;
- `content/images/greenhouse-full-view-YYYY-MM-DD.jpg` retains the exact
  high-resolution capture analyzed that day;
- `output/` contains the rebuilt static site.

A repeated run on the same local date replaces that date's article and both
images instead of creating duplicates. This local dated copy is necessary
because the consumer endpoint always advances to the newest capture.
Model-produced HTML is escaped before Markdown rendering, while headings and
lists remain available. Back up `content/` to retain the report history;
`output/` is reproducible.

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
sudo apt install ./greenhouse-analyzer_1.0.0-6_all.deb
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