"""One cause, one blind record: a dark frame is not also a missing fiducial.

When the scene goes dark the marker vanishes with it, but the camera has one
problem, not two: ``dark`` opens and ``fiducial_missing`` must not. And a frame
where the marker cannot be found says nothing about whether the view is still
shifted, so it must not count toward clearing ``view_shifted``. Both run a
synthetic clip through the real ``Capture.run`` in real time.
"""

import sys
from pathlib import Path

from station_watch.log import Log
from station_watch.records import BlindReason, BlindState

sys.path.insert(0, str(Path(__file__).parent))
from helpers.synth_video import write_synth_clip  # noqa: E402
from test_capture_blind import _blinds, _run_clip, _thresholds  # noqa: E402


def test_dark_scene_opens_dark_only_not_fiducial_missing(tmp_path):
    path = write_synth_clip(tmp_path / "dark", frames=40, dark_from=5, dark_until=30, fps=20)
    log = Log(tmp_path / "dark.db")
    # The fiducial window is *shorter* than the dark window, so without the
    # suppression the missing marker would trip first.
    _run_clip(path, log, _thresholds(dark_window_s=0.3), real_time=True)

    assert len(_blinds(log, BlindReason.DARK, BlindState.OPENED)) == 1
    assert len(_blinds(log, BlindReason.DARK, BlindState.CLEARED)) == 1
    assert _blinds(log, BlindReason.FIDUCIAL_MISSING) == []


def test_missing_marker_does_not_count_toward_clearing_view_shifted(tmp_path):
    path = write_synth_clip(
        tmp_path / "vs",
        frames=36,
        marker_move_from=5,
        marker_move_px=30,  # shifted to the end of the clip...
        hide_marker_from=16,  # ...and hidden from frame 16 on: never seen back in place
        fps=20,
        seed=10,
    )
    log = Log(tmp_path / "vs.db")
    _run_clip(path, log, _thresholds(), real_time=True)

    assert len(_blinds(log, BlindReason.VIEW_SHIFTED, BlindState.OPENED)) == 1
    assert _blinds(log, BlindReason.VIEW_SHIFTED, BlindState.CLEARED) == []
