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