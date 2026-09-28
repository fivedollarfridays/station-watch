"""Capture (component 1): stamped frames from a camera or a recorded file."""

from station_watch.capture.capture import Capture
from station_watch.capture.metrics import fingerprint, mean_luma, noise_score
from station_watch.capture.source import CaptureError, FrameSource

__all__ = [
    "Capture",
    "CaptureError",
    "FrameSource",
    "fingerprint",
    "mean_luma",
    "noise_score",
]
