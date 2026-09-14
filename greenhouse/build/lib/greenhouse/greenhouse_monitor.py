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
from croniter import croniter

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


@dataclass(frozen=True)
class CameraTuning:
    """Optional V4L2 image controls and capture warm-up behavior."""
    pixel_format: Optional[str] = None
    brightness: Optional[int] = None
    contrast: Optional[int] = None
    saturation: Optional[int] = None
    hue: Optional[int] = None
    auto_white_balance: Optional[bool] = None
    white_balance_temperature: Optional[int] = None
    gamma: Optional[int] = None
    gain: Optional[int] = None
    power_line_frequency: Optional[int] = None
    sharpness: Optional[int] = None
    backlight_compensation: Optional[int] = None
    auto_exposure: Optional[bool] = None
    exposure_time: Optional[int] = None
    dynamic_framerate: Optional[bool] = None
    warmup_frames: int = 5
    warmup_delay: float = 0.1

    def v4l2_controls(self) -> dict[str, int]:
        if self.auto_white_balance is True and self.white_balance_temperature is not None:
            raise ValueError(
                "manual white balance temperature requires auto white balance to be disabled"
            )
        if self.auto_exposure is True and self.exposure_time is not None:
            raise ValueError("manual exposure time requires auto exposure to be disabled")

        controls: dict[str, int] = {}
        direct_controls = {
            "brightness": self.brightness,
            "contrast": self.contrast,
            "saturation": self.saturation,
            "hue": self.hue,
            "gamma": self.gamma,
            "gain": self.gain,
            "power_line_frequency": self.power_line_frequency,
            "sharpness": self.sharpness,
            "backlight_compensation": self.backlight_compensation,
            "exposure_dynamic_framerate": (
                int(self.dynamic_framerate)
                if self.dynamic_framerate is not None else None
            ),
        }
        controls.update(
            (name, value) for name, value in direct_controls.items() if value is not None
        )

        if self.auto_white_balance is not None:
            controls["white_balance_automatic"] = int(self.auto_white_balance)
        if self.white_balance_temperature is not None:
            controls.setdefault("white_balance_automatic", 0)
            controls["white_balance_temperature"] = self.white_balance_temperature
        if self.auto_exposure is not None:
            controls["auto_exposure"] = 3 if self.auto_exposure else 1
        if self.exposure_time is not None:
            controls.setdefault("auto_exposure", 1)
            controls["exposure_time_absolute"] = self.exposure_time

        return controls


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

def apply_camera_tuning(tuning: CameraTuning, device: str = "/dev/video0") -> None:
    """Apply configured controls using their native V4L2 names and values."""
    controls = tuning.v4l2_controls()
    if not controls:
        return

    setting = ",".join(f"{name}={value}" for name, value in controls.items())
    subprocess.run(
        ["v4l2-ctl", "--device", device, "--set-ctrl", setting],
        check=True,
        capture_output=True,
        text=True,
    )
    logger.info(f"Applied camera controls: {setting}")


