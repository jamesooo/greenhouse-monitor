import sys
import os
import time
import logging
import numpy as np
import cv2 as cv

# Ensure the senxor library is found
sys.path.append("/home/test/myenv/lib/python3.11/site-packages")

from senxor.mi48 import MI48
from senxor.utils import data_to_frame, remap, cv_filter, connect_senxor

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def capture_thermal_image():
    # 1. Connect to the sensor (automatically finds /dev/ttyACM0 or similar)
    mi48, connected_port, port_names = connect_senxor()
    
    logger.info(f"Connected to {connected_port}")
    
    # 2. Configure for a single high-quality grab
    mi48.set_fps(10)
    mi48.disable_filter(f1=True, f2=True, f3=True)
    mi48.set_filter_1(85)
    mi48.enable_filter(f1=True)
    
    # 3. Start the stream briefly to populate the buffer
    mi48.start(stream=True, with_header=True)
    time.sleep(1)  # Allow sensor to stabilize

    try:
        # 4. Read a single frame
        data, header = mi48.read()
        
        if data is not None:
            # Format raw data to 80x62 frame
            frame = data_to_frame(data, (80, 62), hflip=False)
            
            # Normalize and filter (Clean up noise)
            par = {'blur_ks': 3, 'd': 5, 'sigmaColor': 27, 'sigmaSpace': 27}
            filt_uint8 = cv_filter(remap(frame), par, use_median=True, use_bilat=True)
            
            # 5. Apply Heatmap (Since we can't use cv_render headless)
            # COLORMAP_JET or COLORMAP_INFERNO are standard thermal looks
            heatmap = cv.applyColorMap(filt_uint8, cv.COLORMAP_JET)
            
            # 6. Upscale so the image isn't tiny (80x62 -> 640x496)
            heatmap_large = cv.resize(heatmap, (640, 496), interpolation=cv.INTER_CUBIC)
            
            # 7. Save to file
            filename = f"thermal_capture_{int(time.time())}.jpg"
            cv.imwrite(filename, heatmap_large)
            logger.info(f"Successfully saved thermal image to {filename}")
        else:
            logger.error("Failed to receive data from sensor.")

    finally:
        # 8. Clean shutdown
        mi48.stop()

if __name__ == "__main__":
    capture_thermal_image()