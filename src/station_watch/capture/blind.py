"""Blind-condition state machines: turn per-frame judgements into ``BlindRecord``s.

Capture must never go silent (README K1/K4): when the source fails it says so
with a record and evidence instead of emitting nothing. This module owns that
promise. A :class:`BlindTracker` wraps one :mod:`monitors` monitor and the shared
recovery rule -- a condition *opens* when the monitor trips and only *clears*
after ``recover_good_frames`` consecutive good frames (K11: a fix is verified by
re-observing, never by a claim) -- and both opening and clearing write a
``BlindRecord`` so the Log alone distinguishes an active blind condition from a
past one.

``disconnected`` is special: no frame at all is arriving, so it cannot be judged
from a frame. :class:`DisconnectedTracker` is driven by a liveness timer instead,
and :class:`BlindWatch` fans a frame out to every per-frame tracker while the
Capture loop's timer thread calls :meth:`BlindWatch.check_liveness` on its own.
Every emission is serialized under one lock, so the timer thread and the frame
loop never build a record concurrently, and each lands through the same Log.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from station_watch.capture.monitors import (
    DarkMonitor,
    FiducialMissingMonitor,
    FrozenMonitor,
    Sample,
    ViewShiftedMonitor,
)
from station_watch.clock import utc_now_iso
from station_watch.records import BlindReason, BlindRecord, BlindState

_DEFAULT_FIDUCIAL_WINDOW_S = 1.0

Emit = Callable[[BlindReason, BlindState, dict, "int | None"], None]


@dataclass(frozen=True)
class BlindThresholds:
    """The blind-detection knobs, sourced from a :class:`StationConfig`."""

    liveness_window_s: float
    dark_luma_threshold: float
    dark_window_s: float
    frozen_frames: int
    recover_good_frames: int
    fiducial: dict

    @classmethod
    def from_station_config(cls, config) -> BlindThresholds:
        return cls(
            liveness_window_s=config.liveness_window_s,
            dark_luma_threshold=config.dark_luma_threshold,
            dark_window_s=config.dark_window_s,
            frozen_frames=config.frozen_frames,
            recover_good_frames=config.recover_good_frames,
            fiducial=config.fiducial,
        )


class BlindTracker:
    """One monitor plus the open/clear recovery rule shared by every reason."""

    def __init__(self, reason: BlindReason, monitor, recover_good_frames: int, emit: Emit) -> None:
        self._reason = reason
        self._monitor = monitor
        self._recover = recover_good_frames
        self._emit = emit
        self._open = False
        self._good_run = 0
        self._last_good_frame_id: int | None = None

    def observe(self, sample: Sample, frame_id: int) -> None:
        tripped, bad, evidence = self._monitor.evaluate(sample)
        if not self._open:
            if not bad:
                self._last_good_frame_id = frame_id
            if tripped:
                self._open = True
                self._good_run = 0
                self._emit(self._reason, BlindState.OPENED, evidence, self._last_good_frame_id)
            return
        if bad:
            self._good_run = 0
            return
        self._good_run += 1
        if self._good_run >= self._recover:
            self._open = False
            self._emit(self._reason, BlindState.CLEARED, evidence, frame_id)


class DisconnectedTracker:
    """Opened by the liveness timer (no fresh frame); cleared when frames resume."""

    def __init__(self, window_s: float, recover_good_frames: int, emit: Emit) -> None:
        self._window_s = window_s
        self._recover = recover_good_frames
        self._emit = emit
        self._open = False
        self._good_run = 0
        self._last_good_frame_id: int | None = None

    def frame_arrived(self, frame_id: int) -> None:
        if not self._open:
            self._last_good_frame_id = frame_id
            return
        self._good_run += 1
        if self._good_run >= self._recover:
            self._open = False
            self._emit(
                BlindReason.DISCONNECTED,
                BlindState.CLEARED,
                {"recovered_after_frames": self._good_run},
                frame_id,
            )

    def check(self, silent_s: float) -> None:
        if self._open or silent_s < self._window_s:
            return
        self._open = True
        self._good_run = 0
        self._emit(
            BlindReason.DISCONNECTED,
            BlindState.OPENED,
            {"silent_seconds": silent_s, "liveness_window_s": self._window_s},
            self._last_good_frame_id,
        )


class BlindWatch:
    """Fans each frame to every per-frame tracker; the timer drives disconnected."""

    def __init__(
        self,
        thresholds: BlindThresholds,
        *,
        station_id: str,
        camera_id: str,
        run_id: str,
        sink: Callable[[BlindRecord], None],
        clock: Callable[[], str] = utc_now_iso,
    ) -> None:
        self._station_id = station_id
        self._camera_id = camera_id
        self._run_id = run_id
        self._sink = sink
        self._clock = clock
        self._lock = threading.Lock()
        self._seq = 0
        self._last_frame_mono: float | None = None
        fid: dict[str, Any] = thresholds.fiducial
        window_s = fid.get("window_s", _DEFAULT_FIDUCIAL_WINDOW_S)
        self._trackers = [
            BlindTracker(
                BlindReason.FROZEN,
                FrozenMonitor(thresholds.frozen_frames),
                thresholds.recover_good_frames,
                self._emit,
            ),
            BlindTracker(
                BlindReason.DARK,
                DarkMonitor(thresholds.dark_luma_threshold, thresholds.dark_window_s),
                thresholds.recover_good_frames,
                self._emit,
            ),
            BlindTracker(
                BlindReason.FIDUCIAL_MISSING,
                FiducialMissingMonitor(window_s),
                thresholds.recover_good_frames,
                self._emit,
            ),
            BlindTracker(
                BlindReason.VIEW_SHIFTED,
                ViewShiftedMonitor(fid["expected_center_px"], fid["tolerance_px"], window_s),
                thresholds.recover_good_frames,
                self._emit,
            ),
        ]
        self._disconnected = DisconnectedTracker(
            thresholds.liveness_window_s, thresholds.recover_good_frames, self._emit
        )

    def start(self, mono: float) -> None:
        """Seed the liveness clock at loop start so a hang before frame 0 still trips."""
        with self._lock:
            self._last_frame_mono = mono

    def observe_frame(self, record, marker_center: tuple[float, float] | None) -> None:
        sample = Sample(
            fingerprint=record.fingerprint,
            mean_luma=record.mean_luma,
            mono=record.capture_mono,
            marker_center=marker_center,
        )
        with self._lock:
            self._last_frame_mono = record.capture_mono
            self._disconnected.frame_arrived(record.frame_id)
            for tracker in self._trackers:
                tracker.observe(sample, record.frame_id)

    def check_liveness(self, now_mono: float) -> None:
        with self._lock:
            if self._last_frame_mono is None:
                return
            self._disconnected.check(now_mono - self._last_frame_mono)

    def _emit(
        self, reason: BlindReason, state: BlindState, evidence: dict, last_good: int | None
    ) -> None:
        # Always called with self._lock held (from observe_frame / check_liveness).
        self._seq += 1
        self._sink(
            BlindRecord(
                station_id=self._station_id,
                camera_id=self._camera_id,
                ts=self._clock(),
                reason=reason,
                evidence=evidence,
                last_good_frame_id=last_good,
                state=state,
                seq=self._seq,
                run_id=self._run_id,
            )
        )


__all__ = ["BlindThresholds", "BlindTracker", "DisconnectedTracker", "BlindWatch"]
