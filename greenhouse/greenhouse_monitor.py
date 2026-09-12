#!/usr/bin/env python3
"""
Unified Greenhouse Monitoring Script for Raspberry Pi Zero 2 W

Combines:
- BLE climate sensor monitoring (internal/external temp & humidity)
- Optical camera capture with light level measurement

Optimizations for Pi Zero 2 W:
- asyncio + ThreadPoolExecutor for efficient concurrency
- USB camera bound only during capture
- Memory-efficient image processing (process then discard)
- Single MQTT client for all data streams

MQTT Topics:
- {base_topic}/climate/{address}  - BLE sensor data (temp/humidity)
- {base_topic}/light              - Light metrics from optical camera

Usage:
  python greenhouse_monitor.py --ble-addresses AA:BB:CC:DD:EE:FF,11:22:33:44:55:66 \
      --mqtt-host 192.168.1.100 --output-dir /home/pi/captures

Dependencies:
    pip install bleak paho-mqtt opencv-python numpy
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import json
import logging
import os
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from struct import unpack_from
from typing import Optional, Tuple, List

import cv2 as cv
import numpy as np

# Optional imports with graceful fallback
MQTT_AVAILABLE = False
try:
    import paho.mqtt.client as mqtt
    MQTT_AVAILABLE = True
except ImportError:
    pass

BLE_AVAILABLE = False
try:
    from bleak import BleakClient
    BLE_AVAILABLE = True
except ImportError:
    pass

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

# BLE Characteristics for climate sensors
BLE_TRIGGER_CHAR = "0000fff5-0000-1000-8000-00805f9b34fb"
BLE_NOTIFY_CHAR = "0000fff3-0000-1000-8000-00805f9b34fb"

# USB device ID - find this using 'lsusb -t'
# This should be configured for your specific setup
DEFAULT_OPTICAL_USB_ID = "1-1.1.4"

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("greenhouse")

# Force unbuffered print for systemd/journald
print = functools.partial(print, flush=True)


# -----------------------------------------------------------------------------
# Data Classes
# -----------------------------------------------------------------------------

@dataclass
class ClimateReading:
    """BLE climate sensor reading"""
    timestamp: str
    address: str
    main_temp: Optional[float]
    main_humidity: Optional[float]
    external_temp: Optional[float]
    external_humidity: Optional[float]


@dataclass
class LightMetrics:
    """Light metrics from optical camera"""
    timestamp: str
    mean: float              # 0..255
    normalized_mean: float   # 0..1
    median: float
    std: float
    bright_pixel_ratio: float
    dark_pixel_ratio: float


# -----------------------------------------------------------------------------
# Circuit Breaker for Fault Isolation
# -----------------------------------------------------------------------------

class CircuitBreaker:
    """
    Circuit breaker pattern to prevent repeatedly hammering a failing component.
    
    States:
    - CLOSED: Normal operation, requests go through
    - OPEN: Component failing, requests are rejected immediately
    - HALF_OPEN: Testing if component recovered
    """
    
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"
    
    def __init__(self, name: str, failure_threshold: int = 3,
                 recovery_timeout: float = 300, half_open_successes: int = 1):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_successes = half_open_successes
        
        self._state = self.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: Optional[float] = None
        self._lock = asyncio.Lock()
    
    @property
    def state(self) -> str:
        return self._state
    
    @property
    def is_available(self) -> bool:
        """Check if requests should be allowed through"""
        if self._state == self.CLOSED:
            return True
        if self._state == self.OPEN:
            # Check if recovery timeout has elapsed
            if self._last_failure_time and \
               (time.monotonic() - self._last_failure_time) >= self.recovery_timeout:
                return True  # Allow half-open attempt
            return False
        # HALF_OPEN - allow through for testing
        return True
    
    async def record_success(self):
        """Record a successful operation"""
        async with self._lock:
            if self._state == self.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.half_open_successes:
                    logger.info(f"Circuit breaker [{self.name}]: CLOSED (recovered)")
                    self._state = self.CLOSED
                    self._failure_count = 0
                    self._success_count = 0
            elif self._state == self.CLOSED:
                # Reset failure count on success
                self._failure_count = 0
    
    async def record_failure(self):
        """Record a failed operation"""
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            
            if self._state == self.HALF_OPEN:
                # Failed during recovery test, reopen
                logger.warning(f"Circuit breaker [{self.name}]: OPEN (recovery failed)")
                self._state = self.OPEN
                self._success_count = 0
            elif self._state == self.CLOSED:
                if self._failure_count >= self.failure_threshold:
                    logger.warning(
                        f"Circuit breaker [{self.name}]: OPEN "
                        f"(failed {self._failure_count} times)"
                    )
                    self._state = self.OPEN
    
    async def attempt(self) -> bool:
        """
        Check if an attempt should be made.
        Returns True if allowed, False if circuit is open.
        Transitions OPEN -> HALF_OPEN when recovery timeout elapsed.
        """
        async with self._lock:
            if self._state == self.CLOSED:
                return True
            
            if self._state == self.OPEN:
                if self._last_failure_time and \
                   (time.monotonic() - self._last_failure_time) >= self.recovery_timeout:
                    logger.info(f"Circuit breaker [{self.name}]: HALF_OPEN (testing recovery)")
                    self._state = self.HALF_OPEN
                    self._success_count = 0
                    return True
                return False
            
            # HALF_OPEN
            return True


# -----------------------------------------------------------------------------
# USB Device Management
# -----------------------------------------------------------------------------

class USBDeviceManager:
    """Manages optical camera USB binding during capture."""
    
    def __init__(self, optical_usb_id: str):
        self.optical_usb_id = optical_usb_id
        self._lock = asyncio.Lock()  # For bind/unbind operations
        self._lock_timeout = 60  # Max seconds to wait for lock
    
    def _is_device_bound(self, usb_id: str) -> bool:
        """Check if a USB device is currently bound to a driver"""
        return os.path.exists(f"/sys/bus/usb/devices/{usb_id}/driver")
    
    def _device_exists(self, usb_id: str) -> bool:
        """Check if USB device exists in sysfs"""
        return os.path.exists(f"/sys/bus/usb/devices/{usb_id}")
    
    def _set_device_state(self, usb_id: str, state: str) -> bool:
        """
        Bind or unbind a USB device.
        state: 'bind' or 'unbind'
        """
        if not self._device_exists(usb_id):
            logger.warning(f"Device {usb_id} not found in /sys/bus/usb/devices/")
            return False
        
        currently_bound = self._is_device_bound(usb_id)
        
        # Skip if already in desired state
        if state == "unbind" and not currently_bound:
            logger.debug(f"Device {usb_id} already unbound")
            return True
        if state == "bind" and currently_bound:
            logger.debug(f"Device {usb_id} already bound")
            return True
        
        path = f"/sys/bus/usb/drivers/usb/{state}"
        try:
            result = subprocess.run(
                f"echo '{usb_id}' | sudo tee {path}",
                shell=True, check=True, capture_output=True, text=True
            )
            time.sleep(0.5)  # Brief pause for kernel
            logger.info(f"Successfully {state} device {usb_id}")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Error {state}ing {usb_id}: {e.stderr.strip()}")
            return False
    
    @asynccontextmanager
    async def exclusive_optical(self):
        """Bind the optical camera for the duration of a capture."""
        try:
            async with asyncio.timeout(self._lock_timeout):
                async with self._lock:
                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(
                        None, self._set_device_state, self.optical_usb_id, "bind"
                    )
                    if not result:
                        raise RuntimeError("Failed to bind optical camera")
            
            # Allow device to initialize
            await asyncio.sleep(2)
            
            yield True  # Camera is ready
            
        finally:
            try:
                async with asyncio.timeout(self._lock_timeout):
                    async with self._lock:
                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(
                            None, self._set_device_state, self.optical_usb_id, "unbind"
                        )
            except asyncio.TimeoutError:
                logger.error("Timeout waiting for USB lock while releasing optical camera")
    
    async def release_all(self):
        """Unbind the optical camera to save power/bandwidth."""
        try:
            async with asyncio.timeout(self._lock_timeout):
                async with self._lock:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(
                        None, self._set_device_state, self.optical_usb_id, "unbind"
                    )
        except asyncio.TimeoutError:
            logger.error("Timeout waiting for USB lock (release_all)")
            # Force release without lock as last resort
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, self._set_device_state, self.optical_usb_id, "unbind"
            )


# -----------------------------------------------------------------------------
# MQTT Publisher
# -----------------------------------------------------------------------------

class MQTTPublisher:
    """Thread-safe MQTT publisher with automatic reconnection"""
    
    def __init__(self, host: str, port: int = 1883,
                 username: Optional[str] = None, password: Optional[str] = None,
                 base_topic: str = "greenhouse"):
        self.host = host
        self.port = port
        self.base_topic = base_topic
        self.client: Optional[mqtt.Client] = None
        self._connected = False
        
        if not MQTT_AVAILABLE:
            logger.warning("paho-mqtt not installed, MQTT publishing disabled")
            return
        
        self.client = mqtt.Client()
        if username:
            self.client.username_pw_set(username, password)
        
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
    
    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self._connected = True
            logger.info(f"Connected to MQTT broker at {self.host}:{self.port}")
        else:
            logger.error(f"MQTT connection failed with code {rc}")
    
    def _on_disconnect(self, client, userdata, rc):
        self._connected = False
        logger.warning("Disconnected from MQTT broker")
    
    def connect(self) -> bool:
        if not self.client:
            return False
        try:
            self.client.connect(self.host, self.port)
            self.client.loop_start()
            # Wait briefly for connection
            time.sleep(1)
            return self._connected
        except Exception as e:
            logger.error(f"MQTT connection error: {e}")
            return False
    
    def disconnect(self):
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
    
    def _safe_publish(self, topic: str, payload: str, context: str):
        """
        Safely publish a message, catching all exceptions.
        This ensures MQTT failures never propagate to callers.
        """
        if not self.client:
            return False
        if not self._connected:
            logger.debug(f"MQTT not connected, skipping publish to {topic}")
            return False
        try:
            result = self.client.publish(topic, payload, retain=False)
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                logger.debug(f"Published {context} to {topic}")
                return True
            else:
                logger.warning(f"MQTT publish returned {result.rc} for {topic}")
                return False
        except Exception as e:
            logger.error(f"MQTT publish error ({context}): {e}")
            return False
    
    def publish_climate(self, reading: ClimateReading):
        """Publish climate data to climate/{address} topic"""
        topic = f"{self.base_topic}/climate/{reading.address.replace(':', '')}"
        self._safe_publish(topic, json.dumps(asdict(reading)), "climate")
    
    def publish_light(self, metrics: LightMetrics):
        """Publish light metrics to light topic"""
        topic = f"{self.base_topic}/light"
        self._safe_publish(topic, json.dumps(asdict(metrics)), "light")


# -----------------------------------------------------------------------------
# BLE Climate Sensor
# -----------------------------------------------------------------------------

def decode_ble_climate(data: bytes) -> Tuple[Optional[float], ...]:
    """Decode BLE climate sensor data"""
    def safe_unpack(data, offset):
        try:
            return unpack_from("<h", data, offset)[0] / 16
        except Exception:
            return None
    main_temp = safe_unpack(data, 1)
    main_hum = safe_unpack(data, 3)
    ext_temp = safe_unpack(data, 7)
    ext_hum = safe_unpack(data, 9)
    return main_temp, main_hum, ext_temp, ext_hum


async def poll_ble_sensor(address: str, timeout: float = 5.0) -> Optional[ClimateReading]:
    """Poll a single BLE climate sensor"""
    if not BLE_AVAILABLE:
        logger.warning("bleak not installed, BLE polling disabled")
        return None
    
    for attempt in [1, 2]:
        try:
            async with BleakClient(address) as client:
                data_event = asyncio.Event()
                reading_data = {}
                
                def notification_handler(sender, data):
                    mt, mh, et, eh = decode_ble_climate(data)
                    reading_data['values'] = (mt, mh, et, eh)
                    data_event.set()
                
                await client.start_notify(BLE_NOTIFY_CHAR, notification_handler)
                await client.write_gatt_char(BLE_TRIGGER_CHAR, b'\x0d')
                
                try:
                    await asyncio.wait_for(data_event.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    logger.warning(f"Timeout waiting for BLE response from {address}")
                    await client.stop_notify(BLE_NOTIFY_CHAR)
                    continue
                
                await client.stop_notify(BLE_NOTIFY_CHAR)
                
                mt, mh, et, eh = reading_data['values']
                return ClimateReading(
                    timestamp=datetime.now().isoformat(),
                    address=address,
                    main_temp=mt,
                    main_humidity=mh,
                    external_temp=et,
                    external_humidity=eh
                )
        except Exception as e:
            logger.error(f"BLE error with {address} (attempt {attempt}): {e}")
            if attempt == 1:
                await asyncio.sleep(2)
    
    return None


# -----------------------------------------------------------------------------
# Optical Camera & Light Measurement
# -----------------------------------------------------------------------------

def capture_optical_with_light_metrics(
    output_dir: str,
    bright_thresh: int = 220,
    dark_thresh: int = 30,
    save_image: bool = True
) -> Tuple[Optional[str], Optional[LightMetrics]]:
    """
    Capture optical image and compute light metrics.
    Returns (image_path, metrics) or (None, None) on failure.
    
    Args:
        output_dir: Directory to save image
        bright_thresh: Threshold for bright pixel ratio
        dark_thresh: Threshold for dark pixel ratio
        save_image: If False, only compute metrics without saving image
    
    Memory-optimized: processes image in-place, releases resources immediately.
    """
    cap = None
    try:
        # Use V4L2 backend for Linux
        cap = cv.VideoCapture(0, cv.CAP_V4L2)
        if not cap.isOpened():
            logger.error("Could not open optical camera")
            return None, None
        
        # Low resolution for Pi Zero 2 W memory efficiency
        cap.set(cv.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv.CAP_PROP_BUFFERSIZE, 1)
        
        # Flush buffer (exposure/white balance adjustment)
        for _ in range(5):
            cap.read()
            time.sleep(0.1)
        
        ret, frame = cap.read()
        if not ret or frame is None:
            logger.error("Failed to read frame from optical camera")
            return None, None
        
        # Compute light metrics from grayscale
        gray = cv.cvtColor(frame, cv.COLOR_BGR2GRAY)
        
        mean_val = float(np.mean(gray))
        metrics = LightMetrics(
            timestamp=datetime.now().isoformat(),
            mean=mean_val,
            normalized_mean=mean_val / 255.0,
            median=float(np.median(gray)),
            std=float(np.std(gray)),
            bright_pixel_ratio=float(np.mean(gray >= bright_thresh)),
            dark_pixel_ratio=float(np.mean(gray <= dark_thresh))
        )
        
        # Release grayscale to free memory
        del gray
        
        # Save image if requested
        filename = None
        if save_image:
            filename = os.path.join(output_dir, f"optical_{int(time.time())}.jpg")
            if cv.imwrite(filename, frame):
                logger.info(f"Saved optical image: {filename}")
            else:
                logger.error(f"Failed to write optical image: {filename}")
                filename = None
        
        return filename, metrics
    
    except Exception as e:
        logger.error(f"Optical capture error: {e}")
        return None, None
    
    finally:
        if cap is not None:
            cap.release()


# -----------------------------------------------------------------------------
# Main Monitoring Loop
# -----------------------------------------------------------------------------

class GreenhouseMonitor:
    """
    Main monitoring coordinator.
    Uses asyncio for BLE and coordination, ThreadPoolExecutor for camera I/O.
    
    Each component (BLE and optical camera) runs independently
    with its own error handling and circuit breaker for fault isolation.
    """
    
    def __init__(
        self,
        ble_addresses: List[str],
        mqtt_publisher: Optional[MQTTPublisher],
        usb_manager: USBDeviceManager,
        output_dir: str,
        ble_interval: int = 60,
        camera_interval: int = 300,
        image_capture_interval: int = 0
    ):
        self.ble_addresses = ble_addresses
        self.mqtt = mqtt_publisher
        self.usb = usb_manager
        self.output_dir = output_dir
        self.ble_interval = ble_interval
        self.camera_interval = camera_interval
        
        # Image capture interval: 0 = same as camera_interval, -1 = disabled
        if image_capture_interval == 0:
            self.image_capture_interval = camera_interval
        elif image_capture_interval < 0:
            self.image_capture_interval = None  # Disabled
        else:
            self.image_capture_interval = image_capture_interval
        
        # Track last image save times
        self._last_optical_image_time: float = 0
        
        # Single thread for camera operations (memory efficient)
        self.executor = ThreadPoolExecutor(max_workers=1)
        self._running = True
        
        # Circuit breakers for each component (3 failures = 5 min cooldown)
        self.circuit_breakers = {
            'optical': CircuitBreaker('optical', failure_threshold=3, recovery_timeout=300),
        }
        # Per-BLE-device circuit breakers
        for addr in ble_addresses:
            self.circuit_breakers[f'ble_{addr}'] = CircuitBreaker(
                f'ble_{addr}', failure_threshold=5, recovery_timeout=180
            )
        
        # Timeouts for blocking operations
        self.optical_capture_timeout = 30  # seconds
    
    async def ble_monitor_loop(self):
        """Poll all BLE sensors at regular intervals"""
        if not self.ble_addresses:
            logger.info("No BLE addresses configured, skipping BLE monitoring")
            return
        
        logger.info(f"Starting BLE monitoring for {len(self.ble_addresses)} sensors")
        
        while self._running:
            start_time = time.monotonic()
            
            # Poll all sensors (could parallelize, but sequential is gentler on Pi Zero)
            for address in self.ble_addresses:
                if not self._running:
                    break
                
                cb_key = f'ble_{address}'
                cb = self.circuit_breakers.get(cb_key)
                
                # Check circuit breaker
                if cb and not await cb.attempt():
                    logger.debug(f"BLE {address}: circuit breaker open, skipping")
                    continue
                
                try:
                    reading = await poll_ble_sensor(address)
                    if reading:
                        # Format values safely (handle None)
                        def fmt(v):
                            return f"{v:.1f}" if v is not None else "N/A"
                        logger.info(
                            f"BLE {address}: "
                            f"Main={fmt(reading.main_temp)}°C/{fmt(reading.main_humidity)}% "
                            f"Ext={fmt(reading.external_temp)}°C/{fmt(reading.external_humidity)}%"
                        )
                        if self.mqtt:
                            self.mqtt.publish_climate(reading)
                        if cb:
                            await cb.record_success()
                    else:
                        logger.warning(f"Failed to read BLE sensor {address}")
                        if cb:
                            await cb.record_failure()
                except Exception as e:
                    logger.error(f"Unexpected error polling BLE {address}: {e}")
                    if cb:
                        await cb.record_failure()
            
            # Sleep for remaining interval
            elapsed = time.monotonic() - start_time
            sleep_time = max(0, self.ble_interval - elapsed)
            await asyncio.sleep(sleep_time)
    
    async def optical_capture_loop(self):
        """Capture optical images at regular intervals."""
        logger.info(f"Starting optical capture loop (interval: {self.camera_interval}s)")
        if self.image_capture_interval:
            logger.info(f"Optical image save interval: {self.image_capture_interval}s")
        else:
            logger.info("Optical image saving disabled (metrics only)")
        
        loop = asyncio.get_event_loop()
        cb = self.circuit_breakers['optical']
        
        while self._running:
            start_time = time.monotonic()
            
            # Check circuit breaker
            if not await cb.attempt():
                logger.debug("Optical camera: circuit breaker open, skipping")
                await asyncio.sleep(self.camera_interval)
                continue
            
            # Determine if we should save an image this cycle
            should_save_image = False
            if self.image_capture_interval is not None:
                time_since_last = time.monotonic() - self._last_optical_image_time
                should_save_image = time_since_last >= self.image_capture_interval
            
            try:
                logger.info("Acquiring exclusive optical camera access...")
                async with self.usb.exclusive_optical():
                    # Run capture with timeout
                    try:
                        image_path, light_metrics = await asyncio.wait_for(
                            loop.run_in_executor(
                                self.executor,
                                functools.partial(
                                    capture_optical_with_light_metrics,
                                    self.output_dir,
                                    save_image=should_save_image
                                )
                            ),
                            timeout=self.optical_capture_timeout
                        )
                        
                        if light_metrics:
                            logger.info(
                                f"Light: mean={light_metrics.normalized_mean:.2%}, "
                                f"bright={light_metrics.bright_pixel_ratio:.1%}"
                            )
                            if self.mqtt:
                                self.mqtt.publish_light(light_metrics)
                            await cb.record_success()
                            
                            # Update last image save time if we saved
                            if image_path:
                                self._last_optical_image_time = time.monotonic()
                        else:
                            logger.warning("Optical capture returned no metrics")
                            await cb.record_failure()
                    
                    except asyncio.TimeoutError:
                        logger.error(
                            f"Optical capture timed out after {self.optical_capture_timeout}s"
                        )
                        await cb.record_failure()
            
            except asyncio.TimeoutError:
                logger.error("Timeout waiting for exclusive camera access (optical)")
                await cb.record_failure()
            except Exception as e:
                logger.error(f"Optical capture error: {e}")
                await cb.record_failure()
            
            # Sleep for remaining interval
            elapsed = time.monotonic() - start_time
            sleep_time = max(0, self.camera_interval - elapsed)
            await asyncio.sleep(sleep_time)
    
    async def run(self):
        """
        Run all monitoring tasks concurrently.
        Each task runs independently - failures in one don't affect others.
        """
        tasks = []
        
        if self.ble_addresses:
            tasks.append(asyncio.create_task(
                self._supervised_task(self.ble_monitor_loop(), "BLE monitor")
            ))
        
        tasks.append(asyncio.create_task(
            self._supervised_task(self.optical_capture_loop(), "Optical camera")
        ))
        
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("Monitoring tasks cancelled")
        finally:
            self._running = False
            self.executor.shutdown(wait=False)
    
    async def _supervised_task(self, coro, name: str):
        """
        Wrap a coroutine with supervision - log errors but don't crash.
        Restarts the task if it fails unexpectedly.
        """
        restart_delay = 10  # seconds
        max_restarts = 5
        restart_count = 0
        
        while self._running and restart_count < max_restarts:
            try:
                await coro
                break  # Normal exit
            except asyncio.CancelledError:
                logger.info(f"{name}: task cancelled")
                break
            except Exception as e:
                restart_count += 1
                logger.error(
                    f"{name}: unexpected error (restart {restart_count}/{max_restarts}): {e}"
                )
                if restart_count < max_restarts and self._running:
                    logger.info(f"{name}: restarting in {restart_delay}s...")
                    await asyncio.sleep(restart_delay)
                    restart_delay = min(restart_delay * 2, 300)  # Exponential backoff
        
        if restart_count >= max_restarts:
            logger.error(f"{name}: max restarts exceeded, task stopped")
    
    def stop(self):
        """Signal all loops to stop"""
        self._running = False


# -----------------------------------------------------------------------------
# CLI Entry Point
# -----------------------------------------------------------------------------

def env_or_default(env_var: str, default, cast_type=str):
    """Get value from environment variable or use default"""
    val = os.environ.get(env_var)
    if val is None:
        return default
    if cast_type == bool:
        return val.lower() in ('true', '1', 'yes', 'on')
    return cast_type(val)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Unified Greenhouse Monitoring for Raspberry Pi Zero 2 W",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment Variables:
  All options can be set via environment variables (CLI args take precedence):
    GREENHOUSE_BLE_ADDRESSES     - Comma-separated BLE MAC addresses
    GREENHOUSE_BLE_INTERVAL      - BLE polling interval in seconds
    GREENHOUSE_CAMERA_INTERVAL   - Camera metric collection interval in seconds
    GREENHOUSE_IMAGE_CAPTURE_INTERVAL - Image save interval (0=same as camera, -1=disabled)
    GREENHOUSE_OPTICAL_USB_ID    - USB ID for optical camera
    GREENHOUSE_OUTPUT_DIR        - Directory to save captured images
    GREENHOUSE_MQTT_HOST         - MQTT broker address
    GREENHOUSE_MQTT_PORT         - MQTT broker port
    GREENHOUSE_MQTT_USER         - MQTT username
    GREENHOUSE_MQTT_PASS         - MQTT password
    GREENHOUSE_MQTT_BASE_TOPIC   - MQTT base topic prefix
    GREENHOUSE_DEBUG             - Enable debug logging (true/false)

Examples:
  # Basic usage with BLE sensors and MQTT
  python greenhouse_monitor.py --ble-addresses AA:BB:CC:DD:EE:FF \\
      --mqtt-host 192.168.1.100 --output-dir /home/pi/captures

  # Using environment variables
  export GREENHOUSE_MQTT_HOST=192.168.1.100
  export GREENHOUSE_BLE_ADDRESSES=AA:BB:CC:DD:EE:FF
  python greenhouse_monitor.py

  # systemd service with environment file
  # See /etc/greenhouse/greenhouse.env for configuration
"""
    )
    
    # BLE options
    parser.add_argument(
        "--ble-addresses",
        default=env_or_default("GREENHOUSE_BLE_ADDRESSES", ""),
        help="Comma-separated list of BLE MAC addresses for climate sensors "
             "(env: GREENHOUSE_BLE_ADDRESSES)"
    )
    parser.add_argument(
        "--ble-interval",
        type=int,
        default=env_or_default("GREENHOUSE_BLE_INTERVAL", 60, int),
        help="BLE polling interval in seconds (default: 60, 0 to disable) "
             "(env: GREENHOUSE_BLE_INTERVAL)"
    )
    
    # Camera options
    parser.add_argument(
        "--camera-interval",
        type=int,
        default=env_or_default("GREENHOUSE_CAMERA_INTERVAL", 300, int),
        help="Camera metric collection interval in seconds (default: 300) "
             "(env: GREENHOUSE_CAMERA_INTERVAL)"
    )
    parser.add_argument(
        "--image-capture-interval",
        type=int,
        default=env_or_default("GREENHOUSE_IMAGE_CAPTURE_INTERVAL", 0, int),
        help="Image save interval in seconds (default: 0 = same as camera-interval, "
             "-1 = never save images) (env: GREENHOUSE_IMAGE_CAPTURE_INTERVAL)"
    )
    parser.add_argument(
        "--optical-usb-id",
        default=env_or_default("GREENHOUSE_OPTICAL_USB_ID", DEFAULT_OPTICAL_USB_ID),
        help=f"USB ID for optical camera (default: {DEFAULT_OPTICAL_USB_ID}) "
             "(env: GREENHOUSE_OPTICAL_USB_ID)"
    )
    # Output options
    parser.add_argument(
        "-d", "--output-dir",
        default=env_or_default("GREENHOUSE_OUTPUT_DIR", "."),
        help="Directory to save captured images (default: current directory) "
             "(env: GREENHOUSE_OUTPUT_DIR)"
    )
    
    # MQTT options
    parser.add_argument(
        "--mqtt-host",
        default=env_or_default("GREENHOUSE_MQTT_HOST", None),
        help="MQTT broker address (env: GREENHOUSE_MQTT_HOST)"
    )
    parser.add_argument(
        "--mqtt-port",
        type=int,
        default=env_or_default("GREENHOUSE_MQTT_PORT", 1883, int),
        help="MQTT broker port (default: 1883) (env: GREENHOUSE_MQTT_PORT)"
    )
    parser.add_argument(
        "--mqtt-user",
        default=env_or_default("GREENHOUSE_MQTT_USER", None),
        help="MQTT username (optional) (env: GREENHOUSE_MQTT_USER)"
    )
    parser.add_argument(
        "--mqtt-pass",
        default=env_or_default("GREENHOUSE_MQTT_PASS", None),
        help="MQTT password (optional) (env: GREENHOUSE_MQTT_PASS)"
    )
    parser.add_argument(
        "--mqtt-base-topic",
        default=env_or_default("GREENHOUSE_MQTT_BASE_TOPIC", "greenhouse"),
        help="MQTT base topic prefix (default: greenhouse) "
             "(env: GREENHOUSE_MQTT_BASE_TOPIC)"
    )
    
    # Debug options
    parser.add_argument(
        "--debug",
        action="store_true",
        default=env_or_default("GREENHOUSE_DEBUG", False, bool),
        help="Enable debug logging (env: GREENHOUSE_DEBUG)"
    )
    
    return parser.parse_args()


