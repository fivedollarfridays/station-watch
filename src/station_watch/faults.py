"""Inject camera faults into any frame source on a wall-clock schedule.

A bench-free fault drill needs the five camera faults an operator can actually
cause, applied to a live source (synthetic or a real :class:`FrameSource`) so the
real Capture loop and the blind monitors see exactly what a failing camera would
show. :class:`FaultSource` wraps a source and, while a scheduled window is open,
transforms (or withholds) each frame so it carries the property its fault name
promises, as the camera would see it:

* ``lens_covered`` -- near-black frames that keep sensor noise (the lens is
  blocked: no scene, but the sensor still shimmers).
* ``frozen`` -- the last good frame's bytes, repeated exactly (a stuck sensor).
* ``bumped`` -- the image translated so the fiducial lands beyond
  ``fiducial.tolerance_px`` of where it normally sits (the camera was knocked).
* ``cable_pulled`` -- :meth:`read` returns nothing and :meth:`reopen` fails until
  the window clears (the feed is gone, and re-opening the device does not help).
* ``lights_off`` -- a global luma drop below ``dark_luma_threshold`` with sensor
  noise kept and the scene *structure* kept (distinct from a covered lens).

A schedule is a list of :class:`FaultWindow` (``fault``, ``start_s``, ``clear_s``),
loadable from YAML by :func:`load_fault_schedule`, which rejects an unknown fault
name or overlapping windows with a :class:`FaultScheduleError` that names the row.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import yaml

from station_watch.capture.source import CaptureError

FAULT_NAMES = frozenset({"lens_covered", "frozen", "bumped", "cable_pulled", "lights_off"})


@dataclass(frozen=True)
class FaultWindow:
    """One scheduled fault open over ``[start_s, clear_s)`` on the wall clock."""

    fault: str
    start_s: float
    clear_s: float


class FaultScheduleError(ValueError):
    """Raised for a malformed fault schedule, with a message naming the row."""


class FaultSource:
    """Wrap a frame source and apply a schedule of camera faults to its frames.

    The wrapped ``source`` only needs the :class:`FrameSource` surface
    (``read`` / ``reopen`` / ``release`` / ``fps``). ``dark_luma_threshold`` and
    ``tolerance_px`` are the station's own thresholds, so a darkened frame lands
    under the blind monitor's bar and a bumped frame lands beyond the view-shift
    monitor's. ``now`` is injectable so a drill or a test can drive the schedule on
    a controlled clock.
    """

    def __init__(
        self,
        source,
        schedule: Iterable[FaultWindow],
        *,
        dark_luma_threshold: float = 15.0,
        tolerance_px: float = 10.0,
        now: Callable[[], float] = time.monotonic,
        seed: int = 0,
    ) -> None:
        self._source = source
        self._schedule = list(schedule)
        self._dark = float(dark_luma_threshold)
        self._bump_px = max(int(tolerance_px * 2 + 6), 6)
        self._now = now
        self._t0 = now()
        self._rng = np.random.default_rng(seed)
        self._last_good: np.ndarray | None = None

    # -- FrameSource surface ---------------------------------------------------

    @property
    def is_file(self) -> bool:
        """Mirror the wrapped source (False for a live/synthetic feed)."""
        return bool(getattr(self._source, "is_file", False))

    @property
    def fps(self) -> float:
        """The wrapped source's frame rate, unchanged by fault injection."""
        return float(self._source.fps)

    def read(self) -> np.ndarray | None:
        """The next frame, transformed by whichever fault window is open now."""
        fault = self._active_fault()
        if fault == "cable_pulled":
            return None
        if fault == "frozen":
            if self._last_good is None:
                frame = self._source.read()
                if frame is None:
                    return None
                self._last_good = frame
            return self._last_good
        frame = self._source.read()
        if frame is None:
            return None
        if fault is None:
            self._last_good = frame
            return frame
        if fault == "lens_covered":
            return self._lens_covered(frame.shape)
        if fault == "lights_off":
            return self._lights_off(frame)
        if fault == "bumped":
            return self._bumped(frame)
        return frame  # pragma: no cover - schedule is validated to the five names

    def reopen(self) -> None:
        """Reopen the wrapped source -- but a pulled cable makes reopen fail."""
        if self._active_fault() == "cable_pulled":
            raise CaptureError("cable_pulled: device cannot be reopened until the fault clears")
        reopen = getattr(self._source, "reopen", None)
        if reopen is not None:
            reopen()

    def release(self) -> None:
        """Release the wrapped source."""
        release = getattr(self._source, "release", None)
        if release is not None:
            release()

    def __enter__(self) -> FaultSource:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()

    # -- fault application ------------------------------------------------------

    def _active_fault(self) -> str | None:
        elapsed = self._now() - self._t0
        for window in self._schedule:
            if window.start_s <= elapsed < window.clear_s:
                return window.fault
        return None

    def _lens_covered(self, shape) -> np.ndarray:
        """Near-black, but with fresh sensor noise and no scene structure."""
        sigma = max(self._dark * 0.3, 1.0)
        noise = self._rng.normal(0.0, sigma, shape)
        return np.clip(noise, 0, 255).astype(np.uint8)

    def _lights_off(self, frame: np.ndarray) -> np.ndarray:
        """Dim the whole scene under threshold, keeping its structure and noise."""
        gray_mean = float(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).mean())
        target = self._dark * 0.5
        factor = target / gray_mean if gray_mean > 1e-6 else 0.0
        dimmed = frame.astype(np.float64) * factor
        noise = self._rng.normal(0.0, 1.5, frame.shape)
        return np.clip(dimmed + noise, 0, 255).astype(np.uint8)

    def _bumped(self, frame: np.ndarray) -> np.ndarray:
        """Translate the image so the fiducial lands beyond tolerance."""
        height, width = frame.shape[:2]
        matrix = np.float32([[1, 0, self._bump_px], [0, 1, self._bump_px]])
        return cv2.warpAffine(frame, matrix, (width, height), borderMode=cv2.BORDER_REPLICATE)


