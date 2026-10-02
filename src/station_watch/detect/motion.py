"""Station-zone motion: frame-to-frame change inside the zone, calibrated to noise.

:class:`MotionTracker` follows the Detector's tracker protocol (``update(frame, frame_id,
ts, corners)`` / ``unknown_all(...)``). Each frame it maps the configured
``station_zone`` polygon into pixels through the marker and compares the zone to the
previous *readable* frame: a pixel counts as changed when its gray-level difference
clears a cutoff set **above the zone's measured noise floor** (HF1's noise-score
idea -- the floor is the running-minimum mean absolute difference, so it settles on
the sensor's own still-scene noise). The zone reads ``motion`` when the changed
fraction clears ``motion_area_frac``, else ``no_motion``, with the same N-frame
persistence and periodic re-emit as :class:`~station_watch.detect.positions.PositionTracker`.

A still-but-live zone (sensor noise only) therefore reads ``no_motion``, never
``motion``. An *unknown* frame -- marker missing, or the zone too dark -- emits
nothing at all (unlike a rail position, there is no ``motion_unknown`` kind): the
reading is dropped and the previous frame forgotten so motion is never computed
across a blind gap, and the Judge's existing blind handling governs instead.
"""

from __future__ import annotations

from datetime import datetime

import cv2
import numpy as np

from station_watch.clock import parse_iso
from station_watch.detect.geometry import region_to_pixels
from station_watch.records import Observation, ObservationKind

_METHOD = "station_zone_motion:frame_diff:v1"
_DEFAULT_MOTION_AREA_FRAC = 0.005  # fraction of the zone that must change to read motion
_NOISE_MULT = 4.0  # per-pixel change cutoff, as a multiple of the measured noise floor
_MIN_CUTOFF = 14.0  # a floor on that cutoff, so a near-frozen scene can't drop it to noise
_CORNER_TOLERANCE_PX = 3.0  # marker jitter up to this still diffs; a bigger move forgets
_CONFIRMED = {"motion": ObservationKind.MOTION, "no_motion": ObservationKind.NO_MOTION}


