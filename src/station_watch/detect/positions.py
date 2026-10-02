"""Rail-position detection: a pure per-frame read, then N-frame persistence.

:func:`read_positions` is a pure function: given a frame it finds the marker and
reads every configured rail position into a :class:`PositionReading` (occupied /
empty / not_seated / unknown). It carries no state and applies no time thresholds.

:class:`PositionTracker` turns a stream of those readings into
:class:`~station_watch.records.Observation` records. It emits ``part_present`` only
after ``persistence_frames`` consecutive visible frames read *occupied and seated*,
``part_absent`` only after N consecutive frames read *empty or not_seated*, and
``part_unknown`` the instant any frame reads unknown -- an unknown frame resets the
streak, so a covered part can never be folded into ``part_present``. It emits on
every confirmed-state change and re-emits the current state every ``emit_interval_s``.

The tracker protocol consumed by the ``Detector`` is ``update(frame, frame_id,
ts, corners)`` -- the ``Detector`` finds the marker once and passes the corners in
-- and ``unknown_all(frame_id, ts, cause, detail)`` for whole-frame failures.
"""

from __future__ import annotations

from dataclasses import dataclass

from station_watch.detect.geometry import find_marker_corners
from station_watch.detect.persistence import PersistenceEngine
from station_watch.detect.regions import CAUSE_FIDUCIAL_MISSING, read_region
from station_watch.records import Observation, ObservationKind

_METHOD = "rail_positions:color_fill+edge_density:v1"

# How each per-frame reading state feeds the persistence streak.
_BUCKET = {"occupied": "present", "empty": "absent", "not_seated": "absent", "unknown": "unknown"}


@dataclass(frozen=True)
class PositionReading:
    """One rail position read from one frame (no time thresholds applied)."""

    target: str
    state: str  # occupied | empty | not_seated | unknown
    cause: str | None  # unknown cause, else None
    confidence_ceiling: float
    scores: dict


def _cause_for(reading: PositionReading) -> str | None:
    """The cause an Observation should carry: the unknown reason, or the absent kind."""
    if reading.state == "unknown":
        return reading.cause
    if reading.state in ("empty", "not_seated"):
        return reading.state
    return None


def _read_all(frame, config, corners) -> list[PositionReading]:
    """Read every configured rail position; all read ``fiducial_missing`` if no marker."""
    readings = []
    for target, region in config.detect["rail_positions"].items():
        if corners is None:
            readings.append(PositionReading(target, "unknown", CAUSE_FIDUCIAL_MISSING, 0.0, {}))
            continue
        state, cause, ceiling, scores = read_region(frame, region, corners, config.detect)
        readings.append(PositionReading(target, state, cause, ceiling, scores))
    return readings


def read_positions(frame, frame_id, ts, config) -> list[PositionReading]:
    """Pure read of every configured rail position (finds the marker itself)."""
    corners = find_marker_corners(
        frame, config.fiducial["dictionary_id"], config.fiducial["marker_id"]
    )
    return _read_all(frame, config, corners)


class PositionTracker:
    """Persist per-frame rail readings into part_present/absent/unknown Observations."""

    def __init__(self, config, run_id: str) -> None:
        self.config = config
        self._engine = PersistenceEngine(config, run_id, config.detect["rail_positions"])

    def update(self, frame, frame_id: int, ts: str, corners) -> list[Observation]:
        """Read every position from this frame (using the given corners) and persist."""
        out = []
        for reading in _read_all(frame, self.config, corners):
            observation = self._apply(reading, frame_id, ts)
            if observation is not None:
                out.append(observation)
        return out

    def unknown_all(self, frame_id: int, ts: str, cause: str, detail: str) -> list[Observation]:
        """Force a ``part_unknown`` reading for every position (whole-frame failure)."""
        out = []
        for target in self._engine.tracks:
            reading = PositionReading(target, "unknown", cause, 0.0, {"detail": detail})
            observation = self._apply(reading, frame_id, ts)
            if observation is not None:
                out.append(observation)
        return out

    def confirmed_state(self, target: str) -> ObservationKind | None:
        """The last confirmed state of a position, read by the detail tracker."""
        return self._engine.confirmed_state(target)

    def _apply(self, reading: PositionReading, frame_id: int, ts: str) -> Observation | None:
        return self._engine.apply(
            reading.target,
            _BUCKET[reading.state],
            _cause_for(reading),
            reading.confidence_ceiling,
            reading.scores,
            _METHOD,
            frame_id,
            ts,
        )


__all__ = ["PositionReading", "PositionTracker", "read_positions"]