def capture_optical_with_light_metrics(
    output_dir: str,
    bright_thresh: int = 220,
    dark_thresh: int = 30,
    save_image: bool = True,
    image_width: int = 1920,
    image_height: int = 1080,
    jpeg_quality: int = 95,
    camera_tuning: Optional[CameraTuning] = None
) -> Tuple[Optional[str], Optional[LightMetrics]]:
    """
    Capture optical image and compute light metrics.
    Returns (image_path, metrics) or (None, None) on failure.
    
    Args:
        output_dir: Directory to save image
        bright_thresh: Threshold for bright pixel ratio
        dark_thresh: Threshold for dark pixel ratio
        save_image: If False, only compute metrics without saving image
        image_width: Requested width for saved images
        image_height: Requested height for saved images
        jpeg_quality: JPEG encoding quality from 0 to 100
        camera_tuning: Optional camera controls and warm-up behavior
    
    Memory-optimized: processes image in-place, releases resources immediately.
    """
    cap = None
    tuning = camera_tuning or CameraTuning()
    try:
        apply_camera_tuning(tuning)

        # Use V4L2 backend for Linux
        cap = cv.VideoCapture(0, cv.CAP_V4L2)
        if not cap.isOpened():
            logger.error("Could not open optical camera")
            return None, None
        
        # Metrics-only captures stay small; archival captures use the configured size.
        capture_width = image_width if save_image else 640
        capture_height = image_height if save_image else 480
        if tuning.pixel_format:
            cap.set(
                cv.CAP_PROP_FOURCC,
                cv.VideoWriter_fourcc(*tuning.pixel_format),
            )
        cap.set(cv.CAP_PROP_FRAME_WIDTH, capture_width)
        cap.set(cv.CAP_PROP_FRAME_HEIGHT, capture_height)
        cap.set(cv.CAP_PROP_BUFFERSIZE, 1)
        
        # Flush buffer (exposure/white balance adjustment)
        for _ in range(tuning.warmup_frames):
            cap.read()
            if tuning.warmup_delay:
                time.sleep(tuning.warmup_delay)
        
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
            if cv.imwrite(filename, frame, [cv.IMWRITE_JPEG_QUALITY, jpeg_quality]):
                height, width = frame.shape[:2]
                logger.info(
                    f"Saved optical image: {filename} "
                    f"({width}x{height}, JPEG quality {jpeg_quality})"
                )
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
        image_capture_cron: str = "0 9,15 * * *",
        image_width: int = 1920,
        image_height: int = 1080,
        jpeg_quality: int = 95,
        camera_tuning: Optional[CameraTuning] = None
    ):
        self.ble_addresses = ble_addresses
        self.mqtt = mqtt_publisher
        self.usb = usb_manager
        self.output_dir = output_dir
        self.ble_interval = ble_interval
        self.camera_interval = camera_interval
        self.image_width = image_width
        self.image_height = image_height
        self.jpeg_quality = jpeg_quality
        self.camera_tuning = camera_tuning or CameraTuning()
        self.image_capture_cron = image_capture_cron
        self._next_image_capture = (
            croniter(image_capture_cron, datetime.now()).get_next(datetime)
            if image_capture_cron else None
        )
        
        # Single thread for camera operations (memory efficient)
        self.executor = ThreadPoolExecutor(max_workers=1)
        self._running = True
        self._image_capture_requested = asyncio.Event()
        
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
        if self._next_image_capture:
            logger.info(
                f"Optical image schedule: {self.image_capture_cron} "
                f"(next capture: {self._next_image_capture.isoformat(timespec='minutes')})"
            )
        else:
            logger.info("Optical image saving disabled (metrics only)")
        
        loop = asyncio.get_event_loop()
        cb = self.circuit_breakers['optical']
        
        while self._running:
            start_time = time.monotonic()
            manual_capture_requested = self._image_capture_requested.is_set()
            if manual_capture_requested:
                self._image_capture_requested.clear()
            
            # Check circuit breaker
            if not manual_capture_requested and not await cb.attempt():
                logger.debug("Optical camera: circuit breaker open, skipping")
                try:
                    await asyncio.wait_for(
                        self._image_capture_requested.wait(),
                        timeout=self.camera_interval,
                    )
                except asyncio.TimeoutError:
                    pass
                continue
            
            # Determine if we should save an image this cycle
            scheduled_capture_due = (
                self._next_image_capture is not None
                and datetime.now() >= self._next_image_capture
            )
            should_save_image = manual_capture_requested or scheduled_capture_due
            
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
                                    save_image=should_save_image,
                                    image_width=self.image_width,
                                    image_height=self.image_height,
                                    jpeg_quality=self.jpeg_quality,
                                    camera_tuning=self.camera_tuning
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
                            
                            # Manual captures do not change the next scheduled capture.
                            if image_path and scheduled_capture_due:
                                self._next_image_capture = croniter(
                                    self.image_capture_cron, datetime.now()
                                ).get_next(datetime)
                                logger.info(
                                    "Next optical image capture: "
                                    f"{self._next_image_capture.isoformat(timespec='minutes')}"
                                )
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
            try:
                await asyncio.wait_for(
                    self._image_capture_requested.wait(),
                    timeout=sleep_time,
                )
            except asyncio.TimeoutError:
                pass
    
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

    def request_image_capture(self):
        """Request an immediate archival image from the camera loop."""
        logger.info("Immediate image capture requested")
        self._image_capture_requested.set()


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


def optional_env(env_var: str, cast_type):
    """Get an optional environment value, treating blank as unset."""
    value = os.environ.get(env_var)
    if value is None or not value.strip():
        return None
    return cast_type(value)


def positive_int(value: str) -> int:
    """Parse a positive integer for pixel dimensions."""
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def nonnegative_int(value: str) -> int:
    """Parse a nonnegative integer."""
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def bounded_int(value: str, minimum: int, maximum: int) -> int:
    """Parse an integer constrained to an inclusive range."""
    parsed = int(value)
    if not minimum <= parsed <= maximum:
        raise argparse.ArgumentTypeError(f"must be between {minimum} and {maximum}")
    return parsed


