"""Detect (HF2): read bench state -- rail positions, zones -- from stamped frames.

HF2.1 lays the geometry foundation: :func:`find_marker_corners` and
:func:`region_to_pixels` turn the fiducial-anchored, marker-unit regions in the
station config into pixel polygons every later Detect task reads from.
"""

from station_watch.detect.geometry import find_marker_corners, region_to_pixels

__all__ = ["find_marker_corners", "region_to_pixels"]
