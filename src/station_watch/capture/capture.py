"""The Capture loop: raw frames in, stamped ``FrameRecord``s out.

Each frame read from a :class:`~station_watch.capture.source.FrameSource` becomes
a :class:`~station_watch.records.FrameRecord` carrying a monotonic per-camera
``frame_id``, both wall (``ts``) and monotonic (``capture_mono``) capture times,
and the three metrics (fingerprint, mean luma, noise vs. the previous frame).

A file source is paced at its native fps so a recording replays in real time; a
``speed`` factor (>1 faster, for tests) scales that. A live device is left to the
hardware clock. ``capture_mono`` is guaranteed strictly increasing -- nudged
forward by a nanosecond if two reads land on the same OS clock tick -- so it is a
sound ordering key even when frames arrive faster than the clock's resolution.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator

import numpy as np

from station_watch.capture.metrics import fingerprint, mean_luma, noise_score
from station_watch.capture.source import FrameSource
from station_watch.clock import utc_now_iso
from station_watch.records import FrameRecord

_MONO_EPSILON = 1e-9


class Capture:
    """Turns a :class:`FrameSource` into a stream of ``FrameRecord``s."""

    def __init__(
        self,
        source: FrameSource,
        *,
        station_id: str,
        camera_id: str,
        run_id: str,
        speed: float = 1.0,
        fps: float | None = None,
        clock: Callable[[], str] = utc_now_iso,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if speed <= 0:
            raise ValueError(f"speed must be positive, got {speed}")
        self._source = source
        self._station_id = station_id
        self._camera_id = camera_id
        self._run_id = run_id
        self._speed = speed
        self._fps_override = fps
        self._clock = clock
        self._monotonic = monotonic
        self._sleep = sleep

    def _interval(self) -> float:
        """Seconds to hold between file frames (0.0 for a live device)."""
        if not self._source.is_file:
            return 0.0
        fps = self._fps_override if self._fps_override is not None else self._source.fps
        return 1.0 / fps if fps > 0 else 0.0

    def _record(self, frame: np.ndarray, frame_id: int, mono: float) -> FrameRecord:
        prev = self._prev
        return FrameRecord(
            station_id=self._station_id,
            camera_id=self._camera_id,
            frame_id=frame_id,
            ts=self._clock(),
            capture_mono=mono,
            fingerprint=fingerprint(frame),
            mean_luma=mean_luma(frame),
            noise_score=noise_score(frame, prev),
            run_id=self._run_id,
        )

    def frames(self) -> Iterator[FrameRecord]:
        """Yield one ``FrameRecord`` per frame until the source is exhausted."""
        interval = self._interval()
        start = self._monotonic()
        self._prev: np.ndarray | None = None
        last_mono: float | None = None
        frame_id = 0
        while True:
            frame = self._source.read()
            if frame is None:
                return
            mono = self._monotonic()
            if last_mono is not None and mono <= last_mono:
                mono = last_mono + _MONO_EPSILON
            yield self._record(frame, frame_id, mono)
            self._prev = frame
            last_mono = mono
            frame_id += 1
            self._pace(start, frame_id, interval)

    def _pace(self, start: float, frame_id: int, interval: float) -> None:
        """Sleep until this frame's slot in a real-time (speed-scaled) replay."""
        if interval <= 0:
            return
        target = start + frame_id * interval / self._speed
        delay = target - self._monotonic()
        if delay > 0:
            self._sleep(delay)
