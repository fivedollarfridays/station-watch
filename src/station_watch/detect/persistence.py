"""Shared N-frame persistence for part_present / part_absent / part_unknown tracks.

The rail-position tracker (:class:`~station_watch.detect.positions.PositionTracker`)
and the detail tracker (:class:`~station_watch.detect.details.DetailTracker`) both
turn a stream of per-frame readings into :class:`~station_watch.records.Observation`
records with the *same* rule: a confirmed state change is emitted only after
``persistence_frames`` consecutive readings land in one bucket (``unknown`` confirms
instantly, resetting the streak so a covered part can never be folded into present),
and the current confirmed state is re-emitted every ``emit_interval_s``.

:class:`PersistenceEngine` holds that one machinery so neither tracker copies it. A
caller feeds a reading's bucket (``present`` / ``absent`` / ``unknown``), cause,
confidence ceiling, scores and method string through :meth:`apply`; the engine owns
the streak, the confirmed state and the emission. :meth:`confirmed_state` exposes the
last confirmed :class:`~station_watch.records.ObservationKind` per target, which the
detail tracker reads to gate a child on its parent position.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from station_watch.clock import parse_iso
from station_watch.records import Observation, ObservationKind

_CONFIRMED = {"present": ObservationKind.PART_PRESENT, "absent": ObservationKind.PART_ABSENT}


@dataclass
class _Track:
    kind: str | None = None  # bucket of the current streak: present | absent | unknown
    frames: list[int] = field(default_factory=list)
    cause: str | None = None
    state: ObservationKind | None = None  # last confirmed/emitted state
    ceiling: float = 0.0
    scores: dict = field(default_factory=dict)
    method: str = ""
    last_emit: datetime | None = None


class PersistenceEngine:
    """Per-target N-frame persistence and periodic re-emit, shared by the trackers."""

    def __init__(self, config, run_id: str, targets) -> None:
        self.station_id = config.station_id
        self.run_id = run_id
        self.persistence = config.detect["persistence_frames"]
        self.emit_interval = config.detect["emit_interval_s"]
        self.tracks = {target: _Track() for target in targets}

    def apply(
        self,
        target: str,
        bucket: str,
        cause: str | None,
        ceiling: float,
        scores: dict,
        method: str,
        frame_id: int,
        ts: str,
    ) -> Observation | None:
        """Fold one reading into ``target``'s streak and emit on change or interval."""
        track = self.tracks[target]
        if bucket == track.kind:
            track.frames.append(frame_id)
        else:
            track.kind, track.frames = bucket, [frame_id]
        track.cause, track.ceiling, track.scores, track.method = cause, ceiling, scores, method
        return self._maybe_emit(target, track, self._confirm(track, bucket), frame_id, ts)

    def confirmed_state(self, target: str) -> ObservationKind | None:
        """The last confirmed state of ``target`` (``None`` until confirmed, or if untracked)."""
        track = self.tracks.get(target)
        return track.state if track is not None else None

    def _confirm(self, track: _Track, bucket: str) -> ObservationKind | None:
        if bucket == "unknown":
            return ObservationKind.PART_UNKNOWN
        if len(track.frames) >= self.persistence:
            return _CONFIRMED[bucket]
        return track.state

    def _maybe_emit(self, target, track, new_state, frame_id, ts) -> Observation | None:
        now = parse_iso(ts)
        if new_state is not None and new_state != track.state:
            track.state, track.last_emit = new_state, now
            return self._observation(target, track, frame_id, ts)
        if track.state is not None and track.last_emit is not None:
            if (now - track.last_emit).total_seconds() >= self.emit_interval:
                track.last_emit = now
                return self._observation(target, track, frame_id, ts)
        return None

    def _observation(self, target, track, frame_id, ts) -> Observation:
        return Observation(
            station_id=self.station_id,
            frame_id=frame_id,
            ts=ts,
            kind=track.state,
            target=target,
            method=track.method,
            confidence_ceiling=track.ceiling,
            detector_output={
                "cause": track.cause,
                "scores": track.scores,
                "streak_frame_ids": list(track.frames),
            },
            run_id=self.run_id,
        )


__all__ = ["PersistenceEngine"]
