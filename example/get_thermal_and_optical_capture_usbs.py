import os
import subprocess
import time
import sys
import logging
import signal
import argparse
import functools
import numpy as np
import cv2 as cv

# Force unbuffered output for systemd/journald
print = functools.partial(print, flush=True)

# Ensure the senxor library is found
sys.path.append("/home/test/myenv/lib/python3.11/site-packages")

from senxor.mi48 import MI48
from senxor.utils import data_to_frame, remap, cv_filter, connect_senxor

# Setup logging - use StreamHandler with immediate flush
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# You need to find these IDs using 'lsusb -t' 
# They look like '1-1.2' or '1-1.4'
OPTICAL_USB_ID = "1-1.1.4" 
THERMAL_USB_ID = "1-1.1.3"

def is_device_bound(usb_id):
    """Check if a USB device is currently bound to a driver"""
    return os.path.exists(f"/sys/bus/usb/devices/{usb_id}/driver")

def set_camera_state(usb_id, state):
    """state is 'bind' or 'unbind'"""
    path = f"/sys/bus/usb/drivers/usb/{state}"
    device_exists = os.path.exists(f"/sys/bus/usb/devices/{usb_id}")
    
    if not device_exists:
        print(f"Warning: Device {usb_id} not found in /sys/bus/usb/devices/")
        return False
    
    currently_bound = is_device_bound(usb_id)
    
    # Skip if already in desired state
    if state == "unbind" and not currently_bound:
        print(f"Device {usb_id} is already unbound, skipping.")
        return True
    if state == "bind" and currently_bound:
        print(f"Device {usb_id} is already bound, skipping.")
        return True
    
    try:
        # Requires sudo/root permissions
        result = subprocess.run(
            f"echo '{usb_id}' | sudo tee {path}",
            shell=True, check=True, capture_output=True, text=True
        )
        time.sleep(1)  # Give the kernel a second to breathe
        print(f"Successfully {state} device {usb_id}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error {state}ing {usb_id}: {e.stderr.strip()}")
        return False


def capture_optical_image(output_dir="."):
    """Capture an image from the optical USB camera"""
    # Use V4L2 backend for Linux stability
    cap = cv.VideoCapture(0, cv.CAP_V4L2)

    if not cap.isOpened():
        print("Error: Could not access /dev/video0. Check permissions or if camera is in use.")
        return False

    # Optimization for Pi Zero: Set a standard resolution to save RAM
    cap.set(cv.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv.CAP_PROP_BUFFERSIZE, 1)

    print("Optical camera initialized. Flushing buffer...")

    try:
        # USB cameras often need a moment to adjust exposure/white balance.
        # We skip the first few frames to avoid a dark/green image.
        for _ in range(5):
            cap.read()
            time.sleep(0.1)

        # Grab the actual frame
        ret, frame = cap.read()

        if ret:
            filename = os.path.join(output_dir, f"optical_capture_{int(time.time())}.jpg")
            success = cv.imwrite(filename, frame)
            if success:
                print(f"Success! Optical image saved to: {os.path.abspath(filename)}")
                return True
            else:
                print("Error: OpenCV could not write the file. Check disk space/permissions.")
                return False
        else:
            print("Error: Could not read frame from camera.")
            return False

    finally:
        cap.release()


class TimeoutError(Exception):
    pass

def timeout_handler(signum, frame):
    raise TimeoutError("Connection timed out")

def connect_with_timeout(timeout_seconds=10):
    """Attempt to connect to senxor with a timeout"""
    # Set up the signal handler
    old_handler = signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(timeout_seconds)
    
    try:
        mi48, connected_port, port_names = connect_senxor()
        signal.alarm(0)  # Cancel the alarm
        return mi48, connected_port, port_names
    except TimeoutError:
        print(f"Connection timed out after {timeout_seconds} seconds")
        return None, None, []
    except Exception as e:
        signal.alarm(0)
        raise e
    finally:
        signal.signal(signal.SIGALRM, old_handler)

