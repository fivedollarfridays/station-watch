"""Detect (HF2): read bench state -- rail positions, zones -- from stamped frames.

HF2.1 lays the geometry foundation: :func:`find_marker_corners` and
:func:`region_to_pixels` turn the fiducial-anchored, marker-unit regions in the
station config into pixel polygons every later Detect task reads from.

HF2.2 adds rail-position detection on top: the pure per-frame :func:`read_positions`
and the N-frame :class:`PositionTracker` that persists its readings into
part_present / part_absent / part_unknown Observations.
"""

from station_watch.detect.geometry import find_marker_corners, region_to_pixels
from station_watch.detect.positions import PositionReading, PositionTracker, read_positions

__all__ = [
    "find_marker_corners",
    "region_to_pixels",
    "PositionReading",
    "PositionTracker",
    "read_positions",
]
