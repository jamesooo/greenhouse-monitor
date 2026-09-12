# Greenhouse Monitor

A unified monitoring solution for greenhouse environments running on Raspberry Pi Zero 2 W. Combines BLE climate sensors and optical camera light measurement in a single, resource-efficient script.

## Features

- **BLE Climate Monitoring**: Poll multiple Bluetooth Low Energy temperature/humidity sensors
- **Optical Light Measurement**: Capture images and compute average light levels
- **MQTT Publishing**: Stream all sensor data to an MQTT broker for integration with Home Assistant, Node-RED, etc.
- **Image Archival**: Save optical images to disk at configurable intervals
- **Fault Isolation**: Circuit breakers prevent one failing sensor from affecting others
- **USB Device Management**: Binds the optical camera only while capturing

## System Requirements

### Hardware

- **Raspberry Pi Zero 2 W** (primary target) or any Raspberry Pi with:
  - Bluetooth support (for BLE sensors)
  - USB port (for cameras)
- **Optical USB Camera** (e.g., standard webcam)
- **BLE Climate Sensors** (compatible with the `0000fff3/0000fff5` characteristic protocol)

### Software

- Python 3.8+
- Raspbian/Raspberry Pi OS (Linux required for USB bind/unbind)
- Root access or sudo privileges (for USB device management)

## Installation

Build the Debian package from the repository root:

```bash
bash packaging/build-deb.sh
```

Copy the resulting package from `dist/` to the Raspberry Pi and install it:

```bash
sudo apt install ./greenhouse-monitor_1.5.0-7_all.deb
```

The package installs the application under `/opt/greenhouse`, preserves the configuration at `/etc/greenhouse/greenhouse.env` during upgrades, and enables and restarts the systemd service. Network access is required during installation so the package can populate its Python virtual environment.

### Development Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/greenhouse-monitor --help
```

## Configuration

All settings can be configured via command-line arguments or environment variables. Environment variables are ideal for systemd service deployment.

### Environment File

Edit `/etc/greenhouse/greenhouse.env`:

```bash
# BLE Climate Sensors
GREENHOUSE_BLE_ADDRESSES=AA:BB:CC:DD:EE:FF,11:22:33:44:55:66
GREENHOUSE_BLE_INTERVAL=60

# Camera Settings
GREENHOUSE_CAMERA_INTERVAL=300
GREENHOUSE_IMAGE_CAPTURE_CRON="0 9,15 * * *"  # Save at 09:00 and 15:00 local time
GREENHOUSE_IMAGE_WIDTH=1920
GREENHOUSE_IMAGE_HEIGHT=1080
GREENHOUSE_JPEG_QUALITY=95

# Optional tuning (blank values preserve camera defaults)
GREENHOUSE_CAMERA_PIXEL_FORMAT=YUYV
GREENHOUSE_CAMERA_POWER_LINE_FREQUENCY=60
GREENHOUSE_CAMERA_AUTO_WHITE_BALANCE=false
GREENHOUSE_CAMERA_WHITE_BALANCE_TEMPERATURE=5000
GREENHOUSE_CAMERA_AUTO_EXPOSURE=false
GREENHOUSE_CAMERA_EXPOSURE_TIME=200
GREENHOUSE_CAMERA_GAIN=0
GREENHOUSE_CAMERA_SHARPNESS=3
GREENHOUSE_CAMERA_WARMUP_FRAMES=20
GREENHOUSE_CAMERA_WARMUP_DELAY=0.1

# USB Device IDs (find with: lsusb -t)
GREENHOUSE_OPTICAL_USB_ID=1-1.1.4

# Output
GREENHOUSE_OUTPUT_DIR=/opt/greenhouse/captures

