"""Capture tests: the proving K14 read-through plus noise/fingerprint behaviour.

Every clip is generated at test time into pytest's ``tmp_path`` (nothing is
written outside it and no binary video is committed) and read back through the
*real* ``cv2.VideoCapture`` file path, so what is under test is the whole
decode-to-``FrameRecord`` path, not a mock.
"""

import sys
from pathlib import Path

import pytest

from station_watch.capture import Capture, CaptureError, FrameSource
from station_watch.records import FrameRecord

sys.path.insert(0, str(Path(__file__).parent))
from helpers.synth_video import write_synth_clip  # noqa: E402

STATION = "ST1"
CAMERA = "CAM1"
RUN = "run-capture-test"


def _capture(path: Path, **kwargs) -> Capture:
    """A Capture over a file source with real time skipped (no-op sleep)."""
    return Capture(
        FrameSource(str(path)),
        station_id=STATION,
        camera_id=CAMERA,
        run_id=RUN,
        sleep=lambda _seconds: None,
        **kwargs,
    )


def test_k14_every_frame_yields_record_with_increasing_ids(tmp_path):
    path = write_synth_clip(tmp_path, frames=24, seed=1)
    records = list(_capture(path).frames())

    assert len(records) == 24
    assert all(isinstance(r, FrameRecord) for r in records)
    frame_ids = [r.frame_id for r in records]
    monos = [r.capture_mono for r in records]
    assert frame_ids == list(range(24))
    assert all(b > a for a, b in zip(frame_ids, frame_ids[1:], strict=False))
    assert all(b > a for a, b in zip(monos, monos[1:], strict=False))


def test_live_still_scene_has_distinct_fingerprints(tmp_path):
    path = write_synth_clip(tmp_path, frames=20, seed=2)
    fingerprints = [r.fingerprint for r in _capture(path).frames()]

    assert len(set(fingerprints)) == len(fingerprints)


def test_noise_score_positive_live_and_zero_when_frozen(tmp_path):
    live = write_synth_clip(tmp_path / "live", frames=15, seed=3)
    frozen = write_synth_clip(tmp_path / "frozen", frames=15, freeze_from=5, seed=3)

    live_records = list(_capture(live).frames())
    # First frame has no predecessor; every later live frame differs.
    assert all(r.noise_score > 0.0 for r in live_records[1:])

    frozen_records = list(_capture(frozen).frames())
    # From the freeze point on, decoded bytes are identical -> exactly zero.
    assert all(r.noise_score == 0.0 for r in frozen_records[6:])
    frozen_prints = {r.fingerprint for r in frozen_records[5:]}
    assert len(frozen_prints) == 1


def test_dark_frames_drop_mean_luma(tmp_path):
    path = write_synth_clip(tmp_path, frames=12, dark_from=6, seed=4)
    records = list(_capture(path).frames())

    assert records[0].mean_luma > records[-1].mean_luma
    assert records[-1].mean_luma < 15.0


def test_file_source_is_paced_at_native_fps(tmp_path):
    path = write_synth_clip(tmp_path, frames=6, fps=20.0, seed=5)
    # A fake clock that only advances when we sleep (frame processing is instant
    # in the test), so drift-corrected pacing schedules one interval per frame.
    now = [0.0]
    slept: list[float] = []

    def _sleep(seconds):
        slept.append(seconds)
        now[0] += seconds

    cap = Capture(
        FrameSource(str(path)),
        station_id=STATION,
        camera_id=CAMERA,
        run_id=RUN,
        monotonic=lambda: now[0],
        sleep=_sleep,
    )
    list(cap.frames())

    # 20 fps -> one ~0.05s sleep per emitted frame.
    assert slept
    assert all(abs(s - 0.05) < 1e-6 for s in slept)


def test_speed_factor_shortens_pacing(tmp_path):
    path = write_synth_clip(tmp_path, frames=4, fps=10.0, seed=6)
    now = [0.0]
    slept: list[float] = []

    def _sleep(seconds):
        slept.append(seconds)
        now[0] += seconds

    cap = Capture(
        FrameSource(str(path)),
        station_id=STATION,
        camera_id=CAMERA,
        run_id=RUN,
        speed=10.0,  # 10x faster than native 10 fps -> 0.01s per frame
        monotonic=lambda: now[0],
        sleep=_sleep,
    )
    list(cap.frames())

    assert slept
    assert all(abs(s - 0.01) < 1e-6 for s in slept)


def test_missing_source_raises(tmp_path):
    with pytest.raises(CaptureError):
        FrameSource(str(tmp_path / "does_not_exist.mkv"))
