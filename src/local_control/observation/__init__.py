"""Observation package: screen capture, window management, image processing, and observer."""

from local_control.observation.image import (
    compute_dhash,
    compute_downscale_scale,
    downscale_image,
    encode_png,
    frame_to_pillow,
    hamming_distance,
    is_black_frame,
)
from local_control.observation.observer import Observer
from local_control.observation.screen import RawFrame, ScreenCapture, init_dpi_awareness
from local_control.observation.windows import WindowManager

__all__ = [
    "init_dpi_awareness",
    "RawFrame",
    "ScreenCapture",
    "WindowManager",
    "Observer",
    "compute_dhash",
    "hamming_distance",
    "compute_downscale_scale",
    "downscale_image",
    "is_black_frame",
    "encode_png",
    "frame_to_pillow",
]
