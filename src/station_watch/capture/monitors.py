"""Per-frame blind-condition monitors: is *this* frame bad, and is it tripped yet.

Each monitor consumes a :class:`Sample` (the metrics a frame already carries plus
the fiducial's location) and answers two questions the generic
:class:`~station_watch.capture.blind.BlindTracker` needs:

* ``bad`` -- is this single frame showing the condition (a repeated fingerprint,
  a dark frame, a missing/shifted marker)? Recovery counts *good* frames, so this
  is what resets the recovery run.
* ``tripped`` -- has the condition held long enough to *open* a blind record? A
  frozen sensor needs ``frozen_frames`` identical frames; dark/fiducial faults
  must hold for a whole window so a single flickered frame never trips them.

A monitor may also answer ``bad = None``: this frame cannot be judged for its
condition at all (``view_shifted`` with no marker found). Such a frame neither
counts toward recovery nor resets it.

Monitors are stateful (they remember the run/window so far) but never emit; the
tracker owns the opened/cleared decision and the Log.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Sample:
    """The per-frame inputs the monitors judge."""

    fingerprint: str
    mean_luma: float
    mono: float  # capture_mono seconds, the window clock
    marker_center: tuple[float, float] | None


class _SustainedFor:
    """Tracks how long a per-frame ``bad`` condition has held continuously (mono s)."""

    def __init__(self, window_s: float) -> None:
        self._window_s = window_s
        self._since: float | None = None

    def update(self, bad: bool, mono: float) -> bool:
        """Record this frame's ``bad``; return True once bad has held >= the window."""
        if not bad:
            self._since = None
            return False
        if self._since is None:
            self._since = mono
        return (mono - self._since) >= self._window_s

    def duration(self, mono: float) -> float:
        """Seconds the current unbroken bad run has lasted (0.0 if not bad)."""
        return 0.0 if self._since is None else mono - self._since


class FrozenMonitor:
    """Trips when ``frozen_frames`` consecutive frames share one fingerprint."""

    def __init__(self, frozen_frames: int) -> None:
        self._need = frozen_frames
        self._last: str | None = None
        self._run = 0

    def evaluate(self, sample: Sample) -> tuple[bool, bool, dict]:
        if sample.fingerprint == self._last:
            self._run += 1
        else:
            self._run = 1
            self._last = sample.fingerprint
        bad = self._run >= 2  # this frame repeats its predecessor's bytes
        tripped = self._run >= self._need
        return tripped, bad, {"fingerprint": sample.fingerprint, "identical_run": self._run}


class DarkMonitor:
    """Trips when mean luma stays below ``threshold`` for the whole ``window_s``."""

    def __init__(self, threshold: float, window_s: float) -> None:
        self._threshold = threshold
        self._sustained = _SustainedFor(window_s)

    def evaluate(self, sample: Sample) -> tuple[bool, bool, dict]:
        bad = sample.mean_luma < self._threshold
        tripped = self._sustained.update(bad, sample.mono)
        evidence = {
            "mean_luma": sample.mean_luma,
            "threshold": self._threshold,
            "dark_seconds": self._sustained.duration(sample.mono),
        }
        return tripped, bad, evidence


class FiducialMissingMonitor:
    """Trips when the marker is not found for the whole ``window_s``."""

    def __init__(self, window_s: float) -> None:
        self._sustained = _SustainedFor(window_s)

    def evaluate(self, sample: Sample) -> tuple[bool, bool, dict]:
        bad = sample.marker_center is None
        tripped = self._sustained.update(bad, sample.mono)
        evidence = {
            "found": sample.marker_center is not None,
            "missing_seconds": self._sustained.duration(sample.mono),
        }
        return tripped, bad, evidence


class ViewShiftedMonitor:
    """Trips when the found marker's center is > ``tolerance_px`` off for the window."""

    def __init__(self, expected_center, tolerance_px: float, window_s: float) -> None:
        self._expected = (float(expected_center[0]), float(expected_center[1]))
        self._tolerance = tolerance_px
        self._sustained = _SustainedFor(window_s)

    def evaluate(self, sample: Sample) -> tuple[bool, bool | None, dict]:
        evidence: dict = {
            "expected_center": list(self._expected),
            "tolerance_px": self._tolerance,
        }
        center = sample.marker_center
        if center is None:
            # No marker, no position: this frame says nothing about the view
            # (a missing marker is FiducialMissingMonitor's business).
            return False, None, evidence
        offset = math.hypot(center[0] - self._expected[0], center[1] - self._expected[1])
        bad = offset > self._tolerance
        evidence["center"] = [center[0], center[1]]
        evidence["offset_px"] = offset
        tripped = self._sustained.update(bad, sample.mono)
        return tripped, bad, evidence
