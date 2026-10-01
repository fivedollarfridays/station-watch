"""Detect (HF2): read bench state -- rail positions, zones -- from stamped frames.

The geometry foundation: :func:`find_marker_corners` and
:func:`region_to_pixels` turn the fiducial-anchored, marker-unit regions in the
station config into pixel polygons every later Detect task reads from.

Rail-position detection sits on top: the pure per-frame :func:`read_positions`
and the N-frame :class:`PositionTracker` that persists its readings into
part_present / part_absent / part_unknown Observations.

Those trackers compose behind :class:`Detector`, which finds the fiducial
once per frame and fans the corners out, and which Capture and the runner wire in.

Keep-out: :class:`KeepoutTracker` reads person boxes from a backend --
the default :class:`~station_watch.detect.yolox.YoloxBackend` (YOLOX-Nano,
Apache-2.0, run locally via ``cv2.dnn``) -- and persists per-zone readings into
``person_in_keepout`` / ``zone_clear`` / ``zone_unknown`` Observations.
"""

from station_watch.detect.detector import (
    CAUSE_DETECT_ERROR,
    Detector,
    build_detector,
    detect_targets_configured,
)
from station_watch.detect.fetch import fetch_model
from station_watch.detect.geometry import find_marker_corners, region_to_pixels
from station_watch.detect.keepout import KeepoutTracker
from station_watch.detect.motion import MotionTracker
from station_watch.detect.positions import PositionReading, PositionTracker, read_positions
from station_watch.detect.yolox import YoloxBackend, verify_weights

__all__ = [
    "find_marker_corners",
    "region_to_pixels",
    "PositionReading",
    "PositionTracker",
    "read_positions",
    "MotionTracker",
    "KeepoutTracker",
    "YoloxBackend",
    "fetch_model",
    "verify_weights",
    "Detector",
    "build_detector",
    "detect_targets_configured",
    "CAUSE_DETECT_ERROR",
]