# MQTT
GREENHOUSE_MQTT_HOST=192.168.1.100
GREENHOUSE_MQTT_PORT=1883
GREENHOUSE_MQTT_USER=
GREENHOUSE_MQTT_PASS=
GREENHOUSE_MQTT_BASE_TOPIC=greenhouse
```

### Configuration Reference

| Variable | CLI Argument | Default | Description |
|----------|--------------|---------|-------------|
| `GREENHOUSE_BLE_ADDRESSES` | `--ble-addresses` | (none) | Comma-separated BLE MAC addresses |
| `GREENHOUSE_BLE_INTERVAL` | `--ble-interval` | 60 | Seconds between BLE polls (0 = disable) |
| `GREENHOUSE_CAMERA_INTERVAL` | `--camera-interval` | 300 | Seconds between camera metric captures |
| `GREENHOUSE_IMAGE_CAPTURE_CRON` | `--image-capture-cron` | `0 9,15 * * *` | Five-field image schedule in local time (empty = disabled) |
| `GREENHOUSE_IMAGE_WIDTH` | `--image-width` | 1920 | Requested saved image width in pixels |
| `GREENHOUSE_IMAGE_HEIGHT` | `--image-height` | 1080 | Requested saved image height in pixels |
| `GREENHOUSE_JPEG_QUALITY` | `--jpeg-quality` | 95 | Saved JPEG quality (0-100) |
| `GREENHOUSE_CAMERA_PIXEL_FORMAT` | `--camera-pixel-format` | auto | Pixel format: `auto`, `YUYV`, or `MJPG` |
| `GREENHOUSE_CAMERA_BRIGHTNESS` | `--camera-brightness` | device default | Brightness (-64 to 64) |
| `GREENHOUSE_CAMERA_CONTRAST` | `--camera-contrast` | device default | Contrast (0 to 64) |
| `GREENHOUSE_CAMERA_SATURATION` | `--camera-saturation` | device default | Saturation (0 to 128) |
| `GREENHOUSE_CAMERA_HUE` | `--camera-hue` | device default | Hue (-40 to 40) |
| `GREENHOUSE_CAMERA_AUTO_WHITE_BALANCE` | `--camera-auto-white-balance` | device default | Automatic white balance (`true`/`false`) |
| `GREENHOUSE_CAMERA_WHITE_BALANCE_TEMPERATURE` | `--camera-white-balance-temperature` | device default | Manual white balance in Kelvin (2800 to 6500) |
| `GREENHOUSE_CAMERA_GAMMA` | `--camera-gamma` | device default | Gamma (72 to 500) |
| `GREENHOUSE_CAMERA_GAIN` | `--camera-gain` | device default | Sensor gain (0 to 100) |
| `GREENHOUSE_CAMERA_POWER_LINE_FREQUENCY` | `--camera-power-line-frequency` | device default | Anti-flicker mode: `disabled`, `50`, or `60` |
| `GREENHOUSE_CAMERA_SHARPNESS` | `--camera-sharpness` | device default | Sharpness (0 to 6) |
| `GREENHOUSE_CAMERA_BACKLIGHT_COMPENSATION` | `--camera-backlight-compensation` | device default | Backlight compensation (0 to 192) |
| `GREENHOUSE_CAMERA_AUTO_EXPOSURE` | `--camera-auto-exposure` | device default | Automatic exposure (`true`/`false`) |
| `GREENHOUSE_CAMERA_EXPOSURE_TIME` | `--camera-exposure-time` | device default | Manual exposure in 100-microsecond units (1 to 5000) |
| `GREENHOUSE_CAMERA_DYNAMIC_FRAMERATE` | `--camera-dynamic-framerate` | device default | Let exposure reduce frame rate (`true`/`false`) |
| `GREENHOUSE_CAMERA_WARMUP_FRAMES` | `--camera-warmup-frames` | 5 | Frames discarded before capture |
| `GREENHOUSE_CAMERA_WARMUP_DELAY` | `--camera-warmup-delay` | 0.1 | Delay between warm-up frames in seconds |
| `GREENHOUSE_OPTICAL_USB_ID` | `--optical-usb-id` | 1-1.1.4 | USB device ID for optical camera |
| `GREENHOUSE_OUTPUT_DIR` | `--output-dir` | . | Directory for saved images |
| `GREENHOUSE_MQTT_HOST` | `--mqtt-host` | (none) | MQTT broker address |
| `GREENHOUSE_MQTT_PORT` | `--mqtt-port` | 1883 | MQTT broker port |
| `GREENHOUSE_MQTT_USER` | `--mqtt-user` | (none) | MQTT username |
| `GREENHOUSE_MQTT_PASS` | `--mqtt-pass` | (none) | MQTT password |
| `GREENHOUSE_MQTT_BASE_TOPIC` | `--mqtt-base-topic` | greenhouse | MQTT topic prefix |
| `GREENHOUSE_DEBUG` | `--debug` | false | Enable debug logging |

Image cron schedules use the host's local timezone. The camera loop evaluates the
schedule at each metrics capture, so an image may be recorded up to one
`GREENHOUSE_CAMERA_INTERVAL` after its scheduled time.

Camera control ranges are device-specific; the documented ranges match the
Innomaker U20CAM-720P. Setting a manual white balance temperature or exposure
implicitly selects manual mode unless its corresponding automatic setting is
explicitly `true`, which is rejected as a configuration error. Longer warm-up
periods allow automatic exposure and white balance more time to settle after
the USB camera is rebound.

### Finding USB Device IDs

```bash
# List USB device tree
lsusb -t

