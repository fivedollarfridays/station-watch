"""Fiducial-anchored geometry: marker corners and marker-unit regions in pixels.

Detect reads fixed bench locations -- rail positions, the station zone, keep-out
zones -- that are defined once in *marker units*: a quadrilateral relative to the
fiducial marker's center and axes, where ``1.0`` is one marker side. Anchoring to
the marker (not to fixed pixels) means a region lands on the same real-world spot
however the marker appears in frame, so a bumped or re-zoomed camera does not
silently move every ROI.

``find_marker_corners`` returns the marker's four detected corners (ArUco order:
top-left, top-right, bottom-right, bottom-left) or ``None`` when the marker is not
in view. ``region_to_pixels`` maps a marker-unit region through those corners to an
integer pixel polygon, and returns ``None`` when the corners are ``None`` so a
blind frame propagates cleanly instead of raising.
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


def find_marker_corners(frame: np.ndarray, dictionary_id: str, marker_id: int) -> np.ndarray | None:
    """The four corners of ``marker_id`` in ``frame`` (shape ``(4, 2)`` float32), or ``None``.

    Points are in ArUco order (top-left, top-right, bottom-right, bottom-left).
    """
    corners, ids, _ = _detector(dictionary_id).detectMarkers(frame)
    if ids is None:
        return None
    for marker_corners, found_id in zip(corners, ids.flatten(), strict=False):
        if int(found_id) == marker_id:
            return np.asarray(marker_corners, dtype=np.float32).reshape(4, 2)
    return None


def marker_center(corners: np.ndarray | None) -> tuple[float, float] | None:
    """Pixel center ``(x, y)`` of a marker from its four ``corners``, or ``None``.

    The center is the centroid of the corners :func:`find_marker_corners` returns,
    so Capture can find the marker once per frame and derive both its center (for
    the blind-condition watch) and the corners (for Detect) from one search.
    """
    if corners is None:
        return None
    center = np.asarray(corners, dtype=np.float32).reshape(4, 2).mean(axis=0)
    return float(center[0]), float(center[1])


def region_to_pixels(region, corners: np.ndarray | None) -> np.ndarray | None:
    """Map a marker-unit ``region`` to a pixel polygon through the marker ``corners``.

    ``region`` is a sequence of ``(u, v)`` points in marker units relative to the
    marker's center: ``u`` along the marker's x-axis (top edge), ``v`` along its
    y-axis (left edge), where ``1.0`` is one marker side. Returns an ``int32``
    array of the same point count, or ``None`` when ``corners`` is ``None``.
    """
    if corners is None:
        return None
    corners = np.asarray(corners, dtype=np.float32).reshape(4, 2)
    top_left, top_right, _bottom_right, bottom_left = corners
    center = corners.mean(axis=0)
    x_axis = top_right - top_left  # one marker side along the marker's x-axis
    y_axis = bottom_left - top_left  # one marker side along the marker's y-axis
    points = np.asarray(region, dtype=np.float32).reshape(-1, 2)
    pixels = center + points[:, 0:1] * x_axis + points[:, 1:2] * y_axis
    return np.rint(pixels).astype(np.int32)


__all__ = ["find_marker_corners", "marker_center", "region_to_pixels"]
