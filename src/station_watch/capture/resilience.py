"""Capture's failure plumbing: error text, device reopen, the liveness timer, Detect.

Split out of :mod:`station_watch.capture.capture` so the frame loop there stays
about reading and stamping frames. Everything here keeps one rule (K1/K10/K11): a
failure is recorded and reported, never a dead thread.

* :func:`error_detail` -- the bounded ``"<Type>: <message>"`` text a record carries.
* :func:`reopen_source` -- best-effort reopen of a live device between retries.
* :func:`liveness_loop` -- the timer thread that polls :meth:`BlindWatch.check_liveness`.
* :class:`DetectStep` -- runs Detect on a frame; a Detect fault reads
  ``part_unknown``/``detect_error`` (never a false disconnect), reported once per
  episode on stderr.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable

import numpy as np

from station_watch.capture.blind import BlindWatch
from station_watch.detect.detector import CAUSE_DETECT_ERROR, Detector

_ERROR_MESSAGE_MAX = 200


def error_detail(exc: BaseException) -> str:
    """``"<Type>: <message>"`` with the message capped for the Log."""
    return f"{type(exc).__name__}: {str(exc)[:_ERROR_MESSAGE_MAX]}"


def report(message: str) -> None:
    """One operator-facing line on stderr, flushed at once."""
    print(f"station-watch: {message}", file=sys.stderr, flush=True)


def reopen_source(source) -> None:
    """Reopen a live device if it supports it; a failed reopen is retried next backoff."""
    reopen = getattr(source, "reopen", None)
    if reopen is None:
        return
    try:
        reopen()
    except Exception:  # a failed reopen is retried next backoff
        pass


def liveness_loop(
    watch: BlindWatch,
    stop_event: threading.Event,
    window_s: float,
    poll: float | None,
    monotonic: Callable[[], float],
) -> None:
    """Poll liveness on the timer's own clock until ``stop_event`` is set."""
    interval = poll if poll is not None else max(window_s / 4.0, 0.01)
    while not stop_event.is_set():
        watch.check_liveness(monotonic())
        stop_event.wait(interval)


class DetectStep:
    """Run Detect on one frame and append its rows to the Log (same writer)."""

    def __init__(self, detector: Detector) -> None:
        self._detector = detector
        self._error_reported = False

    def __call__(self, log, frame: np.ndarray, frame_id: int, ts: str) -> None:
        """A Detect exception must not kill Capture and must not look disconnected:
        every target reads ``part_unknown`` with cause ``detect_error`` and the
        error text, reported once per episode (a later clean read ends it).
        """
        try:
            observations = self._detector.process(frame, frame_id, ts)
            self._error_reported = False
        except Exception as exc:  # a Detect fault is unknown, never a false disconnect
            detail = error_detail(exc)
            if not self._error_reported:
                self._error_reported = True
                report(f"detect frame {frame_id} failed: {detail}")
            observations = self._detector.unknown_all(frame_id, ts, CAUSE_DETECT_ERROR, detail)
        for observation in observations:
            log.append(observation)


__all__ = ["DetectStep", "error_detail", "liveness_loop", "reopen_source", "report"]
