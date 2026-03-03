"""
Relative light quantification from an image using OpenCV.

What it does:
- Loads an image from disk
- Converts to grayscale and computes:
  - mean brightness (0..255)
  - normalized mean (0..1)
  - median brightness
  - percentile stats (p10, p90)
  - % of pixels above a "bright" threshold (configurable)
- Optionally focuses on a region-of-interest (ROI)
- Optionally applies gamma correction to approximate perceptual response

This is intended for *relative* measurements (e.g., compare frames over time).
For alerting later, you can threshold on normalized_mean or bright_pixel_ratio.

Usage:
  python light_meter.py --image path/to/photo.jpg
  python light_meter.py --image photo.jpg --roi 100 50 400 300
  python light_meter.py --image photo.jpg --downscale 1024 --gamma 2.2
  python light_meter.py --image photo.jpg --bright-thresh 220 --blur 3 --show

Dependencies:
  pip install opencv-python numpy
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict, dataclass
from typing import Optional, Tuple


import cv2
import numpy as np


@dataclass
class LightMetrics:
    mean: float                 # 0..255
    normalized_mean: float      # 0..1
    median: float               # 0..255
    p10: float                  # 0..255
    p90: float                  # 0..255
    std: float                  # 0..255
    min: float                  # 0..255
    max: float                  # 0..255
    bright_pixel_ratio: float   # 0..1, fraction above bright threshold
    dark_pixel_ratio: float     # 0..1, fraction below dark threshold
    used_roi: Optional[Tuple[int, int, int, int]]  # x, y, w, h


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Relative light quantification for a photo (OpenCV).")
    p.add_argument("--image", required=True, help="Path to image file.")
    p.add_argument(
        "--roi",
        nargs=4,
        type=int,
        metavar=("X", "Y", "W", "H"),
        help="Optional ROI rectangle in pixels (x y w h).",
    )
    p.add_argument(
        "--downscale",
        type=int,
        default=0,
        help="Optional max dimension for speed (e.g., 1024). 0 disables.",
    )
    p.add_argument(
        "--gamma",
        type=float,
        default=0.0,
        help="Optional gamma correction (e.g., 2.2). 0 disables.",
    )
    p.add_argument(
        "--blur",
        type=int,
        default=0,
        help="Optional median blur kernel size (odd int, e.g., 3 or 5). 0 disables.",
    )
    p.add_argument(
        "--bright-thresh",
        type=int,
        default=220,
        help="Brightness threshold (0..255) for 'bright_pixel_ratio'.",
    )
    p.add_argument(
        "--dark-thresh",
        type=int,
        default=30,
        help="Brightness threshold (0..255) for 'dark_pixel_ratio'.",
    )
    p.add_argument("--show", action="store_true", help="Show visualization windows.")
    return p.parse_args()


def imread_bgr(path: str) -> np.ndarray:
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"Could not read image: {path}")
    return img


def maybe_downscale(img: np.ndarray, max_dim: int) -> np.ndarray:
    if max_dim <= 0:
        return img
    h, w = img.shape[:2]
    scale = max(h, w) / float(max_dim)
    if scale <= 1.0:
        return img
    new_w = int(round(w / scale))
    new_h = int(round(h / scale))
    return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)


def clip_roi(x: int, y: int, w: int, h: int, img_w: int, img_h: int) -> Tuple[int, int, int, int]:
    x = max(0, min(x, img_w - 1))
    y = max(0, min(y, img_h - 1))
    w = max(1, w)
    h = max(1, h)
    w = min(w, img_w - x)
    h = min(h, img_h - y)
    return x, y, w, h


def apply_gamma(gray_u8: np.ndarray, gamma: float) -> np.ndarray:
    """
    Apply gamma correction to grayscale.
    gamma > 1 brightens mid-tones less (more "perceptual"), gamma < 1 brightens.
    We use: out = (in/255)^(1/gamma) * 255
    """
    if gamma <= 0:
        return gray_u8
    gray = gray_u8.astype(np.float32) / 255.0
    inv_g = 1.0 / gamma
    corrected = np.power(gray, inv_g) * 255.0
    return np.clip(corrected, 0, 255).astype(np.uint8)


def compute_metrics(gray_u8: np.ndarray, bright_thresh: int, dark_thresh: int, used_roi) -> LightMetrics:
    g = gray_u8
    mean = float(np.mean(g))
    median = float(np.median(g))
    p10 = float(np.percentile(g, 10))
    p90 = float(np.percentile(g, 90))
    std = float(np.std(g))
    mn = float(np.min(g))
    mx = float(np.max(g))

    bright_thresh = int(np.clip(bright_thresh, 0, 255))
    dark_thresh = int(np.clip(dark_thresh, 0, 255))

    bright_ratio = float(np.mean(g >= bright_thresh))
    dark_ratio = float(np.mean(g <= dark_thresh))

    return LightMetrics(
        mean=mean,
        normalized_mean=mean / 255.0,
        median=median,
        p10=p10,
        p90=p90,
        std=std,
        min=mn,
        max=mx,
        bright_pixel_ratio=bright_ratio,
        dark_pixel_ratio=dark_ratio,
        used_roi=used_roi,
    )


def main() -> int:
    args = parse_args()


    bgr = imread_bgr(args.image)
    bgr = maybe_downscale(bgr, args.downscale)

    h, w = bgr.shape[:2]
    used_roi = None

    if args.roi:
        x, y, rw, rh = clip_roi(args.roi[0], args.roi[1], args.roi[2], args.roi[3], w, h)
        used_roi = (x, y, rw, rh)
        bgr_roi = bgr[y : y + rh, x : x + rw]
    else:
        bgr_roi = bgr

    gray = cv2.cvtColor(bgr_roi, cv2.COLOR_BGR2GRAY)

    if args.blur:
        k = int(args.blur)
        if k % 2 == 0 or k < 1:
            print("--blur must be an odd integer (e.g., 3, 5).", file=sys.stderr)
            return 2
        gray = cv2.medianBlur(gray, k)

    gray = apply_gamma(gray, args.gamma)

    metrics = compute_metrics(gray, args.bright_thresh, args.dark_thresh, used_roi)

    # Print results (easy to parse later)
    for k, v in asdict(metrics).items():
        print(f"{k}: {v}")

    if args.show:
        vis = bgr.copy()
        if used_roi is not None:
            x, y, rw, rh = used_roi
            cv2.rectangle(vis, (x, y), (x + rw, y + rh), (0, 255, 255), 2)

        # Heatmap visualization of brightness for the ROI
        heat = cv2.applyColorMap(gray, cv2.COLORMAP_INFERNO)

        cv2.imshow("image (with ROI if set)", vis)
        cv2.imshow("grayscale (ROI)", gray)
        cv2.imshow("brightness heatmap (ROI)", heat)
        cv2.waitKey(0)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())