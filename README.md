# Greenhouse Monitor

This program watches a Bluetooth thermometer and optical camera inside a greenhouse, saving images and publishing metrics to MQTT on configured intervals. See `greenhouse/greenhouse.env` for configuration.

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
