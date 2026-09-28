"""Blind-record tests: Capture must never go silent when the source fails.

Each proving test drives a synthetic clip (or, for the hung read, a fake source)
through the real ``Capture.run`` and reads the ``BlindRecord``s back out of the
real Log -- so what is asserted is the whole decode -> detect -> Log path a live
station produces, not a mocked state machine (README K14). Clips are written into
pytest's ``tmp_path`` and windows are kept tiny so the suite stays fast.
"""

import sys
import threading
import time
from pathlib import Path

import numpy as np

from station_watch.capture import BlindThresholds, Capture, FrameSource
from station_watch.log import Log
from station_watch.records import BlindReason, BlindState

sys.path.insert(0, str(Path(__file__).parent))
from helpers.synth_video import write_synth_clip  # noqa: E402

STATION = "ST1"
CAMERA = "CAM1"
RUN = "run-blind-test"
EPOCH = "1970-01-01T00:00:00.000000+00:00"


def _thresholds(**over) -> BlindThresholds:
    fiducial = {
        "dictionary_id": "DICT_4X4_50",
        "marker_id": 0,
        "expected_center_px": [40, 40],
        "tolerance_px": 10,
        "window_s": 0.05,
    }
    base = dict(
        liveness_window_s=5.0,
        dark_luma_threshold=15.0,
        dark_window_s=0.06,
        frozen_frames=4,
        recover_good_frames=3,
        fiducial=fiducial,
    )
    base.update(over)
    return BlindThresholds(**base)


def _blinds(log: Log, reason: BlindReason | None = None, state: BlindState | None = None):
    records = log.since(EPOCH, ["blind"])
    if reason is not None:
        records = [r for r in records if r.reason == reason]
    if state is not None:
        records = [r for r in records if r.state == state]
    return records


def _run_clip(path, log, thresholds, *, real_time=False, speed=1.0) -> None:
    kwargs = {"speed": speed}
    if not real_time:
        kwargs["sleep"] = lambda _s: None
    source = FrameSource(str(path))
    cap = Capture(source, station_id=STATION, camera_id=CAMERA, run_id=RUN, **kwargs)
    cap.run(log, thresholds)


# --- frozen (AC1: proving test + the still-but-live negative) ---------------


def test_frozen_clip_opens_a_frozen_record(tmp_path):
    path = write_synth_clip(tmp_path / "frozen", frames=20, freeze_from=3, fps=30, seed=1)
    log = Log(tmp_path / "frozen.db")
    _run_clip(path, log, _thresholds())

    opened = _blinds(log, BlindReason.FROZEN, BlindState.OPENED)
    assert len(opened) == 1
    assert opened[0].evidence["identical_run"] >= 4
    assert "fingerprint" in opened[0].evidence


def test_still_but_live_clip_opens_no_frozen_record(tmp_path):
    path = write_synth_clip(tmp_path / "live", frames=20, fps=30, seed=2)
    log = Log(tmp_path / "live.db")
    _run_clip(path, log, _thresholds())

    assert _blinds(log, BlindReason.FROZEN) == []


# --- dark (AC2 proving + AC3 flicker) ---------------------------------------


def test_dark_clip_opens_a_dark_record_with_evidence(tmp_path):
    path = write_synth_clip(tmp_path / "dark", frames=20, dark_from=4, fps=20, seed=4)
    log = Log(tmp_path / "dark.db")
    _run_clip(path, log, _thresholds(), real_time=True)

    opened = _blinds(log, BlindReason.DARK, BlindState.OPENED)
    assert len(opened) == 1
    assert opened[0].evidence["mean_luma"] < 15.0
    assert opened[0].evidence["threshold"] == 15.0
    assert opened[0].evidence["dark_seconds"] >= _thresholds().dark_window_s


def test_single_dark_frame_is_flicker_not_dark(tmp_path):
    # Frame 8 alone is black; every other frame is normally lit.
    path = write_synth_clip(
        tmp_path / "flicker", frames=20, dark_from=8, dark_until=9, fps=20, seed=3
    )
    log = Log(tmp_path / "flicker.db")
    _run_clip(path, log, _thresholds(), real_time=True)

    assert _blinds(log, BlindReason.DARK) == []


# --- fiducial_missing (AC2 proving) -----------------------------------------


def test_missing_marker_opens_a_fiducial_missing_record(tmp_path):
    path = write_synth_clip(tmp_path / "fid", frames=20, hide_marker_from=4, fps=20, seed=5)
    log = Log(tmp_path / "fid.db")
    _run_clip(path, log, _thresholds(), real_time=True)

    opened = _blinds(log, BlindReason.FIDUCIAL_MISSING, BlindState.OPENED)
    assert len(opened) == 1
    assert opened[0].evidence["found"] is False
    assert opened[0].evidence["missing_seconds"] >= _thresholds().fiducial["window_s"]


# --- view_shifted (AC2 proving) ---------------------------------------------


def test_shifted_marker_opens_a_view_shifted_record(tmp_path):
    path = write_synth_clip(
        tmp_path / "shift", frames=20, marker_move_from=4, marker_move_px=30, fps=20, seed=6
    )
    log = Log(tmp_path / "shift.db")
    _run_clip(path, log, _thresholds(), real_time=True)

    opened = _blinds(log, BlindReason.VIEW_SHIFTED, BlindState.OPENED)
    assert len(opened) == 1
    assert opened[0].evidence["offset_px"] > 10
    assert opened[0].evidence["tolerance_px"] == 10


# --- opened + cleared, one test per reason (AC5) ----------------------------


