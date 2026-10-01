"""Geometry tests: locate the fiducial's four corners and map marker-unit regions.

``find_marker_corners`` answers "where are the marker's four corners right now"
(the basis for every rail-position / zone region), and ``region_to_pixels`` turns
a region expressed in *marker units* (relative to the marker's center and axes,
1.0 == one marker side) into a pixel polygon through those corners. A region is
anchored to the marker, so the same config region lands correctly whatever
position and scale the marker appears at -- which is what lets HF2.2+ read fixed
bench positions from a camera that was bumped or zoomed.
"""

from __future__ import annotations

import cv2
import numpy as np

from station_watch.detect.geometry import find_marker_corners, region_to_pixels

_DICT = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)


def _frame_with_marker(size, marker_xy, marker_px):
    """A light gray frame with a DICT_4X4_50 id-0 marker drawn at ``marker_xy``."""
    frame = np.full((size[1], size[0], 3), 200, np.uint8)
    tile = cv2.cvtColor(cv2.aruco.generateImageMarker(_DICT, 0, marker_px), cv2.COLOR_GRAY2BGR)
    x, y = marker_xy
    frame[y : y + marker_px, x : x + marker_px] = tile
    return frame


def test_find_marker_corners_returns_four_points_float32():
    frame = _frame_with_marker((320, 240), (100, 80), 60)

    corners = find_marker_corners(frame, "DICT_4X4_50", 0)

    assert corners is not None
    assert corners.shape == (4, 2)
    assert corners.dtype == np.float32
    # Corners cluster around the drawn tile [100,160] x [80,140].
    assert corners[:, 0].min() > 90 and corners[:, 0].max() < 170
    assert corners[:, 1].min() > 70 and corners[:, 1].max() < 150


def test_find_marker_corners_absent_marker_is_none():
    blank = np.full((240, 320, 3), 200, np.uint8)
    assert find_marker_corners(blank, "DICT_4X4_50", 0) is None


def test_find_marker_corners_wrong_id_is_none():
    frame = _frame_with_marker((320, 240), (100, 80), 60)
    assert find_marker_corners(frame, "DICT_4X4_50", 7) is None


def test_region_to_pixels_none_corners_is_none():
    region = [[-1.0, -1.0], [1.0, -1.0], [1.0, 1.0], [-1.0, 1.0]]
    assert region_to_pixels(region, None) is None


def _expect_px(center, side, u, v):
    """Axis-aligned expectation: center + u/v marker-sides along x/y."""
    return (center[0] + u * side, center[1] + v * side)


def _assert_region_maps(marker_xy, marker_px):
    frame = _frame_with_marker((480, 360), marker_xy, marker_px)
    corners = find_marker_corners(frame, "DICT_4X4_50", 0)
    assert corners is not None

    center = (marker_xy[0] + marker_px / 2.0, marker_xy[1] + marker_px / 2.0)
    # A region one marker-side wide, half tall, offset one side to the right of center.
    region = [[0.5, -0.5], [1.5, -0.5], [1.5, 0.5], [0.5, 0.5]]
    pixels = region_to_pixels(region, corners)

    assert pixels.shape == (4, 2)
    assert pixels.dtype == np.int32
    expected = np.array([_expect_px(center, marker_px, u, v) for u, v in region], dtype=np.float32)
    # Within a couple of pixels of the geometric expectation (ArUco is subpixel).
    assert np.max(np.abs(pixels.astype(np.float32) - expected)) <= 2.0


def test_region_to_pixels_maps_a_small_near_marker():
    _assert_region_maps((60, 50), 40)


def test_region_to_pixels_maps_a_larger_shifted_marker():
    # A different position AND scale: the mapping tracks the marker, not fixed px.
    _assert_region_maps((260, 180), 90)
