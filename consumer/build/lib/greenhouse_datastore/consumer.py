from __future__ import annotations

import json
import logging
import os
import signal
from datetime import datetime
from importlib.resources import files
from numbers import Real
from typing import Any
from zoneinfo import ZoneInfo

import paho.mqtt.client as mqtt
import psycopg
from psycopg import sql


logger = logging.getLogger("greenhouse-datastore")


CLIMATE_COLUMNS = (
    "main_temp",
    "main_humidity",
    "external_temp",
    "external_humidity",
)
LIGHT_COLUMNS = (
    "mean",
    "normalized_mean",
    "median",
    "std",
    "bright_pixel_ratio",
    "dark_pixel_ratio",
)


class ReadingStore:
    def __init__(self, dsn: str, source_timezone: str):
        self.dsn = dsn
        self.source_timezone = ZoneInfo(source_timezone)
        self.connection: psycopg.Connection[Any] | None = None

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def provision_schema(self) -> None:
        schema = files("greenhouse_datastore").joinpath("schema.sql").read_text()
        connection = self._connect()
        try:
            connection.execute(schema)
        except psycopg.Error:
            self.close()
            raise

    def store(self, topic: str, payload: bytes) -> None:
        reading = json.loads(payload)
        if not isinstance(reading, dict):
            raise ValueError("payload must be a JSON object")

        if topic.endswith("/light"):
            self._store_light(topic, reading)
        elif "/climate/" in topic:
            self._store_climate(topic, reading)
        else:
            raise ValueError(f"unsupported topic: {topic}")

    def _connect(self) -> psycopg.Connection[Any]:
        if self.connection is None or self.connection.closed:
            self.connection = psycopg.connect(self.dsn, autocommit=True)
        return self.connection

    def _execute(self, statement: sql.SQL, values: tuple[Any, ...]) -> None:
        try:
            self._connect().execute(statement, values)
        except psycopg.OperationalError:
            self.close()
            self._connect().execute(statement, values)

    def _timestamp(self, value: Any) -> datetime:
        if not isinstance(value, str):
            raise ValueError("timestamp must be an ISO 8601 string")
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=self.source_timezone)
        return timestamp

    @staticmethod
    def _number(reading: dict[str, Any], key: str, nullable: bool) -> float | None:
        value = reading.get(key)
        if value is None and nullable:
            return None
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError(f"{key} must be numeric")
        return float(value)

    def _store_climate(self, topic: str, reading: dict[str, Any]) -> None:
        address = reading.get("address")
        if not isinstance(address, str) or not address:
            raise ValueError("address must be a non-empty string")
        values = tuple(self._number(reading, key, True) for key in CLIMATE_COLUMNS)
        self._execute(
            sql.SQL(
                """
                INSERT INTO climate_readings (
                    observed_at, sensor_address, main_temp, main_humidity,
                    external_temp, external_humidity, mqtt_topic
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (observed_at, sensor_address) DO NOTHING
                """
            ),
            (self._timestamp(reading.get("timestamp")), address, *values, topic),
        )

    def _store_light(self, topic: str, reading: dict[str, Any]) -> None:
        values = tuple(self._number(reading, key, False) for key in LIGHT_COLUMNS)
        self._execute(
            sql.SQL(
                """
                INSERT INTO light_readings (
                    observed_at, mean, normalized_mean, median, std,
                    bright_pixel_ratio, dark_pixel_ratio, mqtt_topic
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (observed_at) DO NOTHING
                """
            ),
            (self._timestamp(reading.get("timestamp")), *values, topic),
        )


class GreenhouseConsumer:
    def __init__(self) -> None:
        host = os.environ.get("GREENHOUSE_MQTT_HOST", "192.168.8.132")
        port = int(os.environ.get("GREENHOUSE_MQTT_PORT", "1883"))
        base_topic = os.environ.get("GREENHOUSE_MQTT_BASE_TOPIC", "greenhouse")
        source_timezone = os.environ.get("GREENHOUSE_SOURCE_TIMEZONE", "UTC")
        dsn = os.environ.get(
            "GREENHOUSE_DATABASE_DSN",
            "dbname=greenhouse user=greenhouse host=/var/run/postgresql",
        )

        self.host = host
        self.port = port
        self.topics = (f"{base_topic}/climate/+", f"{base_topic}/light")
        self.store = ReadingStore(dsn, source_timezone)
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id="greenhouse-datastore",
            protocol=mqtt.MQTTv311,
        )
        username = os.environ.get("GREENHOUSE_MQTT_USER")
        if username:
            self.client.username_pw_set(
                username,
                os.environ.get("GREENHOUSE_MQTT_PASS"),
            )
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        if reason_code.is_failure:
            logger.error("MQTT connection failed: %s", reason_code)
            return
        for topic in self.topics:
            client.subscribe(topic, qos=1)
        logger.info("Subscribed to %s", ", ".join(self.topics))

    def _on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        message: mqtt.MQTTMessage,
    ) -> None:
        try:
            self.store.store(message.topic, message.payload)
            logger.debug("Stored reading from %s", message.topic)
        except (ValueError, json.JSONDecodeError, psycopg.Error) as error:
            logger.error("Rejected message from %s: %s", message.topic, error)

    def run(self) -> None:
        self.store.provision_schema()
        logger.info("Database schema is ready")
        self.client.connect(self.host, self.port, keepalive=60)
        try:
            self.client.loop_forever(retry_first_connection=True)
        finally:
            self.store.close()

    def stop(self, signum: int, frame: Any) -> None:
        self.client.disconnect()


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("GREENHOUSE_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    consumer = GreenhouseConsumer()
    signal.signal(signal.SIGTERM, consumer.stop)
    signal.signal(signal.SIGINT, consumer.stop)
    consumer.run()


if __name__ == "__main__":
    main()