def _open_and_clear(log, reason):
    opened = _blinds(log, reason, BlindState.OPENED)
    cleared = _blinds(log, reason, BlindState.CLEARED)
    assert len(opened) == 1, f"{reason}: expected one opened, got {len(opened)}"
    assert len(cleared) == 1, f"{reason}: expected one cleared, got {len(cleared)}"


def test_frozen_opens_then_clears_after_recovery(tmp_path):
    path = write_synth_clip(
        tmp_path / "fz", frames=30, freeze_from=3, freeze_until=14, fps=30, seed=7
    )
    log = Log(tmp_path / "fz.db")
    _run_clip(path, log, _thresholds())
    _open_and_clear(log, BlindReason.FROZEN)


def test_dark_opens_then_clears_after_recovery(tmp_path):
    path = write_synth_clip(tmp_path / "dk", frames=30, dark_from=5, dark_until=16, fps=20, seed=8)
    log = Log(tmp_path / "dk.db")
    _run_clip(path, log, _thresholds(), real_time=True)
    _open_and_clear(log, BlindReason.DARK)


def test_fiducial_missing_opens_then_clears_after_recovery(tmp_path):
    path = write_synth_clip(
        tmp_path / "fm", frames=30, hide_marker_from=5, hide_marker_until=16, fps=20, seed=9
    )
    log = Log(tmp_path / "fm.db")
    _run_clip(path, log, _thresholds(), real_time=True)
    _open_and_clear(log, BlindReason.FIDUCIAL_MISSING)


def test_view_shifted_opens_then_clears_after_recovery(tmp_path):
    path = write_synth_clip(
        tmp_path / "vs",
        frames=30,
        marker_move_from=5,
        marker_move_until=16,
        marker_move_px=30,
        fps=20,
        seed=10,
    )
    log = Log(tmp_path / "vs.db")
    _run_clip(path, log, _thresholds(), real_time=True)
    _open_and_clear(log, BlindReason.VIEW_SHIFTED)


# --- disconnected (AC2 proving == AC4 hung read; plus AC5 recovery) ---------


def _bright_frame(rng) -> np.ndarray:
    scene = np.full((120, 120, 3), 200, np.uint8)
    return np.clip(scene.astype(float) + rng.normal(0, 8, scene.shape), 0, 255).astype(np.uint8)


class _HungSource:
    """A live source whose read blocks until released -- a hung camera driver."""

    is_file = False
    fps = 0.0

    def __init__(self) -> None:
        self._released = threading.Event()

    def read(self):
        self._released.wait(5.0)
        return None  # only reached once the test releases it, to end the loop

    def release(self) -> None:
        self._released.set()


class _GapSource:
    """Live frames, then one read stalls past the window, then frames resume.

    A live device never "ends" (an empty read is a failure, not a finish), so the
    script's end stops the Capture through ``on_end`` -- the test's hand on the
    power switch -- rather than by returning nothing.
    """

    is_file = False
    fps = 0.0

    def __init__(self, before: int, gap_s: float, after: int) -> None:
        self._ops = ["f"] * before + ["gap"] + ["f"] * after + ["stop"]
        self._i = 0
        self._gap_s = gap_s
        self._rng = np.random.default_rng(0)
        self.on_end = lambda: None

    def read(self):
        op = self._ops[min(self._i, len(self._ops) - 1)]
        self._i += 1
        if op == "stop":
            self.on_end()
            return None
        if op == "gap":
            time.sleep(self._gap_s)
        return _bright_frame(self._rng)

    def release(self) -> None:
        pass


def test_hung_source_opens_disconnected_within_window_plus_one(tmp_path):
    source = _HungSource()
    log = Log(tmp_path / "hung.db")
    thresholds = _thresholds(liveness_window_s=0.2)
    cap = Capture(source, station_id=STATION, camera_id=CAMERA, run_id=RUN)
    worker = threading.Thread(
        target=cap.run, args=(log, thresholds), kwargs={"poll_interval": 0.02}
    )
    worker.start()
    try:
        deadline = time.monotonic() + (0.2 + 1.0)
        while time.monotonic() < deadline:
            if _blinds(log, BlindReason.DISCONNECTED, BlindState.OPENED):
                break
            time.sleep(0.02)
        opened = _blinds(log, BlindReason.DISCONNECTED, BlindState.OPENED)
        assert len(opened) == 1
        assert opened[0].evidence["silent_seconds"] >= 0.2
        assert opened[0].evidence["liveness_window_s"] == 0.2
    finally:
        cap.stop()
        source.release()
        worker.join(timeout=2.0)


def test_disconnected_opens_then_clears_when_frames_resume(tmp_path):
    source = _GapSource(before=3, gap_s=0.35, after=5)
    log = Log(tmp_path / "gap.db")
    thresholds = _thresholds(liveness_window_s=0.2, recover_good_frames=3)
    cap = Capture(source, station_id=STATION, camera_id=CAMERA, run_id=RUN)
    source.on_end = cap.stop
    cap.run(log, thresholds, poll_interval=0.02)

    _open_and_clear(log, BlindReason.DISCONNECTED)


# --- AC6: records reach the Log through the Log API --------------------------


def test_frame_and_blind_records_both_land_in_the_log(tmp_path):
    path = write_synth_clip(tmp_path / "both", frames=20, freeze_from=3, fps=30, seed=11)
    log = Log(tmp_path / "both.db")
    _run_clip(path, log, _thresholds())

    # Both record kinds are read back through the Log API, not from memory.
    assert log.since(EPOCH, ["frame"])
    assert log.newest("blind", reason="frozen", state="opened") is not None