def nonnegative_float(value: str) -> float:
    """Parse a nonnegative floating-point value."""
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def boolean_value(value: str) -> bool:
    """Parse an explicit boolean option."""
    normalized = value.strip().lower()
    if normalized in ("true", "1", "yes", "on"):
        return True
    if normalized in ("false", "0", "no", "off"):
        return False
    raise argparse.ArgumentTypeError("must be true or false")


def pixel_format(value: str) -> Optional[str]:
    """Parse an optional camera pixel format."""
    normalized = value.strip().upper()
    if normalized in ("", "AUTO"):
        return None
    if normalized not in ("YUYV", "MJPG"):
        raise argparse.ArgumentTypeError("must be auto, YUYV, or MJPG")
    return normalized


def power_line_frequency(value: str) -> int:
    """Map a mains frequency label to the V4L2 menu value."""
    frequencies = {"disabled": 0, "0": 0, "50": 1, "60": 2}
    normalized = value.strip().lower()
    if normalized not in frequencies:
        raise argparse.ArgumentTypeError("must be disabled, 50, or 60")
    return frequencies[normalized]


def jpeg_quality(value: str) -> int:
    """Parse a JPEG quality value from 0 to 100."""
    parsed = int(value)
    if not 0 <= parsed <= 100:
        raise argparse.ArgumentTypeError("must be between 0 and 100")
    return parsed