def main():
    args = parse_args()
    
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Parse BLE addresses
    ble_addresses = []
    if args.ble_addresses and args.ble_interval > 0:
        seen = set()
        for addr in (s.strip() for s in args.ble_addresses.split(",") if s.strip()):
            norm = addr.upper()
            if norm not in seen:
                seen.add(norm)
                ble_addresses.append(addr)
    
    if ble_addresses:
        logger.info(f"Configured BLE sensors: {ble_addresses}")
    else:
        logger.info("No BLE sensors configured")
    
    # Setup output directory
    output_dir = os.path.abspath(args.output_dir)
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        logger.info(f"Created output directory: {output_dir}")
    
    # Setup MQTT
    mqtt_publisher = None
    if args.mqtt_host:
        if not MQTT_AVAILABLE:
            logger.error("paho-mqtt not installed. Install with: pip install paho-mqtt")
            sys.exit(1)
        mqtt_publisher = MQTTPublisher(
            host=args.mqtt_host,
            port=args.mqtt_port,
            username=args.mqtt_user,
            password=args.mqtt_pass,
            base_topic=args.mqtt_base_topic
        )
        if not mqtt_publisher.connect():
            logger.warning("MQTT connection failed, continuing without MQTT")
            mqtt_publisher = None
    
    # Setup USB manager
    usb_manager = USBDeviceManager(args.optical_usb_id)
    
    # Create monitor
    monitor = GreenhouseMonitor(
        ble_addresses=ble_addresses,
        mqtt_publisher=mqtt_publisher,
        usb_manager=usb_manager,
        output_dir=output_dir,
        ble_interval=args.ble_interval,
        camera_interval=args.camera_interval,
        image_capture_interval=args.image_capture_interval
    )
    
    # Setup graceful shutdown
    async def run_with_shutdown():
        loop = asyncio.get_event_loop()
        main_task = asyncio.create_task(monitor.run())
        
        def signal_handler():
            logger.info("Shutdown signal received")
            monitor.stop()
            main_task.cancel()
        
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, signal_handler)
        
        try:
            await main_task
        except asyncio.CancelledError:
            pass
        finally:
            # Cleanup
            await usb_manager.release_all()
            if mqtt_publisher:
                mqtt_publisher.disconnect()
    
    logger.info("Starting Greenhouse Monitor...")
    logger.info(f"BLE interval: {args.ble_interval}s, Camera interval: {args.camera_interval}s")
    if args.image_capture_interval == 0:
        logger.info(f"Image capture interval: {args.camera_interval}s (same as camera)")
    elif args.image_capture_interval < 0:
        logger.info("Image capture: disabled (metrics only)")
    else:
        logger.info(f"Image capture interval: {args.image_capture_interval}s")
    
    try:
        asyncio.run(run_with_shutdown())
    except KeyboardInterrupt:
        pass
    
    logger.info("Greenhouse Monitor stopped")


if __name__ == "__main__":
    main()
