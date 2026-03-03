#!/bin/env python3
import sys
import json
import asyncio
import argparse
import time
from datetime import datetime
from struct import unpack_from

MQTT_AVAILABLE = False
try:
    import paho.mqtt.client as mqtt
    MQTT_AVAILABLE = True
except ImportError:
    pass

from bleak import BleakClient

TRIGGER_CHAR = "0000fff5-0000-1000-8000-00805f9b34fb"
NOTIFY_CHAR  = "0000fff3-0000-1000-8000-00805f9b34fb"

def decode(data):
    def safe_unpack(data, offset):
        try:
            return unpack_from("<h", data, offset)[0] / 16
        except Exception:
            return None
    main_temp = safe_unpack(data, 1)
    main_hum  = safe_unpack(data, 3)
    ext_temp  = safe_unpack(data, 7)
    ext_hum   = safe_unpack(data, 9)
    return main_temp, main_hum, ext_temp, ext_hum

def handler(address, mqttc, topic):
    def inner(sender, data):
        mt, mh, et, eh = decode(data)
        def v(x):
            return f"{x:.2f}" if x is not None else "---"
        reading = {
            "timestamp": datetime.now().isoformat(),
            "address": address,
            "main_temp": mt,
            "main_humidity": mh,
            "external_temp": et,
            "external_humidity": eh,
        }
        msg = (f"{reading['timestamp']} | {address} |"
               f" Main: {v(mt)}°C, {v(mh)}% |"
               f" External: {v(et)}°C, {v(eh)}%")
        if mqttc:
            try:
                mqttc.publish(topic, json.dumps(reading), retain=False)
            except Exception as e:
                print(f"MQTT publish error: {e}")
        else:
            print(msg)
    return inner

async def watch_device(address, poll_interval=60, mqttc=None, base_topic="ble_thermo"):
    topic = f"{base_topic}/{address.replace(':', '')}"
    while True:
        start_time = time.monotonic()
        success = False
        for attempt in [1, 2]:
            try:
                async with BleakClient(address) as client:
                    data_received = asyncio.Event()
                    def one_time_handler(sender, data):
                        handler(address, mqttc, topic)(sender, data)
                        data_received.set()
                    await client.start_notify(NOTIFY_CHAR, one_time_handler)
                    await client.write_gatt_char(TRIGGER_CHAR, b'\x0d')
                    try:
                        await asyncio.wait_for(data_received.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        print(f"Timeout waiting for response from {address}")
                    await client.stop_notify(NOTIFY_CHAR)
                success = True
                break  # If connect + poll worked, exit retry loop
            except Exception as e:
                print(f"ERROR with device {address} (attempt {attempt}): {e}")
                if attempt == 1:
                    await asyncio.sleep(3)  # Wait before retry (customize if needed)
        elapsed = time.monotonic() - start_time
        sleep_time = max(0, poll_interval - elapsed)
        await asyncio.sleep(sleep_time)

async def main(addresses, poll_interval=60, mqttc=None, base_topic="ble_thermo"):
    tasks = [watch_device(addr, poll_interval, mqttc, base_topic) for addr in addresses]
    await asyncio.gather(*tasks)

if __name__ == '__main__':
    import signal

    parser = argparse.ArgumentParser(
        description="Monitor multiple BLE climate sensors, print and optionally publish as JSON to MQTT."
    )
    parser.add_argument("addresses", help="Comma-separated list of BLE MAC addresses.")
    parser.add_argument("--interval", type=int, default=60, help="Polling interval in seconds (default: 60)")
    # MQTT arguments
    parser.add_argument("--mqtt-host", help="MQTT broker address")
    parser.add_argument("--mqtt-port", type=int, default=1883, help="MQTT broker port (default: 1883)")
    parser.add_argument("--mqtt-user", help="MQTT username (optional)")
    parser.add_argument("--mqtt-pass", help="MQTT password (optional)")
    parser.add_argument("--mqtt-base-topic", default="ble_thermo", help="MQTT base topic prefix (default: ble_thermo/...)")
    args = parser.parse_args()

    seen = set()
    address_list = []
    for addr in (s.strip() for s in args.addresses.split(",") if s.strip()):
        # Use upper() if you want A-F consistently uppercase, or lower() for lowercase
        norm = addr.upper()
        if norm not in seen:
            seen.add(norm)
            address_list.append(addr)
    if not address_list:
        sys.exit("No valid addresses provided.")

    mqttc = None

    if args.mqtt_host:
        if not MQTT_AVAILABLE:
            print("ERROR: paho-mqtt is not installed. Install with: sudo apt install python3-paho-mqtt")
            sys.exit(1)
        mqttc = mqtt.Client()
        if args.mqtt_user:
            mqttc.username_pw_set(args.mqtt_user, args.mqtt_pass)
        try:
            mqttc.connect(args.mqtt_host, args.mqtt_port)
            mqttc.loop_start()
            print(f"Connected to MQTT broker at {args.mqtt_host}:{args.mqtt_port}")
        except Exception as e:
            print(f"Could not connect to MQTT: {e}")
            mqttc = None

    async def run_with_cancel():
        main_task = asyncio.create_task(main(address_list, args.interval, mqttc, args.mqtt_base_topic))
        for sig in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_event_loop().add_signal_handler(sig, main_task.cancel)
        try:
            await main_task
        except asyncio.CancelledError:
            print("Stopping BLE monitor tasks gracefully...")

    try:
        asyncio.run(run_with_cancel())
    except KeyboardInterrupt:
        print("Stopping BLE monitor. Goodbye!")

    if mqttc:
        mqttc.loop_stop()
        mqttc.disconnect()
