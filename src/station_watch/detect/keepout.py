"""Keep-out detection: is a person inside an active zone, with N-frame persistence.

A keep-out zone is a marker-unit polygon (see :mod:`station_watch.detect.geometry`)
plus an ``active`` flag. Each frame a *backend* reports person boxes --
``detect_people(frame) -> list[(x0, y0, x1, y1, score)]`` -- and :class:`KeepoutTracker`
asks, per zone, whether any box overlaps the zone polygon by more than
``detect.keepout.min_overlap`` (overlap = the fraction of the person box that lies
inside the zone). The default backend is :class:`station_watch.detect.yolox.YoloxBackend`
(YOLOX-Nano, Apache-2.0, run locally through ``cv2.dnn`` -- see that module for the
model, its license, source and SHA-256); the backend is injectable so tests drive
scripted boxes without any model.

Persistence mirrors :class:`~station_watch.detect.positions.PositionTracker`:

* ``person_in_keepout`` only after ``persistence_frames`` consecutive frames read a
  box inside an **active** zone -- it emits a ``keepout_entry`` fault in the Judge.
* ``zone_clear`` only after that many consecutive clear frames.
* ``zone_unknown`` the instant a frame can't be read -- the marker is missing, the
  backend errored, or it produced no output. An unknown frame resets the streak, so
  a zone a person could be standing in is never folded into ``zone_clear``.

An **inactive** zone is always read clear: its person boxes are ignored, so it never
emits ``person_in_keepout`` and never faults. The tracker follows the HF2.3 tracker
protocol -- ``update(frame, frame_id, ts, corners)`` (the Detector finds the marker
once and passes the corners in) and ``unknown_all(frame_id, ts, cause, detail)``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import cv2
import numpy as np

from station_watch.clock import parse_iso
from station_watch.detect.geometry import region_to_pixels
from station_watch.records import Observation, ObservationKind

_METHOD = "keepout:person_box+zone_overlap:v1"
_DEFAULT_MIN_OVERLAP = 0.1

CAUSE_FIDUCIAL_MISSING = "fiducial_missing"
CAUSE_DETECTOR_ERROR = "detector_error"
CAUSE_NO_MODEL_OUTPUT = "no_model_output"

_CONFIRMED = {"occupied": ObservationKind.PERSON_IN_KEEPOUT, "clear": ObservationKind.ZONE_CLEAR}


@dataclass
class _Track:
    bucket: str | None = None  # the current streak: occupied | clear | unknown
    frames: list[int] = field(default_factory=list)
    cause: str | None = None
    state: ObservationKind | None = None  # last emitted state
    score: float = 0.0
    detail: dict = field(default_factory=dict)
    last_emit: datetime | None = None


def _box_overlap(box: tuple, zone_poly: np.ndarray) -> float:
    """Fraction of the person ``box`` (x0, y0, x1, y1, ...) that lies inside ``zone_poly``."""
    x0, y0, x1, y1 = box[:4]
    box_area = max((x1 - x0) * (y1 - y0), 1e-9)
    rect = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=np.float32)
    inter, _ = cv2.intersectConvexConvex(rect, zone_poly.astype(np.float32))
    return float(inter) / box_area


class KeepoutTracker:
    """Persist per-frame person-box readings into person_in_keepout/zone_clear/zone_unknown."""

    def __init__(self, config, run_id: str, backend) -> None:
        self.config = config
        self.run_id = run_id
        self.backend = backend
        self.station_id = config.station_id
        detect = config.detect
        keepout = detect.get("keepout", {})
        self.persistence = keepout.get("persistence_frames", detect["persistence_frames"])
        self.emit_interval = detect["emit_interval_s"]
        self.min_overlap = keepout.get("min_overlap", _DEFAULT_MIN_OVERLAP)
        # Monitor exactly the zones the Judge weighs (``keepout_zones``), reading each
        # one's polygon from its declared ROI. A pre-declared ROI not in that list is
        # not monitored.
        self.rois = {zid: detect["keepout_rois"][zid] for zid in config.keepout_zones}
        self.tracks = {zid: _Track() for zid in self.rois}

    def update(self, frame, frame_id: int, ts: str, corners) -> list[Observation]:
        """Read every zone from this frame (one backend call) and persist the readings."""
        boxes = self._people(frame)
        out = []
        for zid, roi in self.rois.items():
            reading = self._read_zone(roi, boxes, corners)
            observation = self._apply(zid, reading, frame_id, ts)
            if observation is not None:
                out.append(observation)
        return out

    def unknown_all(self, frame_id: int, ts: str, cause: str, detail: str) -> list[Observation]:
        """Force a ``zone_unknown`` reading for every zone (a whole-frame failure)."""
        out = []
        for zid in self.rois:
            reading = ("unknown", cause, 0.0, {"detail": detail})
            observation = self._apply(zid, reading, frame_id, ts)
            if observation is not None:
                out.append(observation)
        return out

    def _people(self, frame):
        """Run the backend once; ``None`` for a raised backend or a model with no output."""
        try:
            return self.backend.detect_people(frame)
        except Exception as exc:  # noqa: BLE001 -- any backend failure reads as unknown
            return ("error", str(exc)[:200])

    def _read_zone(self, roi, boxes, corners) -> tuple:
        """One zone's reading: ``(state, cause, score, detail)`` with no time thresholds."""
        if corners is None:
            return ("unknown", CAUSE_FIDUCIAL_MISSING, 0.0, {})
        if isinstance(boxes, tuple) and boxes and boxes[0] == "error":
            return ("unknown", CAUSE_DETECTOR_ERROR, 0.0, {"detail": boxes[1]})
        if boxes is None:
            return ("unknown", CAUSE_NO_MODEL_OUTPUT, 0.0, {})
        if not roi.get("active", True):
            return ("clear", None, 0.0, {"active": False})
        return self._overlap_reading(roi, boxes, corners)

    def _overlap_reading(self, roi, boxes, corners) -> tuple:
        zone_poly = region_to_pixels(roi["region"], corners)
        best = 0.0
        for box in boxes:
            best = max(best, _box_overlap(box, zone_poly))
        if best > self.min_overlap:
            return ("occupied", None, best, {"overlap": round(best, 4), "boxes": len(boxes)})
        return ("clear", None, best, {"overlap": round(best, 4), "boxes": len(boxes)})

    def _apply(self, zid: str, reading: tuple, frame_id: int, ts: str) -> Observation | None:
        state, cause, score, detail = reading
        track = self.tracks[zid]
        if state == track.bucket:
            track.frames.append(frame_id)
        else:
            track.bucket, track.frames = state, [frame_id]
        track.cause, track.score, track.detail = cause, score, detail
        return self._maybe_emit(zid, track, self._confirm(track, state), frame_id, ts)

    def _confirm(self, track: _Track, state: str) -> ObservationKind | None:
        if state == "unknown":
            return ObservationKind.ZONE_UNKNOWN
        if len(track.frames) >= self.persistence:
            return _CONFIRMED[state]
        return track.state

    def _maybe_emit(self, zid, track, new_state, frame_id, ts) -> Observation | None:
        now = parse_iso(ts)
        if new_state is not None and new_state != track.state:
            track.state, track.last_emit = new_state, now
            return self._observation(zid, track, frame_id, ts)
        if track.state is not None and track.last_emit is not None:
            if (now - track.last_emit).total_seconds() >= self.emit_interval:
                track.last_emit = now
                return self._observation(zid, track, frame_id, ts)
        return None

    def _observation(self, zid, track, frame_id, ts) -> Observation:
        return Observation(
            station_id=self.station_id,
            frame_id=frame_id,
            ts=ts,
            kind=track.state,
            target=zid,
            method=_METHOD,
            confidence_ceiling=round(float(track.score), 4),
            detector_output={
                "cause": track.cause,
                "scores": track.detail,
                "streak_frame_ids": list(track.frames),
            },
            run_id=self.run_id,
        )


__all__ = [
    "KeepoutTracker",
    "CAUSE_FIDUCIAL_MISSING",
    "CAUSE_DETECTOR_ERROR",
    "CAUSE_NO_MODEL_OUTPUT",
]
