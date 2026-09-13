# Greenhouse Monitor

This repository contains the services used to monitor and review the greenhouse:

- `greenhouse-monitor` captures camera images and publishes climate and light
	readings to MQTT;
- `greenhouse-datastore` stores those readings in TimescaleDB and serves the
	latest camera image;
- the provisioned Grafana dashboard combines the image and environmental
	history; and
- `greenhouse-analyzer` renders that dashboard, analyzes it with Deep Agents
	and a configurable Ollama vision model, and publishes a daily Pelican site.

See `greenhouse/greenhouse.env` for producer configuration,
`consumer/README.md` for datastore setup, `grafana/README.md` for dashboard
provisioning, and `analyzer/README.md` for the analyzer deployment runbook.

## Debian Package

Build an installable package without root access:

```bash
bash packaging/build-deb.sh
```

The package is written to `dist/`. Install it, or upgrade an existing `greenhouse-monitor` package, with:

```bash
sudo apt install ./dist/greenhouse-monitor_1.5.0-7_all.deb
```

The existing `/etc/greenhouse/greenhouse.env` is preserved during upgrades. Package configuration needs network access to install Python dependencies into `/opt/greenhouse/venv`.

See `packaging/README.md` for version overrides and CI details.
