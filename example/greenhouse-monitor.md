# Greenhouse Monitor

A unified monitoring solution for greenhouse environments running on Raspberry Pi Zero 2 W. Combines BLE climate sensors, optical camera light measurement, and thermal camera canopy temperature monitoring into a single, resource-efficient script.

## Features

- **BLE Climate Monitoring**: Poll multiple Bluetooth Low Energy temperature/humidity sensors
- **Optical Light Measurement**: Capture images and compute average light levels
- **Thermal Canopy Analysis**: Measure plant canopy temperature using thermal imaging
- **MQTT Publishing**: Stream all sensor data to an MQTT broker for integration with Home Assistant, Node-RED, etc.
- **Image Archival**: Save optical and thermal images to disk at configurable intervals
- **Fault Isolation**: Circuit breakers prevent one failing sensor from affecting others
- **USB Bandwidth Management**: Enforces mutual exclusivity for camera devices on bandwidth-limited Pi Zero

## System Requirements

### Hardware

- **Raspberry Pi Zero 2 W** (primary target) or any Raspberry Pi with:
  - Bluetooth support (for BLE sensors)
  - USB port (for cameras)
- **Optical USB Camera** (e.g., standard webcam)
- **Meridian Innovation SenXor Thermal Camera** (MI48)
- **BLE Climate Sensors** (compatible with the `0000fff3/0000fff5` characteristic protocol)

### Software

- Python 3.8+
- Raspbian/Raspberry Pi OS (Linux required for USB bind/unbind)
- Root access or sudo privileges (for USB device management)

## Installation

### Quick Install

```bash
# Clone or copy the pysenxor repository
cd /path/to/pysenxor-master/example

# Run the installer (requires root)
sudo ./install-service.sh
```

### Manual Install

```bash
# Create installation directory
sudo mkdir -p /opt/greenhouse
sudo mkdir -p /etc/greenhouse

# Create virtual environment
python3 -m venv /opt/greenhouse/venv

# Install dependencies
/opt/greenhouse/venv/bin/pip install \
    bleak \
    paho-mqtt \
    opencv-python-headless \
    numpy \
    pyserial

# Install senxor library
cd /path/to/pysenxor-master
/opt/greenhouse/venv/bin/pip install -e .

# Copy files
sudo cp example/greenhouse_monitor.py /opt/greenhouse/
sudo cp example/greenhouse.env /etc/greenhouse/
sudo cp example/greenhouse-monitor.service /etc/systemd/system/
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
GREENHOUSE_IMAGE_CAPTURE_INTERVAL=3600  # Save images hourly

# USB Device IDs (find with: lsusb -t)
GREENHOUSE_OPTICAL_USB_ID=1-1.1.4
GREENHOUSE_THERMAL_USB_ID=1-1.1.3

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
| `GREENHOUSE_IMAGE_CAPTURE_INTERVAL` | `--image-capture-interval` | 0 | Image save interval (0 = same as camera, -1 = disabled) |
| `GREENHOUSE_OPTICAL_USB_ID` | `--optical-usb-id` | 1-1.1.4 | USB device ID for optical camera |
| `GREENHOUSE_THERMAL_USB_ID` | `--thermal-usb-id` | 1-1.1.3 | USB device ID for thermal camera |
| `GREENHOUSE_OUTPUT_DIR` | `--output-dir` | . | Directory for saved images |
| `GREENHOUSE_MQTT_HOST` | `--mqtt-host` | (none) | MQTT broker address |
| `GREENHOUSE_MQTT_PORT` | `--mqtt-port` | 1883 | MQTT broker port |
| `GREENHOUSE_MQTT_USER` | `--mqtt-user` | (none) | MQTT username |
| `GREENHOUSE_MQTT_PASS` | `--mqtt-pass` | (none) | MQTT password |
| `GREENHOUSE_MQTT_BASE_TOPIC` | `--mqtt-base-topic` | greenhouse | MQTT topic prefix |
| `GREENHOUSE_DEBUG` | `--debug` | false | Enable debug logging |

### Finding USB Device IDs

```bash
# List USB device tree
lsusb -t

