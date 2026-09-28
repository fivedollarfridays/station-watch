"""Per-frame metrics derived from a decoded BGR frame.

These are the numbers a :class:`~station_watch.records.FrameRecord` carries so a
later component (the blind monitors) can tell a live camera from a blind one without holding
the pixels: a raw-bytes ``fingerprint`` (frozen sensors repeat it), ``mean_luma``
(a dark sensor drops it), and ``noise_score`` -- the mean absolute per-pixel
difference from the previous frame, which is positive for a live still scene and
exactly zero across byte-identical frames.
"""

from __future__ import annotations

import hashlib

import cv2
import numpy as np


def fingerprint(frame: np.ndarray) -> str:
    """SHA-256 hex digest of the frame's raw bytes."""
    return hashlib.sha256(frame.tobytes()).hexdigest()


def mean_luma(frame: np.ndarray) -> float:
    """Mean luminance of the frame (0..255), via BGR->gray."""
    return float(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).mean())


def noise_score(frame: np.ndarray, prev: np.ndarray | None) -> float:
    """Mean absolute per-pixel difference from ``prev`` (0.0 if no predecessor)."""
    if prev is None:
        return 0.0
    diff = np.abs(frame.astype(np.int16) - prev.astype(np.int16))
    return float(diff.mean())
