# Grafana Provisioning

These files provision the local `greenhouse` PostgreSQL database and the
`Greenhouse Climate` dashboard. Replace
`__GREENHOUSE_GRAFANA_DB_PASSWORD__` in the datasource template during
deployment; do not commit the generated password.

Grafana persistent state on `datastore` is stored under
`/mnt/datastore/grafana`:

- `data` contains Grafana's SQLite database and generated state;
- `plugins` contains installed plugins; and
- `logs` contains file logs.

The dashboard uses the real-time `climate_readings_hourly` and
`light_readings_hourly` continuous aggregates, so recent raw readings are
visible before their one-hour windows are materialized.

The `Latest Greenhouse Image` Text panel renders an HTML `<img>` whose source
is the consumer's latest-image API. Its hidden `image_api_url` dashboard
constant is `https://datastore.tail63be5a.ts.net/`, backed by a Tailscale Serve
proxy to the consumer's loopback listener. Change that value in
`dashboards/climate.json` if clients use a different Tailscale DNS name. The
dashboard refresh timestamp is appended to the URL so each Grafana refresh
fetches the current image. Infinity is not required because the browser loads
the JPEG directly. The dashboard preview is capped at 480 pixels high to match
the former capture size, with a link below it that opens the full-resolution
image directly from the API host.

The node-scoped proxy was configured with:

```sh
sudo tailscale serve --bg --yes 8080
```

The `--bg` configuration is retained by `tailscaled` across reboots, service
restarts, and `tailscale down`/`tailscale up` cycles. Check or reapply it with:

```sh
sudo tailscale serve status
sudo tailscale serve --bg --yes 8080
```

Keep `tailscaled.service` enabled and do not delete
`/var/lib/tailscale/tailscaled.state`. For an unattended server, verify on the
Tailscale Machines page that key expiry is disabled. Tagged devices normally
have key expiry disabled automatically.

If the Text panel does not retain the image markup, enable
`disable_sanitize_html = true` in Grafana's `[panels]` configuration and
restart Grafana. Only do this for dashboards editable by trusted users.

## Image Renderer

The daily analyzer requires Grafana's supported standalone Image Renderer.
`datastore` runs the Linux ARM64 renderer binary as
`grafana-image-renderer.service`, bound only to `127.0.0.1:8081`. Chromium is
provided by Debian's `chromium` package. The deprecated in-process Grafana
renderer plugin is not used.

The deployment uses these host files:

- `/usr/local/bin/grafana-image-renderer` contains the renderer binary;
- `/etc/systemd/system/grafana-image-renderer.service` runs the renderer as the
	unprivileged `grafana-image-renderer` user;
- `/etc/grafana/renderer.env` stores the shared renderer token and Grafana
	rendering URLs with mode `0600`;
- `/etc/systemd/system/grafana-server.service.d/renderer.conf` loads that
	environment file and orders Grafana after the renderer.

The configured endpoints are:

```text
GF_RENDERING_SERVER_URL=http://127.0.0.1:8081/render
GF_RENDERING_CALLBACK_URL=http://127.0.0.1:3000/
```

Check the renderer and Grafana after upgrades with:

```sh
sudo systemctl is-active grafana-image-renderer.service grafana-server.service
sudo journalctl -u grafana-image-renderer.service -n 50 --no-pager
sudo journalctl -u grafana-server.service -n 50 --no-pager
```

An HTTP 500 from Grafana's `/render` endpoint accompanied by `rendering plugin
not available` in the Grafana journal means the standalone renderer is absent
or unavailable. The renderer health endpoint is `/healthz` and requires the
`X-Auth-Token` value stored in `/etc/grafana/renderer.env`.

Grafana recommends 16 GiB RAM and four CPU cores for general renderer workloads.
`datastore` has less memory, so keep rendering concurrency low and monitor it
if the schedule or dashboard size increases. The current single daily
1600-by-1200 render completes in about five seconds.