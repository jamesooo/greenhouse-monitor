DO $$
BEGIN
    IF NOT EXISTS (
        SELECT FROM pg_extension WHERE extname = 'timescaledb'
    ) THEN
        RAISE EXCEPTION 'TimescaleDB extension is not enabled in this database';
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS climate_readings (
    observed_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    sensor_address text NOT NULL,
    main_temp double precision,
    main_humidity double precision,
    external_temp double precision,
    external_humidity double precision,
    mqtt_topic text NOT NULL,
    PRIMARY KEY (observed_at, sensor_address)
);

SELECT create_hypertable(
    'climate_readings',
    'observed_at',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS climate_readings_sensor_time_idx
    ON climate_readings (sensor_address, observed_at DESC);

CREATE TABLE IF NOT EXISTS light_readings (
    observed_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    mean double precision NOT NULL,
    normalized_mean double precision NOT NULL,
    median double precision NOT NULL,
    std double precision NOT NULL,
    bright_pixel_ratio double precision NOT NULL,
    dark_pixel_ratio double precision NOT NULL,
    mqtt_topic text NOT NULL,
    PRIMARY KEY (observed_at)
);

SELECT create_hypertable(
    'light_readings',
    'observed_at',
    if_not_exists => TRUE
);

CREATE MATERIALIZED VIEW IF NOT EXISTS climate_readings_hourly
WITH (timescaledb.continuous) AS
SELECT
    time_bucket(INTERVAL '1 hour', observed_at) AS bucket,
    sensor_address,
    count(*) AS sample_count,
    avg(main_temp) AS main_temp_avg,
    min(main_temp) AS main_temp_min,
    max(main_temp) AS main_temp_max,
    avg(main_humidity) AS main_humidity_avg,
    min(main_humidity) AS main_humidity_min,
    max(main_humidity) AS main_humidity_max,
    avg(external_temp) AS external_temp_avg,
    min(external_temp) AS external_temp_min,
    max(external_temp) AS external_temp_max,
    avg(external_humidity) AS external_humidity_avg,
    min(external_humidity) AS external_humidity_min,
    max(external_humidity) AS external_humidity_max
FROM climate_readings
GROUP BY bucket, sensor_address
WITH NO DATA;

CREATE MATERIALIZED VIEW IF NOT EXISTS light_readings_hourly
WITH (timescaledb.continuous) AS
SELECT
    time_bucket(INTERVAL '1 hour', observed_at) AS bucket,
    count(*) AS sample_count,
    avg(mean) AS mean_avg,
    min(mean) AS mean_min,
    max(mean) AS mean_max,
    avg(normalized_mean) AS normalized_mean_avg,
    min(normalized_mean) AS normalized_mean_min,
    max(normalized_mean) AS normalized_mean_max,
    avg(median) AS median_avg,
    min(median) AS median_min,
    max(median) AS median_max,
    avg(std) AS std_avg,
    min(std) AS std_min,
    max(std) AS std_max,
    avg(bright_pixel_ratio) AS bright_pixel_ratio_avg,
    min(bright_pixel_ratio) AS bright_pixel_ratio_min,
    max(bright_pixel_ratio) AS bright_pixel_ratio_max,
    avg(dark_pixel_ratio) AS dark_pixel_ratio_avg,
    min(dark_pixel_ratio) AS dark_pixel_ratio_min,
    max(dark_pixel_ratio) AS dark_pixel_ratio_max
FROM light_readings
GROUP BY bucket
WITH NO DATA;

ALTER MATERIALIZED VIEW climate_readings_hourly
    SET (timescaledb.materialized_only = false);
ALTER MATERIALIZED VIEW light_readings_hourly
    SET (timescaledb.materialized_only = false);