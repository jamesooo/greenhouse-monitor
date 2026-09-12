# Greenhouse Datastore

`greenhouse-datastore` subscribes to the producer's climate and light MQTT
topics and stores readings in PostgreSQL TimescaleDB hypertables. It also
serves the newest optical capture as a JPEG from `GET /`.

## Responsibilities

The service assumes that an administrator has already:

- installed and configured PostgreSQL and TimescaleDB;
- created the database and login named by `GREENHOUSE_DATABASE_DSN`;
- enabled the `timescaledb` extension in that database; and
- granted the login permission to create tables in its target schema.

The service does not configure PostgreSQL, move its data directory, install
TimescaleDB, create database roles, or configure Grafana. At startup it applies
its idempotent packaged schema and verifies that TimescaleDB is enabled.

## Tables

- `climate_readings` is a hypertable partitioned on `observed_at`, with one row
  per sensor timestamp and address.
- `light_readings` is a hypertable partitioned on `observed_at`, with one row
  per capture timestamp.
- `climate_readings_hourly` contains hourly count, average, minimum, and maximum
  climate values per sensor.
- `light_readings_hourly` contains hourly count, average, minimum, and maximum
  light metrics.

Both tables retain the MQTT topic and an independent `received_at` timestamp.
The primary keys make redelivery of the same MQTT message harmless.

TimescaleDB refreshes complete hourly windows after `GREENHOUSE_COMPACT_AFTER`.
It deletes raw rows after `GREENHOUSE_RAW_RETENTION`, then deletes hourly rows
after `GREENHOUSE_HOURLY_RETENTION`. The configured intervals must satisfy:

```text
GREENHOUSE_COMPACT_AFTER < GREENHOUSE_RAW_RETENTION < GREENHOUSE_HOURLY_RETENTION
```

## Configuration

The Debian conffile is
`/etc/greenhouse-datastore/greenhouse-datastore.env`. Set the MQTT connection,
database DSN, base topic, and the IANA time zone used to interpret legacy
producer timestamps that do not contain an offset. The same file controls the
three compaction and retention intervals described above using PostgreSQL
interval syntax.

The latest-image API is configured with:

- `GREENHOUSE_IMAGE_API_HOST` (default `127.0.0.1`);
- `GREENHOUSE_IMAGE_API_PORT` (packaged default `8080`); and
- `GREENHOUSE_IMAGE_DIRECTORY` (default
  `/mnt/datastore/greenhouse-captures`).

Only `GET /` is exposed. It returns the newest `optical_*.jpg` as `image/jpeg`,
or `404` when no capture is available. The deployed loopback listener is
published to authenticated tailnet clients with Tailscale Serve.

## Debian Package

Build from the repository root:

```sh
bash packaging/consumer/build-deb.sh
```

Install with APT so operating-system dependencies are resolved:

```sh
sudo apt install ./dist/greenhouse-datastore_1.0.0-4_all.deb
```

The package installs and enables `greenhouse-datastore.service`. Python
dependencies are downloaded into `/opt/greenhouse-datastore/venv` while the
package is configured.