def capture_thermal_image(output_dir="."):
    """Capture an image from the thermal camera"""
    from serial.tools import list_ports
    
    print("Connecting to thermal sensor...")
    
    # Wait for serial device to appear after bind
    max_retries = 3
    mi48 = None
    connection_timeout = 15  # seconds per attempt
    
    for attempt in range(max_retries):
        # Debug: Show available serial ports
        ports = list(list_ports.comports())
        print(f"Attempt {attempt + 1}/{max_retries}: Found {len(ports)} serial ports:")
        for p in ports:
            print(f"  - {p.device} (VID:{p.vid}, PID:{p.pid}, desc:{p.description})")
        
        if not ports:
            print(f"No serial ports found, waiting...")
            time.sleep(2)
            continue
        
        print(f"Attempting connection to senxor (timeout: {connection_timeout}s)...")
        try:
            mi48, connected_port, port_names = connect_with_timeout(connection_timeout)
            if mi48 is not None:
                print(f"Successfully connected!")
                break
        except Exception as e:
            print(f"Connection error: {e}")
        
        print(f"Thermal sensor connection failed, retrying...")
        time.sleep(3)
    
    if mi48 is None:
        logger.error("Failed to connect to thermal sensor after retries")
        return False
    
    logger.info(f"Connected to {connected_port}")
    
    # Configure for a single high-quality grab
    mi48.set_fps(10)
    mi48.disable_filter(f1=True, f2=True, f3=True)
    mi48.set_filter_1(85)
    mi48.enable_filter(f1=True)
    
    # Start the stream briefly to populate the buffer
    mi48.start(stream=True, with_header=True)
    time.sleep(1)  # Allow sensor to stabilize

    try:
        # Read a single frame
        data, header = mi48.read()
        
        if data is not None:
            # Format raw data to 80x62 frame
            frame = data_to_frame(data, (80, 62), hflip=False)
            
            # Normalize and filter (Clean up noise)
            par = {'blur_ks': 3, 'd': 5, 'sigmaColor': 27, 'sigmaSpace': 27}
            filt_uint8 = cv_filter(remap(frame), par, use_median=True, use_bilat=True)
            
            # Apply Heatmap
            heatmap = cv.applyColorMap(filt_uint8, cv.COLORMAP_JET)
            
            # Upscale so the image isn't tiny (80x62 -> 640x496)
            heatmap_large = cv.resize(heatmap, (640, 496), interpolation=cv.INTER_CUBIC)
            
            # Save to file
            filename = os.path.join(output_dir, f"thermal_capture_{int(time.time())}.jpg")
            cv.imwrite(filename, heatmap_large)
            logger.info(f"Successfully saved thermal image to {filename}")
            return True
        else:
            logger.error("Failed to receive data from sensor.")
            return False

    finally:
        # Clean shutdown
        mi48.stop()


def main():
    """Main capture sequence: optical first, then thermal"""
    parser = argparse.ArgumentParser(
        description="Capture images from optical and thermal USB cameras"
    )
    parser.add_argument(
        "-d", "--output-dir",
        default=".",
        help="Directory to save captured images (default: current directory)"
    )
    args = parser.parse_args()
    
    # Ensure output directory exists
    output_dir = os.path.abspath(args.output_dir)
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        print(f"Created output directory: {output_dir}")
    
    print(f"Output directory: {output_dir}")
    
    # 1. Disable Thermal, Enable Optical
    print("\n=== Capturing Optical Image ===")
    set_camera_state(THERMAL_USB_ID, "unbind")
    set_camera_state(OPTICAL_USB_ID, "bind")
    time.sleep(2)  # Give the USB device time to initialize
    
    capture_optical_image(output_dir)
    
    # 2. Disable Optical, Enable Thermal
    print("\n=== Capturing Thermal Image ===")
    set_camera_state(THERMAL_USB_ID, "bind")
    set_camera_state(OPTICAL_USB_ID, "unbind")
    print("Waiting 5 seconds for thermal device to enumerate...")
    time.sleep(5)  # Thermal sensor needs more time for serial port to appear
    
    capture_thermal_image(output_dir)
    
    print("\n=== Capture Complete ===")

    print("\n=== Unbinding Cameras ===")
    set_camera_state(THERMAL_USB_ID, "unbind")
    set_camera_state(OPTICAL_USB_ID, "unbind")

if __name__ == "__main__":
    main()