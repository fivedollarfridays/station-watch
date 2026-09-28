"""Locate the configured ArUco fiducial in a frame.

The station keeps one printed ArUco marker in the camera's view as an active
canary (K12): if it is not found the view is blind or occluded, and if its
center has moved the camera has been knocked. This module answers only "where is
marker ``marker_id`` right now", returning its pixel center or ``None``; the
blind-condition state machines in :mod:`station_watch.capture.blind` decide what
a missing or moved marker means over a window.
"""

from __future__ import annotations

from functools import cache

import cv2
import numpy as np


@cache
def _detector(dictionary_id: str) -> cv2.aruco.ArucoDetector:
    """A cached detector for the named predefined dictionary (e.g. DICT_4X4_50)."""
    dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, dictionary_id))
    return cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters())


def find_marker_center(
    frame: np.ndarray, dictionary_id: str, marker_id: int
) -> tuple[float, float] | None:
    """Pixel center ``(x, y)`` of ``marker_id`` in ``frame``, or ``None`` if absent."""
    corners, ids, _ = _detector(dictionary_id).detectMarkers(frame)
    if ids is None:
        return None
    for marker_corners, found_id in zip(corners, ids.flatten(), strict=False):
        if int(found_id) == marker_id:
            center = marker_corners[0].mean(axis=0)
            return float(center[0]), float(center[1])
    return None