def _row_window(row, index: int) -> FaultWindow:
    """Validate one authored row (1-based ``index``) into a :class:`FaultWindow`."""
    if not isinstance(row, dict):
        raise FaultScheduleError(f"row {index}: must be a mapping with fault/start_s/clear_s")
    for key in ("fault", "start_s", "clear_s"):
        if key not in row:
            raise FaultScheduleError(f"row {index}: missing required key {key!r}")
    fault = row["fault"]
    if fault not in FAULT_NAMES:
        raise FaultScheduleError(
            f"row {index} (fault={fault!r}): unknown fault name; valid names are "
            f"{sorted(FAULT_NAMES)}"
        )
    start, clear = row["start_s"], row["clear_s"]
    for key, value in (("start_s", start), ("clear_s", clear)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise FaultScheduleError(
                f"row {index} (fault={fault!r}): {key} must be a number, got {value!r}"
            )
    if not clear > start:
        raise FaultScheduleError(
            f"row {index} (fault={fault!r}): clear_s ({clear}) must be greater than "
            f"start_s ({start})"
        )
    return FaultWindow(fault, float(start), float(clear))


def _reject_overlaps(windows: list[FaultWindow]) -> None:
    """Fail if any two windows overlap in time, naming both rows (as authored)."""
    ordered = sorted(enumerate(windows, start=1), key=lambda pair: pair[1].start_s)
    for (prev_i, prev), (cur_i, cur) in zip(ordered, ordered[1:], strict=False):
        if cur.start_s < prev.clear_s:
            raise FaultScheduleError(
                f"row {cur_i} (fault={cur.fault!r}) overlaps row {prev_i} "
                f"(fault={prev.fault!r}): [{cur.start_s}, {cur.clear_s}) overlaps "
                f"[{prev.start_s}, {prev.clear_s})"
            )


def load_fault_schedule(path: str | Path) -> list[FaultWindow]:
    """Load and validate a fault schedule from YAML.

    The file is a list of ``{fault, start_s, clear_s}`` rows (or a mapping with a
    ``faults:`` list). An unknown fault name or any overlapping windows raise
    :class:`FaultScheduleError` naming the offending row(s).
    """
    data = yaml.safe_load(Path(path).read_text())
    rows = data["faults"] if isinstance(data, dict) and "faults" in data else data
    if not isinstance(rows, list):
        raise FaultScheduleError(
            "fault schedule must be a YAML list of {fault, start_s, clear_s} rows"
        )
    windows = [_row_window(row, index) for index, row in enumerate(rows, start=1)]
    _reject_overlaps(windows)
    return windows


__all__ = [
    "FAULT_NAMES",
    "FaultWindow",
    "FaultScheduleError",
    "FaultSource",
    "load_fault_schedule",
]
