# Debian package

Build the package from the repository root:

```bash
bash packaging/build-deb.sh
```

The version comes from `greenhouse/VERSION`, and the Debian revision defaults to `6`. Both can be overridden:

```bash
bash packaging/build-deb.sh 1.5.1 2
```

Packages are written to `dist/`. The builder requires `bash` and `dpkg-deb`; it does not require root.

Install or upgrade on the Raspberry Pi with:

```bash
sudo apt install ./greenhouse-monitor_1.5.0-7_all.deb
```

The package preserves `/etc/greenhouse/greenhouse.env` during upgrades. Its post-install script recreates `/opt/greenhouse/venv`, installs the Python dependencies, enables the systemd unit, and restarts the service. Network access is therefore required during package configuration.

The GitHub Actions workflow in `.github/workflows/build-deb.yml` runs the same builder for version tags and manual workflow dispatches, then publishes the `.deb` as a workflow artifact.

## Additional Services

The datastore consumer and dashboard analyzer have independent versions and
builders:

```bash
bash packaging/consumer/build-deb.sh
bash packaging/analyzer/build-deb.sh
```

The analyzer builder accepts optional version and Debian revision arguments:

```bash
bash packaging/analyzer/build-deb.sh 1.0.1 2
```

It writes `dist/greenhouse-analyzer_1.0.1-2_all.deb`. See
`analyzer/README.md` for Grafana rendering, Ollama, Pelican, static-site serving,
and systemd timer requirements.
