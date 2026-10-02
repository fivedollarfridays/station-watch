"""HF3.9 fix (1): step_times.json comes from normal calibration clips only.

The stall window's source must not be biased by the very clips that stall, keep
out or fault (PR #5 P2). ``step_times.json`` is now computed only from
calibration-split clips labeled as *normal work* -- no ``stalls``, ``keepouts``,
``camera_faults`` or ``creeps`` intervals -- and its provenance records
``step_clips`` (the count used). With no normal calibration clip the file is not
written and the CLI says why.

The proving test hand-checks the p95 against the normal clip's steps alone. Clip
timestamps in the real pipeline are wall-clock (a step's duration is real elapsed
time, not a function of the clip's nominal fps), so an *exact* p95 can only be
hand-checked on deterministic observations: the harness is driven with a stubbed
``run_clip`` that returns a crafted observation stream, exercising the real
selection + ``step_stats`` + write path. The subprocess E2E below then proves the
same selection on a real rendered clip end to end, without pinning a wall-clock
number.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import yaml

from station_watch.evaluate import harness
from station_watch.evaluate.manifest import ClipLabel
from station_watch.evaluate.synthetic_specs import RAIL, STATION_ZONE
from station_watch.records import Observation, ObservationKind
from station_watch.steps import step_durations, step_stats

sys.path.insert(0, str(Path(__file__).parent))
from helpers.synth_station import write_synth_station_clip  # noqa: E402

_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def _obs(kind: ObservationKind, seconds: float, frame_id: int) -> Observation:
    """A station-zone motion observation ``seconds`` into a fixed epoch."""
    ts = (_EPOCH + timedelta(seconds=seconds)).isoformat()
    return Observation(
        station_id="station-1",
        frame_id=frame_id,
        ts=ts,
        kind=kind,
        target="bench",
        method="station_zone_motion",
        confidence_ceiling=1.0,
        detector_output={},
        run_id="run-0",
    )


def _steps_stream(durations_s: list[float]) -> list[Observation]:
    """A motion/no_motion stream whose closed steps have exactly ``durations_s``."""
    stream: list[Observation] = []
    t = 0.0
    fid = 0
    for dur in durations_s:
        stream.append(_obs(ObservationKind.MOTION, t, fid))
        stream.append(_obs(ObservationKind.NO_MOTION, t + dur, fid + 1))
        t += dur + 1.0  # a gap before the next step; it does not form a step
        fid += 2
    return stream


def _clip(rel: str, **labels) -> ClipLabel:
    return ClipLabel(session="s", rel_path=rel, clip_path=Path(rel), **labels)


def _drive_harness(monkeypatch, scored, streams, out_dir):
    """Run ``_evaluate`` over ``scored`` with ``run_clip`` stubbed to ``streams``."""

    def fake_run_clip(
        config, clip_path, work_dir, *, session, rel_path, speed, keepout_backend=None
    ):
        return SimpleNamespace(observations=streams[rel_path])

    monkeypatch.setattr(harness, "run_clip", fake_run_clip)
    monkeypatch.setattr(harness, "extract_outcome", lambda clip, run: None)
    monkeypatch.setattr(harness, "baseline_outcome", lambda clip, config, path: None)
    monkeypatch.setattr(harness, "assemble_metrics", lambda outcomes: {})
    harness._evaluate(
        None,
        scored,
        Path(out_dir),
        dataset="d",
        dataset_kind="real",
        manifest_sha="m",
        config_sha="c",
        speed=1.0,
        backends=[None] * len(scored),
        split="calibration",
        calibration=None,
    )


# --- AC1: p95 from the normal calibration clip only, step_clips == 1 --------------


def test_step_times_computed_from_the_normal_clip_only(tmp_path, monkeypatch):
    normal = _clip("normal.mkv")  # no fault/stall/keepout/creep intervals
    stall = _clip("stall.mkv", stalls=[{"start_frame": 0, "end_frame": 500}])
    normal_steps = [2.0, 3.0, 4.0, 5.0]
    streams = {
        "normal.mkv": _steps_stream(normal_steps),
        # The stall clip's steps include a long stall step that would dominate p95
        # if it were (wrongly) included.
        "stall.mkv": _steps_stream([1.0, 60.0]),
    }
    out = tmp_path / "out"
    _drive_harness(monkeypatch, [normal, stall], streams, out)

    data = json.loads((out / "step_times.json").read_text())
    assert data["provenance"]["step_clips"] == 1
    expected = step_stats(step_durations(streams["normal.mkv"]))
    assert data["metrics"]["count"] == expected["count"] == 4
    assert data["metrics"]["p95_s"] == expected["p95_s"]
    # p95 of {2,3,4,5} is 4.85 -- far below the stall clip's 60 s step.
    assert data["metrics"]["p95_s"] < 6.0


# --- AC2: no normal calibration clips -> no file, and the CLI says why ------------


def test_no_normal_calibration_clips_writes_no_step_times_and_says_why(
    tmp_path, monkeypatch, capsys
):
    stall = _clip("stall.mkv", stalls=[{"start_frame": 0, "end_frame": 9}])
    keepout = _clip("keepout.mkv", keepouts=[{"zone": "z", "start_frame": 0, "end_frame": 9}])
    streams = {"stall.mkv": _steps_stream([1.0]), "keepout.mkv": _steps_stream([1.0])}
    out = tmp_path / "out"
    _drive_harness(monkeypatch, [stall, keepout], streams, out)

    assert not (out / "step_times.json").exists()
    assert (out / "detect.json").exists()  # the other files are still written
    msg = capsys.readouterr().out
    assert "step_times.json" in msg and "normal" in msg


def test_a_held_out_clip_is_not_a_normal_calibration_clip(tmp_path, monkeypatch, capsys):
    # A clean clip on the held_out split must not feed the calibration step window.
    held = _clip("fresh.mkv", split="held_out")
    _drive_harness(monkeypatch, [held], {"fresh.mkv": _steps_stream([2.0])}, tmp_path / "o")
    assert not (tmp_path / "o" / "step_times.json").exists()
    assert "step_times.json" in capsys.readouterr().out


# --- AC6: E2E conformance on a real rendered clip ---------------------------------


def _run_cli(*args, timeout=240):
    return subprocess.run(
        [sys.executable, "-m", "station_watch", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _frame(motion: bool) -> dict:
    return {"positions": {"rail_pos_1": "present", "rail_pos_2": "present"}, "motion": motion}


def _render_normal(clips_dir: Path) -> tuple[str, Path]:
    script: list[dict] = []
    for _ in range(5):  # motion runs, each closed by a still -> closed steps
        script += [_frame(True)] * 4 + [_frame(False)] * 4
    path, _truth = write_synth_station_clip(
        clips_dir / "cal-sess" / "normal",
        script,
        rail_positions=RAIL,
        station_zone={**STATION_ZONE, "track_motion": True},
        fps=20.0,
    )
    return str(Path(path).relative_to(clips_dir)), Path(path)


def _config(path: Path, *, step_times_path=None) -> Path:
    cfg = {
        "station_id": "station-1", "camera_id": "cam-0", "takt_s": 0.2, "grace_s": 0.1,
        "required_slots": list(RAIL), "keepout_zones": [], "liveness_window_s": 2.0,
        "dark_luma_threshold": 15.0, "dark_window_s": 0.3, "frozen_frames": 10_000,
        "recover_good_frames": 3, "cycle_interval_s": 0.05, "recover_healthy_verdicts": 2,
        "fiducial": {"dictionary_id": "DICT_4X4_50", "marker_id": 0,
                     "expected_center_px": [58, 58], "tolerance_px": 10, "window_s": 10_000.0},
        "alarm": {"sinks": ["record"]},
        "watchdog": {"cycle_window_s": 1.0, "alarm_eval_window_s": 1.0, "sinks": ["record"]},
        "detect": {"persistence_frames": 2, "emit_interval_s": 1.0, "rail_positions": RAIL,
                   "station_zone": {**STATION_ZONE, "track_motion": True}, "keepout_rois": {},
                   "blur_threshold": 100.0, "darkness_threshold": 40.0, "occlusion_threshold": 0.5},
    }
    if step_times_path is not None:
        cfg["detect"]["step_times_path"] = str(step_times_path)
    Path(path).write_text(yaml.safe_dump(cfg))
    return path


def test_step_times_e2e_calibration_then_run_consumes_its_window(tmp_path):
    clips = tmp_path / "clips"
    rel, clip_path = _render_normal(clips)
    positions = [
        {"target": t, "state": "present", "start_frame": 0, "end_frame": 39}
        for t in ("rail_pos_1", "rail_pos_2")
    ]
    manifest = tmp_path / "cal.yaml"
    manifest.write_text(yaml.safe_dump({"dataset": "cal", "sessions": [
        {"id": "cal-sess", "split": "calibration", "recorded_on": "2026-09-01",
         "clips": [{"path": rel, "positions": positions}]}]}))

    eval_cfg = _config(tmp_path / "eval.yaml")
    out = tmp_path / "out"
    rc = _run_cli("evaluate", "--split", "calibration", "--config", str(eval_cfg),
                  "--manifest", str(manifest), "--clips-dir", str(clips),
                  "--out", str(out), "--force-out", "--speed", "50")
    assert rc.returncode == 0, rc.stderr
    step_times = out / "step_times.json"
    data = json.loads(step_times.read_text())
    assert data["provenance"]["step_clips"] == 1  # the one normal calibration clip
    p95 = float(data["metrics"]["p95_s"])

    run_cfg = _config(tmp_path / "run.yaml", step_times_path=step_times)
    run = _run_cli("run", "--config", str(run_cfg), "--source", str(clip_path),
                   "--log", str(tmp_path / "log.db"), "--alarm-record", str(tmp_path / "a.jsonl"),
                   "--speed", "50", "--max-cycles", "10")
    assert run.returncode == 0, run.stderr
    assert "measured step times" in run.stdout
    threshold = re.search(r"stall threshold ([\d.]+) s", run.stdout)
    assert threshold, run.stdout
    # The run started with a stall window of the measured p95 plus the config grace.
    assert abs(float(threshold.group(1)) - (p95 + 0.1)) < 0.05
