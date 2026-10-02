"""The Detector: find the fiducial once per frame, fan the corners out to trackers.

Detect is wired into Capture (:mod:`station_watch.capture.capture`) and the
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

The Detector composes a rail-position tracker (:class:`PositionTracker`) whenever the
config lists rail positions, a station-zone motion tracker
(:class:`MotionTracker`) whenever the zone opts into motion, and a keep-out
tracker (:class:`KeepoutTracker`) whenever the config lists keep-out zones. Each
makes :func:`detect_targets_configured` true, so a station with any of them builds
a Detector and refuses ``--observations`` (one source of observations per run).
"""

from __future__ import annotations

from station_watch.detect.details import DetailTracker, detail_targets_configured
from station_watch.detect.geometry import find_marker_corners
from station_watch.detect.keepout import KeepoutTracker
from station_watch.detect.motion import MotionTracker
from station_watch.detect.positions import PositionTracker
from station_watch.records import Observation

CAUSE_DETECT_ERROR = "detect_error"


def _motion_configured(config) -> bool:
    """True when the ``station_zone`` opts into frame-to-frame motion."""
    return bool(config.detect["station_zone"].get("track_motion"))


def detect_targets_configured(config) -> bool:
    """True when the detect config names targets Detect reads.

    This gates both whether the runner builds a :class:`Detector` and whether
    ``--observations`` conflicts with Detect -- one source of observations per run.
    Rail positions, station-zone motion and keep-out zones
    each count.
    """
    return (
        bool(config.detect["rail_positions"])
        or _motion_configured(config)
        or bool(config.keepout_zones)
        or detail_targets_configured(config)
    )


def build_detector(config, run_id: str, keepout_backend=None) -> Detector | None:
    """A :class:`Detector` for this config, or ``None`` when it names no targets.

    ``keepout_backend`` supplies the person detector for keep-out zones; the runner
    builds and hash-verifies it at startup (K9) and passes it in here.
    """
    if not detect_targets_configured(config):
        return None
    return Detector(config, run_id, keepout_backend=keepout_backend)


class Detector:
    """Find the fiducial once per frame and fan the corners out to every tracker."""

    def __init__(self, config, run_id: str, keepout_backend=None) -> None:
        self._config = config
        self._run_id = run_id
        self._fiducial = config.fiducial
        self._trackers: list = []
        position_tracker = None
        if config.detect["rail_positions"]:
            position_tracker = PositionTracker(config, run_id)
            self.add_tracker(position_tracker)
        if _motion_configured(config):
            self.add_tracker(MotionTracker(config, run_id))
        if config.keepout_zones:
            self.add_tracker(KeepoutTracker(config, run_id, keepout_backend))
        # Details come last so each frame's parent-position confirmed state is already
        # updated when a detail is judged against it.
        if detail_targets_configured(config):
            self.add_tracker(DetailTracker(config, run_id, position_tracker))

    def add_tracker(self, tracker) -> None:
        """Compose one more tracker into the per-frame fan-out."""
        self._trackers.append(tracker)

    def process(self, frame, frame_id: int, ts: str, corners=None) -> list[Observation]:
        """Let every tracker read this frame, finding the marker only if not given.

        Capture finds the corners once per frame and passes them in; a caller
        without them (the physics scripts) leaves ``corners=None`` and the Detector
        searches itself -- so the marker is never detected twice on the live path.
        """
        if corners is None:
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