def cron_expression(value: str) -> str:
    """Validate a five-field cron expression; an empty value disables saving."""
    value = value.strip()
    if value and (len(value.split()) != 5 or not croniter.is_valid(value)):
        raise argparse.ArgumentTypeError("must be a valid five-field cron expression")
    return value


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
    GREENHOUSE_IMAGE_CAPTURE_CRON - Image save schedule in local time (empty=disabled)
    GREENHOUSE_IMAGE_WIDTH       - Requested saved image width in pixels
    GREENHOUSE_IMAGE_HEIGHT      - Requested saved image height in pixels
    GREENHOUSE_JPEG_QUALITY      - Saved JPEG quality (0-100)
    GREENHOUSE_CAMERA_PIXEL_FORMAT - Camera format (auto, YUYV, or MJPG)
    GREENHOUSE_CAMERA_*          - Optional camera tuning controls; see --help
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
        "--image-capture-cron",
        type=cron_expression,
        default=env_or_default("GREENHOUSE_IMAGE_CAPTURE_CRON", "0 9,15 * * *"),
        help="Five-field cron schedule for saved images in local time; empty disables "
             "saving (default: '0 9,15 * * *') (env: GREENHOUSE_IMAGE_CAPTURE_CRON)"
    )
    parser.add_argument(
        "--image-width",
        type=positive_int,
        default=env_or_default("GREENHOUSE_IMAGE_WIDTH", 1920, positive_int),
        help="Requested saved image width in pixels (default: 1920) "
             "(env: GREENHOUSE_IMAGE_WIDTH)"
    )
    parser.add_argument(
        "--image-height",
        type=positive_int,
        default=env_or_default("GREENHOUSE_IMAGE_HEIGHT", 1080, positive_int),
        help="Requested saved image height in pixels (default: 1080) "
             "(env: GREENHOUSE_IMAGE_HEIGHT)"
    )
    parser.add_argument(
        "--jpeg-quality",
        type=jpeg_quality,
        metavar="0-100",
        default=env_or_default("GREENHOUSE_JPEG_QUALITY", 95, jpeg_quality),
        help="Saved JPEG quality from 0 to 100 (default: 95) "
             "(env: GREENHOUSE_JPEG_QUALITY)"
    )
    parser.add_argument(
        "--camera-pixel-format",
        type=pixel_format,
        default=optional_env("GREENHOUSE_CAMERA_PIXEL_FORMAT", pixel_format),
        help="Camera pixel format: auto, YUYV, or MJPG (default: auto) "
             "(env: GREENHOUSE_CAMERA_PIXEL_FORMAT)"
    )
    parser.add_argument(
        "--camera-brightness",
        type=functools.partial(bounded_int, minimum=-64, maximum=64),
        default=optional_env(
            "GREENHOUSE_CAMERA_BRIGHTNESS",
            functools.partial(bounded_int, minimum=-64, maximum=64),
        ),
        help="Camera brightness from -64 to 64 (env: GREENHOUSE_CAMERA_BRIGHTNESS)"
    )
    parser.add_argument(
        "--camera-contrast",
        type=functools.partial(bounded_int, minimum=0, maximum=64),
        default=optional_env(
            "GREENHOUSE_CAMERA_CONTRAST",
            functools.partial(bounded_int, minimum=0, maximum=64),
        ),
        help="Camera contrast from 0 to 64 (env: GREENHOUSE_CAMERA_CONTRAST)"
    )
    parser.add_argument(
        "--camera-saturation",
        type=functools.partial(bounded_int, minimum=0, maximum=128),
        default=optional_env(
            "GREENHOUSE_CAMERA_SATURATION",
            functools.partial(bounded_int, minimum=0, maximum=128),
        ),
        help="Camera saturation from 0 to 128 (env: GREENHOUSE_CAMERA_SATURATION)"
    )
    parser.add_argument(
        "--camera-hue",
        type=functools.partial(bounded_int, minimum=-40, maximum=40),
        default=optional_env(
            "GREENHOUSE_CAMERA_HUE",
            functools.partial(bounded_int, minimum=-40, maximum=40),
        ),
        help="Camera hue from -40 to 40 (env: GREENHOUSE_CAMERA_HUE)"
    )
    parser.add_argument(
        "--camera-auto-white-balance",
        type=boolean_value,
        default=optional_env("GREENHOUSE_CAMERA_AUTO_WHITE_BALANCE", boolean_value),
        help="Enable automatic white balance (true/false) "
             "(env: GREENHOUSE_CAMERA_AUTO_WHITE_BALANCE)"
    )
    parser.add_argument(
        "--camera-white-balance-temperature",
        type=functools.partial(bounded_int, minimum=2800, maximum=6500),
        default=optional_env(
            "GREENHOUSE_CAMERA_WHITE_BALANCE_TEMPERATURE",
            functools.partial(bounded_int, minimum=2800, maximum=6500),
        ),
        help="Manual white balance in Kelvin from 2800 to 6500 "
             "(env: GREENHOUSE_CAMERA_WHITE_BALANCE_TEMPERATURE)"
    )
    parser.add_argument(
        "--camera-gamma",
        type=functools.partial(bounded_int, minimum=72, maximum=500),
        default=optional_env(
            "GREENHOUSE_CAMERA_GAMMA",
            functools.partial(bounded_int, minimum=72, maximum=500),
        ),
        help="Camera gamma from 72 to 500 (env: GREENHOUSE_CAMERA_GAMMA)"
    )
    parser.add_argument(
        "--camera-gain",
        type=functools.partial(bounded_int, minimum=0, maximum=100),
        default=optional_env(
            "GREENHOUSE_CAMERA_GAIN",
            functools.partial(bounded_int, minimum=0, maximum=100),
        ),
        help="Camera gain from 0 to 100 (env: GREENHOUSE_CAMERA_GAIN)"
    )
    parser.add_argument(
        "--camera-power-line-frequency",
        type=power_line_frequency,
        metavar="disabled|50|60",
        default=optional_env(
            "GREENHOUSE_CAMERA_POWER_LINE_FREQUENCY",
            power_line_frequency,
        ),
        help="Anti-flicker mains frequency "
             "(env: GREENHOUSE_CAMERA_POWER_LINE_FREQUENCY)"
    )
    parser.add_argument(
        "--camera-sharpness",
        type=functools.partial(bounded_int, minimum=0, maximum=6),
        default=optional_env(
            "GREENHOUSE_CAMERA_SHARPNESS",
            functools.partial(bounded_int, minimum=0, maximum=6),
        ),
        help="Camera sharpness from 0 to 6 (env: GREENHOUSE_CAMERA_SHARPNESS)"
    )
    parser.add_argument(
        "--camera-backlight-compensation",
        type=functools.partial(bounded_int, minimum=0, maximum=192),
        default=optional_env(
            "GREENHOUSE_CAMERA_BACKLIGHT_COMPENSATION",
            functools.partial(bounded_int, minimum=0, maximum=192),
        ),
        help="Backlight compensation from 0 to 192 "
             "(env: GREENHOUSE_CAMERA_BACKLIGHT_COMPENSATION)"
    )
    parser.add_argument(
        "--camera-auto-exposure",
        type=boolean_value,
        default=optional_env("GREENHOUSE_CAMERA_AUTO_EXPOSURE", boolean_value),
        help="Enable automatic exposure (true/false) "
             "(env: GREENHOUSE_CAMERA_AUTO_EXPOSURE)"
    )
    parser.add_argument(
        "--camera-exposure-time",
        type=functools.partial(bounded_int, minimum=1, maximum=5000),
        default=optional_env(
            "GREENHOUSE_CAMERA_EXPOSURE_TIME",
            functools.partial(bounded_int, minimum=1, maximum=5000),
        ),
        help="Manual exposure in 100 microsecond units from 1 to 5000 "
             "(env: GREENHOUSE_CAMERA_EXPOSURE_TIME)"
    )
    parser.add_argument(
        "--camera-dynamic-framerate",
        type=boolean_value,
        default=optional_env("GREENHOUSE_CAMERA_DYNAMIC_FRAMERATE", boolean_value),
        help="Allow exposure to reduce frame rate (true/false) "
             "(env: GREENHOUSE_CAMERA_DYNAMIC_FRAMERATE)"
    )
    parser.add_argument(
        "--camera-warmup-frames",
        type=nonnegative_int,
        default=env_or_default("GREENHOUSE_CAMERA_WARMUP_FRAMES", 5, nonnegative_int),
        help="Frames discarded before capture (default: 5) "
             "(env: GREENHOUSE_CAMERA_WARMUP_FRAMES)"
    )
    parser.add_argument(
        "--camera-warmup-delay",
        type=nonnegative_float,
        default=env_or_default("GREENHOUSE_CAMERA_WARMUP_DELAY", 0.1, nonnegative_float),
        help="Delay between warm-up frames in seconds (default: 0.1) "
             "(env: GREENHOUSE_CAMERA_WARMUP_DELAY)"
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
    
    args = parser.parse_args()
    if (
        args.camera_auto_white_balance is True
        and args.camera_white_balance_temperature is not None
    ):
        parser.error(
            "--camera-white-balance-temperature requires automatic white balance "
            "to be disabled"
        )
    if args.camera_auto_exposure is True and args.camera_exposure_time is not None:
        parser.error("--camera-exposure-time requires automatic exposure to be disabled")
    return args


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
    camera_tuning = CameraTuning(
        pixel_format=args.camera_pixel_format,
        brightness=args.camera_brightness,
        contrast=args.camera_contrast,
        saturation=args.camera_saturation,
        hue=args.camera_hue,
        auto_white_balance=args.camera_auto_white_balance,
        white_balance_temperature=args.camera_white_balance_temperature,
        gamma=args.camera_gamma,
        gain=args.camera_gain,
        power_line_frequency=args.camera_power_line_frequency,
        sharpness=args.camera_sharpness,
        backlight_compensation=args.camera_backlight_compensation,
        auto_exposure=args.camera_auto_exposure,
        exposure_time=args.camera_exposure_time,
        dynamic_framerate=args.camera_dynamic_framerate,
        warmup_frames=args.camera_warmup_frames,
        warmup_delay=args.camera_warmup_delay,
    )
    
    # Create monitor
    monitor = GreenhouseMonitor(
        ble_addresses=ble_addresses,
        mqtt_publisher=mqtt_publisher,
        usb_manager=usb_manager,
        output_dir=output_dir,
        ble_interval=args.ble_interval,
        camera_interval=args.camera_interval,
        image_capture_cron=args.image_capture_cron,
        image_width=args.image_width,
        image_height=args.image_height,
        jpeg_quality=args.jpeg_quality,
        camera_tuning=camera_tuning
    )
    
    # Setup graceful shutdown
    async def run_with_shutdown():
        loop = asyncio.get_event_loop()
        main_task = asyncio.create_task(monitor.run())
        
        def shutdown_signal_handler():
            logger.info("Shutdown signal received")
            monitor.stop()
            main_task.cancel()
        
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, shutdown_signal_handler)
        loop.add_signal_handler(signal.SIGHUP, monitor.request_image_capture)
        
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
    if not args.image_capture_cron:
        logger.info("Image capture: disabled (metrics only)")
    else:
        logger.info(f"Image capture cron schedule: {args.image_capture_cron} (local time)")
    logger.info(
        f"Saved image settings: {args.image_width}x{args.image_height}, "
        f"JPEG quality {args.jpeg_quality}"
    )
    configured_controls = camera_tuning.v4l2_controls()
    logger.info(
        f"Camera tuning: pixel format={camera_tuning.pixel_format or 'auto'}, "
        f"controls={configured_controls or 'device defaults'}, "
        f"warm-up={camera_tuning.warmup_frames} frames at "
        f"{camera_tuning.warmup_delay:g}s"
    )
    
    try:
        asyncio.run(run_with_shutdown())
    except KeyboardInterrupt:
        pass
    
    logger.info("Greenhouse Monitor stopped")


if __name__ == "__main__":
    main()