class MotionTracker:
    """Persist station-zone frame-to-frame change into motion / no_motion Observations."""

    def __init__(self, config, run_id: str) -> None:
        self.config = config
        self.run_id = run_id
        self.station_id = config.station_id
        zone = config.detect["station_zone"]
        self.target = zone["id"]
        self.region = zone["region"]
        self.motion_area_frac = zone.get("motion_area_frac", _DEFAULT_MOTION_AREA_FRAC)
        self.darkness_threshold = config.detect["darkness_threshold"]
        self.persistence = config.detect["persistence_frames"]
        self.emit_interval = config.detect["emit_interval_s"]
        self._prev_crop: np.ndarray | None = None  # previous zone crop, cut under its corners
        self._prev_corners: np.ndarray | None = None  # the corners that crop was cut with
        self._noise_floor: float | None = None
        self._kind: str | None = None  # bucket of the current streak: motion | no_motion
        self._frames: list[int] = []
        self._state: ObservationKind | None = None
        self._last_emit: datetime | None = None
        self._ceiling = 0.0
        self._scores: dict = {}

    def update(self, frame, frame_id: int, ts: str, corners) -> list[Observation]:
        """Read this frame's zone change (vs the previous readable frame) and persist."""
        crop, mask, ceiling = self._zone_crop(frame, corners)
        if crop is None:
            self._forget()  # unknown: drop the reading and the previous frame
            return []
        # Only diff a pair whose crops were cut under (near-)identical corners, so
        # the marker-anchored content is registered: a first frame, a changed crop
        # shape, or a marker that jumped past the tolerance forgets the pair rather
        # than reading the shift as motion (marker jitter must not look like work).
        if (
            self._prev_crop is None
            or self._prev_crop.shape != crop.shape
            or self._corners_moved(corners)
        ):
            self._remember(crop, corners)
            return []
        moved, scores = self._classify(crop, mask)
        self._remember(crop, corners)
        return self._persist("motion" if moved else "no_motion", ceiling, scores, frame_id, ts)

    def unknown_all(self, frame_id: int, ts: str, cause: str, detail: str) -> list[Observation]:
        """A whole-frame Detect failure: emit nothing for the zone, forget the past."""
        self._forget()
        return []

    def _zone_crop(self, frame, corners) -> tuple[np.ndarray | None, np.ndarray | None, float]:
        """Gray crop of the zone (cut to the polygon bbox) and its mask, or ``None``.

        The crop is cut at *this frame's* marker-anchored bounding box, so the zone
        content lands at the same crop coordinates however the marker sits in frame.
        That registration is what keeps a frame diff from reading marker jitter --
        which moves the bbox with the content -- as motion.
        """
        if corners is None:
            return None, None, 0.0
        poly = region_to_pixels(self.region, corners)
        height, width = frame.shape[:2]
        x0, y0 = poly.min(axis=0)
        x1, y1 = poly.max(axis=0)
        if x0 < 0 or y0 < 0 or x1 > width or y1 > height:
            return None, None, 0.0
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        full_mask = np.zeros((height, width), np.uint8)
        cv2.fillPoly(full_mask, [poly.reshape(-1, 1, 2)], 255)
        rows, cols = slice(int(y0), int(y1) + 1), slice(int(x0), int(x1) + 1)
        mask = full_mask[rows, cols].astype(bool)
        crop = gray[rows, cols]
        if not mask.any():
            return None, None, 0.0
        mean_luma = float(crop[mask].mean())
        if mean_luma < self.darkness_threshold:
            return None, None, 0.0
        ceiling = round(mean_luma / (mean_luma + self.darkness_threshold / 2), 4)
        return crop, mask, ceiling

    def _corners_moved(self, corners) -> bool:
        """True when the marker shifted past the tolerance since the stored crop."""
        if self._prev_corners is None:
            return False
        now = np.asarray(corners, dtype=np.float32).reshape(4, 2)
        delta = float(np.linalg.norm(now - self._prev_corners, axis=1).max())
        return delta > _CORNER_TOLERANCE_PX

    def _remember(self, crop: np.ndarray, corners) -> None:
        """Keep this crop and the corners it was cut with as the pair's prev frame."""
        self._prev_crop = crop
        self._prev_corners = np.asarray(corners, dtype=np.float32).reshape(4, 2)

    def _classify(self, crop: np.ndarray, mask: np.ndarray) -> tuple[bool, dict]:
        """Changed-pixel fraction vs a noise-floor-calibrated cutoff; motion if it clears."""
        diff = np.abs(crop.astype(np.int16) - self._prev_crop.astype(np.int16))[mask]
        floor = float(diff.mean())
        if self._noise_floor is None or floor < self._noise_floor:
            self._noise_floor = floor
        cutoff = max(_MIN_CUTOFF, _NOISE_MULT * self._noise_floor)
        fraction = float((diff > cutoff).mean())
        scores = {
            "motion_fraction": round(fraction, 6),
            "per_pixel_cutoff": round(cutoff, 3),
            "noise_floor": round(self._noise_floor, 3),
        }
        return fraction >= self.motion_area_frac, scores

    def _forget(self) -> None:
        self._prev_crop = None
        self._prev_corners = None
        self._kind = None
        self._frames = []

    def _persist(self, bucket, ceiling, scores, frame_id, ts) -> list[Observation]:
        if bucket == self._kind:
            self._frames.append(frame_id)
        else:
            self._kind, self._frames = bucket, [frame_id]
        self._ceiling, self._scores = ceiling, scores
        confirmed = _CONFIRMED[bucket] if len(self._frames) >= self.persistence else self._state
        observation = self._maybe_emit(confirmed, frame_id, ts)
        return [observation] if observation is not None else []

    def _maybe_emit(self, new_state, frame_id, ts) -> Observation | None:
        now = parse_iso(ts)
        if new_state is not None and new_state != self._state:
            self._state, self._last_emit = new_state, now
            return self._observation(frame_id, ts)
        if self._state is not None and self._last_emit is not None:
            if (now - self._last_emit).total_seconds() >= self.emit_interval:
                self._last_emit = now
                return self._observation(frame_id, ts)
        return None

    def _observation(self, frame_id, ts) -> Observation:
        return Observation(
            station_id=self.station_id,
            frame_id=frame_id,
            ts=ts,
            kind=self._state,
            target=self.target,
            method=_METHOD,
            confidence_ceiling=self._ceiling,
            detector_output={**self._scores, "streak_frame_ids": list(self._frames)},
            run_id=self.run_id,
        )


__all__ = ["MotionTracker"]
