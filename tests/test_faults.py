"""Proving tests for the synthetic source and the fault-injecting source wrapper.

These prove the two building blocks a bench-free fault drill needs (HF3A.1):

* :class:`SyntheticSource` renders fresh, noisy station frames from the one HF2.1
  renderer, paced to a set fps, with the ``FrameSource`` surface.
* :class:`FaultSource` wraps *any* source and, on a wall-clock schedule, makes each
  of the five camera faults show the frame property its name promises -- then clears
  back to normal. The schedule loads from YAML and rejects bad rows by name.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from station_watch.capture.fiducial import find_marker_center
from station_watch.capture.metrics import fingerprint, mean_luma, noise_score
from station_watch.capture.source import CaptureError, FrameSource
from station_watch.faults import (
    FAULT_NAMES,
    FaultScheduleError,
    FaultSource,
    FaultWindow,
    load_fault_schedule,
)
from station_watch.synth import SyntheticSource, write_synth_clip

_DICT = "DICT_4X4_50"
_MARKER = 0
_DARK = 15.0
_TOL = 10.0


class _Clock:
    """A hand-advanced wall clock for deterministic fault-window timing."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _fast_synthetic() -> SyntheticSource:
    """A synthetic source that never actually sleeps (no real pacing waits in tests)."""
    return SyntheticSource(fps=1000.0, sleep=lambda _s: None)


def _proving_schedule() -> list[FaultWindow]:
    return [
        FaultWindow("lens_covered", 1.0, 2.0),
        FaultWindow("frozen", 3.0, 4.0),
        FaultWindow("bumped", 5.0, 6.0),
        FaultWindow("cable_pulled", 7.0, 8.0),
        FaultWindow("lights_off", 9.0, 10.0),
    ]


def _proving_source(clock: _Clock) -> FaultSource:
    return FaultSource(
        _fast_synthetic(),
        _proving_schedule(),
        dark_luma_threshold=_DARK,
        tolerance_px=_TOL,
        now=clock,
        seed=7,
    )


def _read_normal(fault_src: FaultSource, clock: _Clock) -> np.ndarray:
    """A healthy frame read before any window opens (also seeds the last-good frame)."""
    clock.t = 0.5
    frame = fault_src.read()
    assert frame is not None
    return frame


def _blurred_structure(frame: np.ndarray) -> float:
    """Spatial spread that survives blurring: scene structure, not per-pixel noise."""
    import cv2

    blurred = cv2.GaussianBlur(frame, (11, 11), 0)
    return float(cv2.cvtColor(blurred, cv2.COLOR_BGR2GRAY).std())


# --- SyntheticSource -----------------------------------------------------------


def test_synthetic_source_has_the_framesource_surface_and_reports_fps():
    src = _fast_synthetic()
    assert src.fps == 1000.0
    assert src.is_file is False
    # The four methods a FrameSource offers all exist and are callable.
    assert src.read() is not None
    src.reopen()
    src.release()


def test_synthetic_source_frames_are_fresh_noisy_and_marker_bearing():
    src = _fast_synthetic()
    a = src.read()
    b = src.read()
    assert a is not None and b is not None
    assert a.shape == b.shape
    # Fresh sensor noise: consecutive live frames differ (distinct fingerprints).
    assert fingerprint(a) != fingerprint(b)
    assert noise_score(b, a) > 0.0
    # A healthy, lit scene carrying the fiducial.
    assert mean_luma(a) > _DARK
    assert find_marker_center(a, _DICT, _MARKER) is not None


def test_synthetic_source_paces_reads_to_fps():
    waits: list[float] = []
    now = {"t": 0.0}
    src = SyntheticSource(fps=50.0, monotonic=lambda: now["t"], sleep=lambda s: waits.append(s))
    src.read()  # first read establishes the cadence, no wait
    src.read()  # second read is "early" (clock did not advance), so it waits ~1/fps
    assert waits and math.isclose(waits[-1], 1.0 / 50.0, rel_tol=1e-6)


# --- FaultSource: the five faults, each showing the property its name promises --


def test_normal_frame_before_any_window_is_healthy_and_marker_bearing():
    clock = _Clock()
    normal = _read_normal(_proving_source(clock), clock)
    assert mean_luma(normal) > _DARK
    assert find_marker_center(normal, _DICT, _MARKER) is not None


def test_lens_covered_is_dark_noisy_and_structureless():
    clock = _Clock()
    fault_src = _proving_source(clock)
    _read_normal(fault_src, clock)
    clock.t = 1.5
    a = fault_src.read()
    b = fault_src.read()
    assert a is not None and b is not None
    assert mean_luma(a) < _DARK  # near-black
    assert noise_score(b, a) > 0.0  # sensor noise kept
    assert find_marker_center(a, _DICT, _MARKER) is None  # scene structure gone


def test_frozen_repeats_the_last_good_frame_bytes_exactly():
    clock = _Clock()
    fault_src = _proving_source(clock)
    normal = _read_normal(fault_src, clock)
    clock.t = 3.5
    a = fault_src.read()
    b = fault_src.read()
    assert fingerprint(a) == fingerprint(b)  # identical bytes
    assert fingerprint(a) == fingerprint(normal)  # the last good frame, repeated


