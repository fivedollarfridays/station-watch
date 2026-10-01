"""The Detector: find the fiducial once per frame, fan the corners out to trackers.

HF2.3 wires Detect into Capture (:mod:`station_watch.capture.capture`) and the
runner. ``Detector`` composes the per-target trackers that share one protocol --
``update(frame, frame_id, ts, corners) -> list[Observation]`` and
``unknown_all(frame_id, ts, cause, detail) -> list[Observation]`` -- so the marker
is found once per frame (the expensive step) and the same corners are handed to
every tracker.

``process`` is the per-frame entry Capture calls; ``unknown_all`` is how Capture
turns a Detect exception into a ``part_unknown`` reading for every target (cause
``detect_error``) rather than a false ``disconnected`` camera. ``build_detector``
returns ``None`` when the config names no Detect targets, so a camera-health-only
station runs exactly as it did before Detect.

HF2.3 composes a rail-position tracker (:class:`PositionTracker`) whenever the
config lists rail positions; station-zone and keep-out trackers join the same
fan-out when HF2.6 lands, and :func:`detect_targets_configured` grows with them.
"""

from __future__ import annotations

from station_watch.detect.geometry import find_marker_corners
from station_watch.detect.positions import PositionTracker
from station_watch.records import Observation

CAUSE_DETECT_ERROR = "detect_error"


def detect_targets_configured(config) -> bool:
    """True when the detect config names targets Detect reads (HF2.3: rail positions).

    This gates both whether the runner builds a :class:`Detector` and whether
    ``--observations`` conflicts with Detect -- one source of observations per run.
    Station-zone and keep-out trackers (HF2.6) extend this predicate.
    """
    return bool(config.detect["rail_positions"])


def build_detector(config, run_id: str) -> Detector | None:
    """A :class:`Detector` for this config, or ``None`` when it names no targets."""
    if not detect_targets_configured(config):
        return None
    return Detector(config, run_id)


class Detector:
    """Find the fiducial once per frame and fan the corners out to every tracker."""

    def __init__(self, config, run_id: str) -> None:
        self._config = config
        self._run_id = run_id
        self._fiducial = config.fiducial
        self._trackers: list = []
        if config.detect["rail_positions"]:
            self.add_tracker(PositionTracker(config, run_id))

    def add_tracker(self, tracker) -> None:
        """Compose one more tracker into the per-frame fan-out."""
        self._trackers.append(tracker)

    def process(self, frame, frame_id: int, ts: str) -> list[Observation]:
        """Find the marker once and let every tracker read this frame."""
        corners = find_marker_corners(
            frame, self._fiducial["dictionary_id"], self._fiducial["marker_id"]
        )
        out: list[Observation] = []
        for tracker in self._trackers:
            out.extend(tracker.update(frame, frame_id, ts, corners))
        return out

    def unknown_all(self, frame_id: int, ts: str, cause: str, detail: str) -> list[Observation]:
        """Force an unknown reading for every target (a whole-frame Detect failure)."""
        out: list[Observation] = []
        for tracker in self._trackers:
            out.extend(tracker.unknown_all(frame_id, ts, cause, detail))
        return out


__all__ = ["Detector", "build_detector", "detect_targets_configured", "CAUSE_DETECT_ERROR"]
