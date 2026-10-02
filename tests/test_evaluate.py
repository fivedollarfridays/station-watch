"""End-to-end tests for ``station-watch evaluate`` (K13).

Covers the proving run (AC1: numbers recomputed correctly from a hand-countable
synthetic set), the honesty rules (AC2/AC3: no labeled set / a missing clip write
nothing and exit non-zero with distinct codes), provenance (AC4), the baseline
written beside the main numbers (AC5), and the E2E conformance that the real
``evaluate`` CLI produces a ``step_times.json`` the real ``run`` CLI then consumes
(AC7). Reachability of the ``evaluate`` module from the CLI (AC6) is proven by the
orphan test in ``test_watchdog.py``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from station_watch.evaluate import EXIT_MISSING_CLIP, EXIT_NO_LABELED_SET, run_evaluation
from station_watch.evaluate.synthetic import RAIL

sys.path.insert(0, str(Path(__file__).parent))
from helpers.synth_station import write_synth_station_clip  # noqa: E402

STATION_ZONE = {"id": "bench", "region": [[0.3, 1.5], [5.5, 1.5], [5.5, 3.5], [0.3, 3.5]]}


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    """Run ``evaluate --synthetic`` once; return the output directory of files."""
    out = tmp_path_factory.mktemp("synth-eval")
    rc = run_evaluation(
        config_path=None, manifest_path=None, out_dir=str(out), synthetic=True, clips_dir="unused"
    )
    assert rc == 0
    return out


def _metrics(path):
    return json.loads(Path(path).read_text())["metrics"]


def _provenance(path):
    return json.loads(Path(path).read_text())["provenance"]


# --- AC1: the proving run recomputes precision / recall / latency correctly ------


def test_synthetic_detect_numbers_are_exact(synth):
    m = _metrics(synth / "detect.json")
    # One clip per flag type is a true positive; the rest are true negatives.
    # One clip has the missing part; the other six (incl. the two detail clips, whose
    # components stay present) are true negatives for the component-level flag.
    assert m["missing_part"] == {
        "tp": 1,
        "fp": 0,
        "fn": 0,
        "tn": 6,
        "precision": 1.0,
        "recall": 1.0,
    }
    assert m["stalled"]["tp"] == 1 and m["stalled"]["fp"] == 0 and m["stalled"]["recall"] == 1.0
    assert m["keepout_entry"]["precision"] == 1.0 and m["keepout_entry"]["recall"] == 1.0
    # cycle_time_creep has no instance in the proving set -> honestly undefined.
    assert m["cycle_time_creep"]["precision"] is None
    # rail-position state confusion, now including the scored detail targets (HF3.16):
    # 9 rail-present + 8 detail-present read present; 1 rail-absent + 2 detail-absent read
    # absent; 2 unknown read unknown. Every detail read lands on the diagonal.
    conf = m["confusion_matrix"]
    assert conf["present"]["present"] == 17 and conf["absent"]["absent"] == 3
    assert conf["unknown"]["unknown"] == 2
    assert m["rail_position_states"]["present"]["recall"] == 1.0
    # latency is real and ordered; one injected fault has a measured time to alarm.
    lat = m["latency"]
    assert lat["count"] > 0 and lat["p95_s"] >= lat["median_s"] >= 0.0
    assert m["time_to_alarm"][0]["seconds"] > 0.0


def test_synthetic_results_are_reported_per_session(synth):
    m = _metrics(synth / "detect.json")
    assert set(m["per_session"]) == {"s1", "s2"}
    # the stall lives in session s2; s1 has no stall true positive.
    assert m["per_session"]["s2"]["stalled"]["tp"] == 1
    assert m["per_session"]["s1"]["stalled"]["tp"] == 0


# --- AC4: every file carries provenance; synthetic files are tagged synthetic ----


def test_every_measurement_file_carries_provenance(synth):
    for name in ("detect.json", "baseline.json", "step_times.json"):
        prov = _provenance(synth / name)
        assert set(prov) >= {
            "dataset",
            "dataset_kind",
            "manifest_sha256",
            "config_sha256",
            "git_commit",
            "detector",
            "clips",
            "sessions",
            "date_utc",
        }
        assert prov["dataset_kind"] == "synthetic"
        assert prov["clips"] == 7 and prov["sessions"] == ["s1", "s2"]
        assert len(prov["manifest_sha256"]) == 64 and len(prov["config_sha256"]) == 64


# --- AC5: the baseline is computed on the same clips, beside the main numbers -----


def test_baseline_written_beside_and_beaten_by_the_detector(synth):
    detect = _metrics(synth / "detect.json")
    baseline = _metrics(synth / "baseline.json")
    assert _provenance(synth / "baseline.json")["detector"].startswith("frame_diff")
    assert _provenance(synth / "baseline.json")["clips"] == 7  # same clip set
    # The naive whole-frame baseline is blind to a missing part and a keep-out entry,
    # so the real detector's recall is at least as good on every flag type.
    for flag in ("missing_part", "stalled", "keepout_entry"):
        assert (detect[flag]["recall"] or 0.0) >= (baseline[flag]["recall"] or 0.0)


# --- AC2/AC3: honesty -- no labeled set / a missing clip write nothing -----------


def test_absent_manifest_prints_message_and_writes_nothing(tmp_path, capsys):
    out = tmp_path / "out"
    rc = run_evaluation(
        config_path=None,
        manifest_path=None,
        out_dir=str(out),
        synthetic=False,
        clips_dir=str(tmp_path),
    )
    assert rc == EXIT_NO_LABELED_SET and rc != 0
    assert "no labeled set present" in capsys.readouterr().out
    assert not (out / "detect.json").exists()


def test_missing_manifest_file_is_distinct_nonzero(tmp_path):
    rc = run_evaluation(
        config_path=None,
        manifest_path=str(tmp_path / "ghost.yaml"),
        out_dir=str(tmp_path / "o"),
        synthetic=False,
        clips_dir=str(tmp_path),
    )
    assert rc == EXIT_NO_LABELED_SET


def test_manifest_naming_a_missing_clip_fails_naming_it_and_writes_nothing(tmp_path, capsys):
    manifest = tmp_path / "m.yaml"
    manifest.write_text(
        yaml.safe_dump({"sessions": [{"id": "s1", "clips": [{"path": "s1/ghost.mkv"}]}]})
    )
    out = tmp_path / "out"
    rc = run_evaluation(
        config_path=None,
        manifest_path=str(manifest),
        out_dir=str(out),
        synthetic=False,
        clips_dir=str(tmp_path / "clips"),
    )
    assert rc == EXIT_MISSING_CLIP and rc not in (0, EXIT_NO_LABELED_SET)
    assert "ghost.mkv" in capsys.readouterr().err
    assert not out.exists() or not (out / "detect.json").exists()


# --- AC7: the real evaluate CLI produces a step_times.json the real run consumes --


def _run_cli(*args, timeout=180):
    return subprocess.run(
        [sys.executable, "-m", "station_watch", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _run_config(path, step_times_path):
    cfg = {
        "station_id": "station-1",
        "camera_id": "cam-0",
        "takt_s": 30.0,
        "grace_s": 5.0,
        "required_slots": list(RAIL),
        "keepout_zones": [],
        "liveness_window_s": 2.0,
        "dark_luma_threshold": 15.0,
        "dark_window_s": 0.3,
        "frozen_frames": 10_000,
        "recover_good_frames": 3,
        "cycle_interval_s": 0.05,
        "recover_healthy_verdicts": 2,
        "fiducial": {
            "dictionary_id": "DICT_4X4_50",
            "marker_id": 0,
            "expected_center_px": [58, 58],
            "tolerance_px": 10,
            "window_s": 10_000.0,
        },
        "alarm": {"sinks": ["record"]},
        "watchdog": {"cycle_window_s": 1.0, "alarm_eval_window_s": 1.0, "sinks": ["record"]},
        "detect": {
            "persistence_frames": 2,
            "emit_interval_s": 5.0,
            "rail_positions": RAIL,
            "station_zone": STATION_ZONE,
            "keepout_rois": {},
            "blur_threshold": 100.0,
            "darkness_threshold": 40.0,
            "occlusion_threshold": 0.5,
            "step_times_path": str(step_times_path),
        },
    }
    Path(path).write_text(yaml.safe_dump(cfg))
    return path


def test_evaluate_subprocess_writes_step_times_the_run_subprocess_reads(tmp_path):
    eval_out = tmp_path / "measurements"
    # --out is outside the synthetic tree (a pytest temp dir), so --force-out is
    # required now that every evaluate --out is confined (HF3.9).
    result = _run_cli("evaluate", "--synthetic", "--out", str(eval_out), "--force-out")
    assert result.returncode == 0, result.stderr
    step_times = eval_out / "step_times.json"
    assert step_times.exists(), "the evaluate run must write step_times.json (HF2.4's file)"

    config = _run_config(tmp_path / "run.yaml", step_times)
    clip, _truth = write_synth_station_clip(
        tmp_path / "clip",
        [{"positions": {"rail_pos_1": "present", "rail_pos_2": "present"}} for _ in range(20)],
        rail_positions=RAIL,
        station_zone=STATION_ZONE,
        fps=20.0,
    )
    run = _run_cli(
        "run",
        "--config",
        str(config),
        "--source",
        str(clip),
        "--log",
        str(tmp_path / "log.db"),
        "--alarm-record",
        str(tmp_path / "alarm.jsonl"),
        "--speed",
        "50",
        "--max-cycles",
        "10",
    )
    assert run.returncode == 0, run.stderr
    assert "measured step times" in run.stdout, run.stdout


# --- HF3.16: a real evaluate --synthetic subprocess scores the detail targets --------


def test_synthetic_subprocess_scores_detail_targets_in_the_confusion_matrix(tmp_path):
    # A real `station-watch evaluate --synthetic` subprocess regenerates detect.json; the
    # two detail clips' label/ferrule/torque_stripe reads land in the rail confusion
    # matrix. Without detail scoring the matrix would hold 9 present + 1 absent (rail
    # positions only); the detail targets raise it to 17 present + 3 absent, all on the
    # diagonal -- every scored detail read is correct.
    eval_out = tmp_path / "measurements"
    result = _run_cli("evaluate", "--synthetic", "--out", str(eval_out), "--force-out")
    assert result.returncode == 0, result.stderr
    m = _metrics(eval_out / "detect.json")
    conf = m["confusion_matrix"]
    assert conf["present"]["present"] == 17, conf
    assert conf["absent"]["absent"] == 3, conf
    # Every read is correct: no detail (or rail) interval is misread or missed.
    off_diagonal = sum(
        conf[gt][pred] for gt in ("present", "absent", "unknown") for pred in conf[gt] if pred != gt
    )
    assert off_diagonal == 0, conf
    assert _provenance(eval_out / "detect.json")["clips"] == 7


# --- the step-time baseline is a real measurement, not a degenerate 0.0 s ----------


def test_synthetic_step_times_measures_closed_steps(synth):
    data = json.loads((synth / "step_times.json").read_text())
    assert data["metrics"]["count"] > 0, data
    assert data["metrics"]["p95_s"] > 0.0


def test_committed_synthetic_step_times_is_not_degenerate():
    committed = Path(__file__).resolve().parents[1] / "measurements/synthetic/step_times.json"
    metrics = json.loads(committed.read_text())["metrics"]
    assert metrics["count"] > 0 and metrics["p95_s"] > 0.0, metrics
