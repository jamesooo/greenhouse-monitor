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
the JPEG directly.

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