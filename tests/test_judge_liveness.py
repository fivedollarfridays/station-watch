"""Judge fix-round tests: run-scoped blind state and K2 frame freshness.

The Judge must not trust Capture alone to say the camera is gone (defense in
depth): if the newest frame of *this run* is older than ``liveness_window_s`` the
station is unobservable even with no BlindRecord at all. And a restart mints a new
``run_id``: a previous run that ended blind must not keep the new run blind.
"""

import sys
from pathlib import Path

from station_watch.clock import offset_iso
from station_watch.judge import Judge
from station_watch.log import Log
from station_watch.records import BlindReason, BlindState, VerdictState

sys.path.insert(0, str(Path(__file__).parent))
from helpers.records import blind_at, frame_at, health_config  # noqa: E402

START = "2026-09-28T12:00:00.000000+00:00"


def _at(offset_s):
    return offset_iso(START, offset_s)


def test_old_run_ending_blind_does_not_leak_into_a_new_clean_run(tmp_path):
    config = health_config()
    with Log(tmp_path / "log.db") as log:
        log.append(frame_at(_at(0), "run-old", 0))
        log.append(blind_at(_at(1), "run-old", BlindReason.DISCONNECTED, BlindState.OPENED, 1))
        log.append(blind_at(_at(1), "run-old", BlindReason.DARK, BlindState.OPENED, 2))
        # The old process died blind. A new run starts on the same log, clean.
        judge = Judge(config, run_id="run-new")
        for n, offset in enumerate((10.0, 10.5, 11.0)):
            log.append(frame_at(_at(offset), "run-new", n))
            verdict = judge.judge(log, _at(offset))
            assert verdict.state == VerdictState.HEALTHY, (offset, verdict)
            assert verdict.blind_reasons == ()


def test_stale_newest_frame_is_unobservable_without_any_blind_record(tmp_path):
    config = health_config(liveness_window_s=2.0)
    with Log(tmp_path / "log.db") as log:
        log.append(frame_at(_at(0), "run-1", 0))
        judge = Judge(config, run_id="run-1")
        assert judge.judge(log, _at(1.0)).state == VerdictState.HEALTHY
        verdict = judge.judge(log, _at(2.5))  # 2.5s since the last frame > 2.0s window
        assert verdict.state == VerdictState.UNOBSERVABLE
        assert verdict.blind_reasons == (BlindReason.DISCONNECTED,)


def test_no_frame_at_all_in_this_run_is_unobservable(tmp_path):
    config = health_config()
    with Log(tmp_path / "log.db") as log:
        log.append(frame_at(_at(0), "run-old", 0))  # a frame, but from another run
        verdict = Judge(config, run_id="run-1").judge(log, _at(0.5))
        assert verdict.state == VerdictState.UNOBSERVABLE
        assert BlindReason.DISCONNECTED in verdict.blind_reasons


def test_a_cleanly_ended_file_stream_is_not_judged_stale(tmp_path):
    config = health_config(liveness_window_s=2.0)
    with Log(tmp_path / "log.db") as log:
        log.append(frame_at(_at(0), "run-1", 0))
        verdict = Judge(config, run_id="run-1").judge(log, _at(5.0), stream_ended=True)
        assert verdict.state == VerdictState.HEALTHY


def test_rows_stamped_after_now_are_held_back_then_counted(tmp_path):
    config = health_config()
    with Log(tmp_path / "log.db") as log:
        log.append(frame_at(_at(0), "run-1", 0))
        log.append(blind_at(_at(5), "run-1", BlindReason.DARK, BlindState.OPENED, 1))
        judge = Judge(config, run_id="run-1")
        assert judge.judge(log, _at(1.0)).state == VerdictState.HEALTHY
        log.append(frame_at(_at(5.5), "run-1", 1))
        later = judge.judge(log, _at(6.0))
        assert later.state == VerdictState.UNOBSERVABLE
        assert later.blind_reasons == (BlindReason.DARK,)