# Example output:
# /:  Bus 01.Port 1: Dev 1, Class=root_hub
#     |__ Port 1: Dev 2, If 0, Class=Hub
#         |__ Port 1: Dev 3, If 0, Class=Hub
#             |__ Port 4: Dev 6, If 0, Class=Video  <-- Optical: 1-1.1.4
```

### Finding BLE Sensor Addresses

```bash
# Scan for BLE devices
bluetoothctl scan on

# Look for your climate sensors in the output
# Example: [NEW] Device AA:BB:CC:DD:EE:FF Climate-Sensor-1
```

## MQTT Data Format

### Topics

| Topic | Description | Interval |
|-------|-------------|----------|
| `greenhouse/climate/{MAC}` | BLE sensor data | `--ble-interval` |
| `greenhouse/light` | Optical camera light metrics | `--camera-interval` |

### Payload Schemas

#### Climate Data (`greenhouse/climate/{MAC}`)

```json
{
  "timestamp": "2026-03-02T14:30:00.123456",
  "address": "AA:BB:CC:DD:EE:FF",
  "main_temp": 24.5,
  "main_humidity": 65.2,
  "external_temp": 18.3,
  "external_humidity": 72.1
}
```

#### Light Metrics (`greenhouse/light`)

```json
{
  "timestamp": "2026-03-02T14:30:00.123456",
  "mean": 142.5,
  "normalized_mean": 0.559,
  "median": 138.0,
  "std": 45.2,
  "bright_pixel_ratio": 0.12,
  "dark_pixel_ratio": 0.08
}
```

## Running the Service

### Start/Stop

```bash
# Enable at boot
sudo systemctl enable greenhouse-monitor

# Start now
sudo systemctl start greenhouse-monitor

# Stop
sudo systemctl stop greenhouse-monitor

# Restart
sudo systemctl restart greenhouse-monitor
```

### Monitoring

```bash
# Check status
sudo systemctl status greenhouse-monitor

# Follow logs
sudo journalctl -u greenhouse-monitor -f

# View recent logs
sudo journalctl -u greenhouse-monitor --since "1 hour ago"
```

### Capture an Image Now

Send `SIGHUP` to request one full-quality image without changing the cron
schedule. The request wakes the camera loop and uses the configured resolution,
JPEG quality, tuning controls, and output directory:

```bash
sudo systemctl kill --signal=HUP greenhouse-monitor
```

### Manual Testing

```bash
# Run directly with verbose output
/opt/greenhouse/venv/bin/python /opt/greenhouse/greenhouse_monitor.py \
    --ble-addresses AA:BB:CC:DD:EE:FF \
    --mqtt-host 192.168.1.100 \
    --camera-interval 60 \
    --debug
