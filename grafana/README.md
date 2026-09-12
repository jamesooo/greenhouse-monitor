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