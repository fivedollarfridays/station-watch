"""Thumbnail geometry and JPEG encoding for the evidence store.

Split out of :mod:`station_watch.evidence` so the store module stays about the
queue/retention machinery. These are the pure, side-effect-free pixel helpers the
writer thread calls: downscale a frame to a bounded width (never upscaling),
encode it as JPEG, and map the marker corners into the thumbnail's pixel space.
"""

from __future__ import annotations

import cv2
import numpy as np


def encode_jpeg(thumbnail: np.ndarray) -> bytes:
    """JPEG-encode an already-downscaled BGR thumbnail."""
    ok, buffer = cv2.imencode(".jpg", thumbnail)
    if not ok:
        raise ValueError("cv2.imencode failed to encode evidence thumbnail")
    return buffer.tobytes()


def downscale(frame: np.ndarray, thumb_width: int) -> tuple[np.ndarray, float]:
    """Downscale ``frame`` to at most ``thumb_width`` wide; never upscale.

    Returns the thumbnail and the scale (thumbnail px / full-frame px) so a marker
    polygon in full-frame pixels maps into the thumbnail by multiplication.
    """
    height, width = frame.shape[:2]
    if width <= thumb_width:
        return frame, 1.0
    scale = thumb_width / width
    target = (thumb_width, max(1, round(height * scale)))
    return cv2.resize(frame, target, interpolation=cv2.INTER_AREA), scale


def scaled_corners(corners, scale: float) -> list[list[float]] | None:
    """The four marker corners scaled into thumbnail pixels, or ``None``."""
    if corners is None:
        return None
    points = np.asarray(corners, dtype=np.float32).reshape(4, 2) * scale
    return [[float(x), float(y)] for x, y in points]


__all__ = ["encode_jpeg", "downscale", "scaled_corners"]
