"""K1 at startup: a camera that has never seen the fiducial is never healthy.

The fiducial blind is debounced (``fiducial.window_s``), so on a camera that has
never once seen the marker the first verdict could run before the debounced
``fiducial_missing`` blind exists and read healthy. Capture now stamps each frame
with whether the marker was found, and the Judge holds the station unobservable
(reason ``fiducial_missing``) until a frame of this run has seen it, whatever the
debounce timing. Frames from a producer that does not report the marker
(``marker_found`` absent) are not held against the station.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from station_watch.clock import offset_iso
from station_watch.judge import Judge
from station_watch.log import Log
from station_watch.records import BlindReason, VerdictState
from station_watch.synth.video import write_synth_clip

sys.path.insert(0, str(Path(__file__).parent))
from helpers.records import frame_at, health_config  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EPOCH = "2026-10-01T00:00:00.000000+00:00"
RUN = "run-marker"


def _t(offset: float) -> str:
    return offset_iso(EPOCH, offset)


def _frame(offset: float, frame_id: int, marker_found):
    return replace(frame_at(_t(offset), RUN, frame_id), marker_found=marker_found)


def test_frames_without_the_marker_ever_seen_are_unobservable_not_healthy(tmp_path):
    judge = Judge(health_config(), run_id=RUN)
    with Log(tmp_path / "log.db") as log:
        log.append(_frame(0.0, 0, False))
        verdict = judge.judge(log, _t(0.1))
    assert verdict.state == VerdictState.UNOBSERVABLE
    assert verdict.blind_reasons == (BlindReason.FIDUCIAL_MISSING,)


def test_the_first_frame_that_sees_the_marker_lets_the_station_turn_healthy(tmp_path):
    judge = Judge(health_config(), run_id=RUN)
    with Log(tmp_path / "log.db") as log:
        log.append(_frame(0.0, 0, False))
        assert judge.judge(log, _t(0.1)).state == VerdictState.UNOBSERVABLE
        log.append(_frame(0.2, 1, True))
        assert judge.judge(log, _t(0.3)).state == VerdictState.HEALTHY
        log.append(_frame(0.4, 2, False))  # later losses are the debounced blind's job
        assert judge.judge(log, _t(0.5)).state == VerdictState.HEALTHY


def test_a_frame_that_does_not_report_the_marker_is_not_held_against_the_station(tmp_path):
    judge = Judge(health_config(), run_id=RUN)
    with Log(tmp_path / "log.db") as log:
        log.append(_frame(0.0, 0, None))
        assert judge.judge(log, _t(0.1)).state == VerdictState.HEALTHY


def test_the_stall_window_is_a_public_property():
    assert Judge(health_config(), run_id=RUN, stall_window_s=12.5).stall_window_s == 12.5
    assert Judge(health_config(), run_id=RUN).stall_window_s == 35.0  # takt 30 + grace 5


def _entry_point() -> list[str]:
    script = Path(sys.executable).parent / "station-watch"
    return [str(script)] if script.exists() else [sys.executable, "-m", "station_watch"]


def test_run_with_the_example_config_on_a_marker_less_clip_is_never_healthy(tmp_path):
    # The shipped 1.0 s fiducial window, not a shortened one: no verdict may be healthy.
    clip = write_synth_clip(tmp_path / "clip", frames=40, fps=10.0, hide_marker_from=0)
    log = tmp_path / "station.db"
    result = subprocess.run(
        [*_entry_point(), "run", "--config", str(ROOT / "config" / "station-example.yaml")]
        + ["--source", str(clip), "--log", str(log)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(log) as conn:
        states = [
            row[0]
            for row in conn.execute(
                "SELECT json_extract(body, '$.state') FROM records WHERE kind = 'verdict'"
            )
        ]
        found = conn.execute(
            "SELECT DISTINCT json_extract(body, '$.marker_found') FROM records WHERE kind = 'frame'"
        ).fetchall()
    assert states and "healthy" not in states, states
    assert found == [(0,)], "Capture stamps every frame with the marker search result"