def test_bumped_moves_the_fiducial_beyond_tolerance():
    clock = _Clock()
    fault_src = _proving_source(clock)
    normal = _read_normal(fault_src, clock)
    normal_center = find_marker_center(normal, _DICT, _MARKER)
    clock.t = 5.5
    bumped = fault_src.read()
    bumped_center = find_marker_center(bumped, _DICT, _MARKER)
    assert bumped_center is not None
    assert math.dist(bumped_center, normal_center) > _TOL


def test_cable_pulled_yields_no_frames_and_reopen_fails():
    clock = _Clock()
    fault_src = _proving_source(clock)
    _read_normal(fault_src, clock)
    clock.t = 7.5
    assert fault_src.read() is None
    assert fault_src.read() is None
    with pytest.raises(CaptureError):
        fault_src.reopen()


def test_lights_off_is_dark_noisy_but_keeps_scene_structure():
    clock = _Clock()
    fault_src = _proving_source(clock)
    _read_normal(fault_src, clock)
    clock.t = 1.5
    lens = fault_src.read()  # a covered-lens frame for the structure comparison
    clock.t = 9.5
    a = fault_src.read()
    b = fault_src.read()
    assert a is not None and b is not None
    assert mean_luma(a) < _DARK  # global luma drop
    assert noise_score(b, a) > 0.0  # sensor noise kept
    assert _blurred_structure(a) > _blurred_structure(lens)  # structure kept


def test_faults_clear_back_to_normal_after_the_last_window():
    clock = _Clock()
    fault_src = _proving_source(clock)
    _read_normal(fault_src, clock)
    clock.t = 11.0
    recovered = fault_src.read()
    assert recovered is not None
    assert mean_luma(recovered) > _DARK
    assert find_marker_center(recovered, _DICT, _MARKER) is not None
    fault_src.reopen()  # reopen works again once the cable fault has cleared


def test_cable_pulled_recovers_after_clear():
    clock = _Clock()
    fault_src = FaultSource(
        _fast_synthetic(),
        [FaultWindow("cable_pulled", 1.0, 2.0)],
        dark_luma_threshold=_DARK,
        tolerance_px=_TOL,
        now=clock,
    )
    clock.t = 1.5
    assert fault_src.read() is None
    clock.t = 2.5
    assert fault_src.read() is not None


# --- FaultSource over a real FrameSource opened on a generated file -------------


def test_fault_source_wraps_a_framesource_on_a_generated_file(tmp_path):
    clip = write_synth_clip(tmp_path / "clip", frames=30, fps=10.0, marker_px=40)
    clock = _Clock()
    fault_src = FaultSource(
        FrameSource(str(clip)),
        [FaultWindow("lens_covered", 1.0, 2.0)],
        dark_luma_threshold=_DARK,
        tolerance_px=_TOL,
        now=clock,
    )
    try:
        clock.t = 0.5
        healthy = fault_src.read()
        assert healthy is not None
        assert mean_luma(healthy) > _DARK

        clock.t = 1.5
        covered = fault_src.read()
        assert covered is not None
        assert mean_luma(covered) < _DARK
    finally:
        fault_src.release()


# --- Schedule loading: reject unknown faults and overlapping windows -----------


def _write_schedule(path: Path, rows: str) -> Path:
    path.write_text(rows)
    return path


def test_schedule_loads_valid_rows(tmp_path):
    path = _write_schedule(
        tmp_path / "s.yaml",
        "- fault: frozen\n  start_s: 1.0\n  clear_s: 2.0\n"
        "- fault: bumped\n  start_s: 3.0\n  clear_s: 4.0\n",
    )
    windows = load_fault_schedule(path)
    assert [w.fault for w in windows] == ["frozen", "bumped"]
    assert windows[0].start_s == 1.0 and windows[0].clear_s == 2.0


def test_schedule_rejects_unknown_fault_naming_the_row(tmp_path):
    path = _write_schedule(
        tmp_path / "s.yaml",
        "- fault: frozen\n  start_s: 1.0\n  clear_s: 2.0\n"
        "- fault: lens_coverd\n  start_s: 3.0\n  clear_s: 4.0\n",
    )
    with pytest.raises(FaultScheduleError) as exc:
        load_fault_schedule(path)
    message = str(exc.value)
    assert "row 2" in message
    assert "lens_coverd" in message


def test_schedule_rejects_overlapping_windows_naming_the_row(tmp_path):
    path = _write_schedule(
        tmp_path / "s.yaml",
        "- fault: frozen\n  start_s: 1.0\n  clear_s: 3.0\n"
        "- fault: bumped\n  start_s: 2.5\n  clear_s: 4.0\n",
    )
    with pytest.raises(FaultScheduleError) as exc:
        load_fault_schedule(path)
    message = str(exc.value)
    assert "row 2" in message and "row 1" in message


def test_all_five_fault_names_are_known():
    assert FAULT_NAMES == frozenset(
        {"lens_covered", "frozen", "bumped", "cable_pulled", "lights_off"}
    )