# Example output:
# /:  Bus 01.Port 1: Dev 1, Class=root_hub
#     |__ Port 1: Dev 2, If 0, Class=Hub
#         |__ Port 1: Dev 3, If 0, Class=Hub
#             |__ Port 3: Dev 5, If 0, Class=Video  <-- Thermal: 1-1.1.3
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
| `greenhouse/canopy` | Thermal camera temperature metrics | `--camera-interval` |

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

#### Canopy Metrics (`greenhouse/canopy`)

```json
{
  "timestamp": "2026-03-02T14:30:00.123456",
  "mean_temp": 26.3,
  "min_temp": 22.1,
  "max_temp": 31.5,
  "std_temp": 2.4,
  "median_temp": 26.0
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
┌─────────────────────────────────────────────────────────────┐
│                      asyncio Event Loop                      │
├─────────────────┬─────────────────┬─────────────────────────┤
│  BLE Monitor    │  Optical Loop   │     Thermal Loop        │
│    (async)      │    (async)      │       (async)           │
│                 │        │        │           │             │
│                 │        ▼        │           ▼             │
│                 │  ┌──────────┐   │    ┌──────────┐         │
│                 │  │ Thread   │   │    │ Thread   │         │
│                 │  │ Pool (1) │   │    │ Pool (1) │         │
│                 │  └──────────┘   │    └──────────┘         │
└─────────────────┴─────────────────┴─────────────────────────┘
                              │
                              ▼
                    ┌──────────────────┐
                    │  USB Semaphore   │
                    │  (Mutex for USB) │
                    └──────────────────┘
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
     ┌─────────────────┐             ┌─────────────────┐
     │ Optical Camera  │             │ Thermal Camera  │
     │   (bound)       │     OR      │   (bound)       │
     └─────────────────┘             └─────────────────┘
```

### Error Isolation

Each component has its own **circuit breaker** to prevent cascading failures:

| Component | Failure Threshold | Recovery Timeout |
|-----------|-------------------|------------------|
| Optical Camera | 3 failures | 5 minutes |
| Thermal Camera | 3 failures | 5 minutes |
| BLE Sensors (each) | 5 failures | 3 minutes |

When a circuit breaker opens:
1. That component stops attempting operations
2. Other components continue normally
3. After the recovery timeout, the component tries again
4. On success, normal operation resumes

### USB Bandwidth Management

The Pi Zero 2 W has limited USB bandwidth. This script enforces that only one camera can be bound at a time:

1. **Semaphore Lock**: A camera task must acquire the semaphore before binding
2. **Exclusive Access**: The other camera is always unbound first
3. **Automatic Release**: Context managers ensure cleanup even on errors
4. **Staggered Scheduling**: Thermal captures are offset by half the interval to minimize contention

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
- During capture: 10-30% (one core)
- BLE polling: < 5%

### Disk I/O

- Images: ~50-100 KB each (JPEG)
- Configurable via `--image-capture-interval`
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

#### Thermal camera not connecting

```bash
# Check serial ports
ls -la /dev/ttyUSB* /dev/ttyACM*

# Verify USB device is bound
cat /sys/bus/usb/devices/1-1.1.3/driver/module

# Check senxor can connect
python3 -c "from senxor.utils import connect_senxor; print(connect_senxor())"
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
| `Timeout waiting for camera semaphore` | Other camera task holding USB too long |
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
      
    - name: "Canopy Temperature"
      state_topic: "greenhouse/canopy"
      value_template: "{{ value_json.mean_temp }}"
      unit_of_measurement: "°C"
```

### Node-RED

Import the MQTT-in node and subscribe to `greenhouse/#` to receive all sensor data.

## License

This script is part of the pysenxor project. See the main repository for license information.
