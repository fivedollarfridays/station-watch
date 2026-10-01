"""Locate the configured ArUco fiducial in a frame.

The station keeps one printed ArUco marker in the camera's view as an active
canary (K12): if it is not found the view is blind or occluded, and if its
center has moved the camera has been knocked. This module answers only "where is
marker ``marker_id`` right now", returning its pixel center or ``None``; the
blind-condition state machines in :mod:`station_watch.capture.blind` decide what
a missing or moved marker means over a window.
"""

from __future__ import annotations

import numpy as np

from station_watch.detect.geometry import find_marker_corners


def find_marker_center(
    frame: np.ndarray, dictionary_id: str, marker_id: int
) -> tuple[float, float] | None:
    """Pixel center ``(x, y)`` of ``marker_id`` in ``frame``, or ``None`` if absent.

    The marker's four corners (and the marker-unit regions mapped through them)
    live in :mod:`station_watch.detect.geometry`; the center is their centroid.
    """
    corners = find_marker_corners(frame, dictionary_id, marker_id)
    if corners is None:
        return None
    center = corners.mean(axis=0)
    return float(center[0]), float(center[1])
