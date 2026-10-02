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

A *file* that reads nothing has reached its end: a clean finish
(:attr:`Capture.stream_ended`). A *live device* never finishes: a read that
returns nothing or raises means the camera was unplugged or its driver died, so
Capture writes a ``disconnected`` BlindRecord at once and keeps retrying the
device with capped exponential backoff (reopening it between tries), and the
record clears only after good frames resume (K11). The liveness timer runs for
the whole of :meth:`Capture.run`, independent of the read loop.

A frame that reads fine but then fails to *process* (fingerprint, luma, noise,
fiducial, blind evaluation or the Log append raising) must not kill the loop
either: the error is reported once on stderr and recorded as the same
``disconnected`` BlindRecord (evidence ``{"error": "<Type>: <message>"}``), one
record per episode, cleared by the usual good-frame rule once frames process
again.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator

import numpy as np

from station_watch.capture.blind import BlindThresholds, BlindWatch
from station_watch.capture.metrics import fingerprint, mean_luma, noise_score
from station_watch.capture.resilience import (
    DetectStep,
    liveness_loop,
    record_frame_failure,
    reopen_source,
)
from station_watch.capture.source import FrameSource
from station_watch.clock import utc_now_iso
from station_watch.detect.detector import Detector
from station_watch.detect.geometry import find_marker_corners, marker_center
from station_watch.evidence import EvidenceStore
from station_watch.records import BlindState, FrameRecord

_MONO_EPSILON = 1e-9
_BACKOFF_START_S = 0.05
_BACKOFF_MAX_S = 1.0


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
        detector: Detector | None = None,
        evidence: EvidenceStore | None = None,
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
        self._detect = DetectStep(detector) if detector is not None else None
        self._evidence = evidence
        self._stopped = False
        self._stop_event = threading.Event()
        self.first_frame = threading.Event()
        self.stream_ended = False

    def stop(self) -> None:
        """Ask :meth:`run` to leave its loop after the current frame (or backoff)."""
        self._stopped = True
        self._stop_event.set()

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

    def run(self, log, thresholds: BlindThresholds, *, poll_interval: float | None = None) -> None:
        """Drive the source to exhaustion, writing frame and blind records to ``log``.

        Every frame becomes a ``FrameRecord`` in ``log``; a :class:`BlindWatch`
        turns per-frame metrics (and the fiducial's location) into ``BlindRecord``s.
        A daemon timer thread calls :meth:`BlindWatch.check_liveness` so a hung
        read still yields ``disconnected`` from off the frame loop.
        """
        watch = BlindWatch(
            thresholds,
            station_id=self._station_id,
            camera_id=self._camera_id,
            run_id=self._run_id,
            sink=self._blind_sink(log),
            clock=self._clock,
        )
        start = self._monotonic()
        watch.start(start)
        stop_event = threading.Event()
        timer = threading.Thread(
            target=liveness_loop,
            args=(watch, stop_event, thresholds.liveness_window_s, poll_interval, self._monotonic),
            daemon=True,
        )
        timer.start()
        try:
            self._drive(log, watch, thresholds.fiducial, start)
        finally:
            stop_event.set()
            timer.join(timeout=1.0)

    def _blind_sink(self, log):
        """The Log sink for blind records, teeing an opened record to the evidence store.

        A ``BlindRecord`` carries the last good frame id before the blind; writing
        that frame as evidence keeps the citable "last we saw" shot a blind episode
        points at. Teeing here (not inside the watch) keeps evidence optional and
        off the blind state machine's path entirely.
        """
        if self._evidence is None:
            return log.append

        def sink(record) -> None:
            log.append(record)
            if record.state is BlindState.OPENED:
                self._evidence.note_blind_open(record.last_good_frame_id)

        return sink

    def _drive(self, log, watch: BlindWatch, fiducial: dict, start: float) -> None:
        """The frame loop: read, stamp, log, and fan each frame out to the watch."""
        interval = self._interval()
        self._prev = None
        last_mono: float | None = None
        frame_id = 0
        backoff = _BACKOFF_START_S
        while not self._stopped:
            frame, error = self._read()
            if frame is None:
                if self._stopped:
                    return
                if self._source.is_file and error is None:
                    self.stream_ended = True  # a recording read to its end: clean
                    return
                backoff = self._read_failed(watch, error, backoff)
                continue
            backoff = _BACKOFF_START_S
            mono = self._monotonic()
            if last_mono is not None and mono <= last_mono:
                mono = last_mono + _MONO_EPSILON
            try:
                self._process(log, watch, fiducial, frame, frame_id, mono)
                self._prev = frame
            except Exception as exc:  # K1: a bad frame is recorded, never a dead thread
                self._frame_failed(watch, exc, frame_id)
            last_mono = mono
            frame_id += 1
            self._pace(start, frame_id, interval)

    def _process(
        self, log, watch: BlindWatch, fiducial: dict, frame: np.ndarray, frame_id: int, mono: float
    ) -> None:
        """Stamp, log, and fan one frame out to the watch (may raise; the caller guards)."""
        record = self._record(frame, frame_id, mono)
        log.append(record)
        self.first_frame.set()
        # Find the marker once per frame; derive the blind-watch center from its
        # corners and hand the same corners to Detect (K12, no double detection).
        corners = find_marker_corners(frame, fiducial["dictionary_id"], fiducial["marker_id"])
        watch.observe_frame(record, marker_center(corners))
        had_observation = False
        if self._detect is not None:
            had_observation = self._detect(log, frame, record.frame_id, record.ts, corners) > 0
        if self._evidence is not None:
            # Every frame is offered (any may be a blind's last good frame); the
            # evidence store writes it only if cited, all off the Capture thread.
            self._evidence.note_frame(
                frame,
                record.frame_id,
                record.ts,
                record.fingerprint,
                corners,
                had_observation=had_observation,
            )

    def _frame_failed(self, watch: BlindWatch, exc: Exception, frame_id: int) -> None:
        """Record a per-frame processing error as ``disconnected``; report it once."""
        self._prev = None
        record_frame_failure(watch, exc, frame_id)

    def _read(self) -> tuple[np.ndarray | None, Exception | None]:
        """One read; K10: a read that raises is unobservable, never a crash."""
        try:
            return self._source.read(), None
        except Exception as exc:  # any driver failure is "no frame"
            return None, exc

    def _read_failed(self, watch: BlindWatch, error: Exception | None, backoff: float) -> float:
        """Record the failure, wait out the backoff, reopen a live device; next backoff."""
        detail = "read returned no frame" if error is None else f"read raised: {error!r}"
        watch.read_failed({"read_error": detail, "retry_in_s": backoff})
        self._prev = None
        self._stop_event.wait(backoff)
        if not self._source.is_file and not self._stopped:
            reopen_source(self._source)
        return min(backoff * 2.0, _BACKOFF_MAX_S)