```

## Architecture

### Concurrency Model

```
┌───────────────────────────────────────┐
│          asyncio Event Loop           │
├──────────────────┬────────────────────┤
│   BLE Monitor    │    Optical Loop    │
│     (async)      │      (async)       │
│                  │         │          │
│                  │         ▼          │
│                  │   ┌───────────┐    │
│                  │   │ Thread    │    │
│                  │   │ Pool (1)  │    │
│                  │   └─────┬─────┘    │
└──────────────────┴─────────┼──────────┘
                             ▼
                    ┌─────────────────┐
                    │ Optical Camera  │
                    └─────────────────┘
```

### Error Isolation

Each component has its own **circuit breaker** to prevent cascading failures:

| Component | Failure Threshold | Recovery Timeout |
|-----------|-------------------|------------------|
| Optical Camera | 3 failures | 5 minutes |
| BLE Sensors (each) | 5 failures | 3 minutes |

When a circuit breaker opens:
1. That component stops attempting operations
2. Other components continue normally
3. After the recovery timeout, the component tries again
4. On success, normal operation resumes

### USB Device Management

The optical camera is bound before each capture and automatically unbound afterward, including when capture fails.

## Resource Usage

### Memory (Typical)

| Component | Usage |
|-----------|-------|
| Python interpreter | ~15 MB |
| OpenCV frame buffer | ~1 MB |
| asyncio + tasks | ~5 MB |
| Libraries (numpy, etc.) | ~20 MB |
| **Total** | **~40-50 MB** |

### CPU

- Idle: < 1%
- During metrics capture: 10-30% (one core)
- During high-resolution image capture: camera- and resolution-dependent
- BLE polling: < 5%

### Disk I/O

- Image size depends on resolution, scene detail, and JPEG quality
- Capture times are configurable via `--image-capture-cron`
- Automatic cleanup available via cron (see install script)

## Troubleshooting

### Common Issues

#### "Permission denied" for USB bind/unbind

```bash
# The script needs root access for USB management
# Either run as root or configure sudoers:
echo "pi ALL=(ALL) NOPASSWD: /usr/bin/tee /sys/bus/usb/drivers/usb/*" | \
    sudo tee /etc/sudoers.d/greenhouse-usb
```

#### BLE sensors not found

```bash
# Check Bluetooth is enabled
sudo systemctl status bluetooth

# Verify sensor is advertising
bluetoothctl scan on
```

#### MQTT not publishing

```bash
# Test broker connectivity
mosquitto_pub -h YOUR_BROKER -t test -m "hello"

# Check credentials
mosquitto_sub -h YOUR_BROKER -u USER -P PASS -t "greenhouse/#"
```

### Log Messages

| Message | Meaning |
|---------|---------|
| `Circuit breaker [X]: OPEN` | Component X failed repeatedly, pausing |
| `Circuit breaker [X]: CLOSED (recovered)` | Component X working again |
| `Failed to bind X camera` | USB device not available or permission issue |

## Integration Examples

### Home Assistant

```yaml
# configuration.yaml
mqtt:
  sensor:
    - name: "Greenhouse Temperature"
      state_topic: "greenhouse/climate/AABBCCDDEEFF"
      value_template: "{{ value_json.main_temp }}"
      unit_of_measurement: "°C"
      
    - name: "Greenhouse Humidity"
      state_topic: "greenhouse/climate/AABBCCDDEEFF"
      value_template: "{{ value_json.main_humidity }}"
      unit_of_measurement: "%"
      
    - name: "Greenhouse Light Level"
      state_topic: "greenhouse/light"
      value_template: "{{ (value_json.normalized_mean * 100) | round(1) }}"
      unit_of_measurement: "%"
      
```

### Node-RED

Import the MQTT-in node and subscribe to `greenhouse/#` to receive all sensor data.

## License

This script is part of the pysenxor project. See the main repository for license information.
