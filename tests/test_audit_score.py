"""``audit score``: reviewed precision, the status rules, and --out confinement.

A session of 4 flags with 3 reviewed (2 correct, 1 incorrect) scores precision 2/3,
reviewed 3, unreviewed 1. A session with no reviewed flags writes ``no_reviewed_flags``
and a QA-failing session writes ``qa_failed`` -- each with no ``metrics`` key. ``--out``
is confined to the dataset kind's own measurement tree unless ``--force-out``. The Log
is only ever read.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from station_watch.audit.score import score_audit
from station_watch.clock import offset_iso
from station_watch.log import Log
from station_watch.records import CycleCompleted, Verdict, VerdictState

sys.path.insert(0, str(Path(__file__).parent))
from helpers.records import HEALTH_CONFIG  # noqa: E402

BASE = "2026-10-01T00:00:00.000000+00:00"
RUN = "run-score"


def _healthy_log(path: Path) -> Path:
    with Log(path) as log:
        for seq, offset in enumerate((0.0, 1.0)):
            log.append(Verdict(
                station_id="station-1", ts=offset_iso(BASE, offset), state=VerdictState.HEALTHY,
                faults=(), blind_reasons=(), seq=seq, run_id=RUN,
            ))
        log.append(CycleCompleted(ts=offset_iso(BASE, 2.0), cycle=0, stages=(), run_id=RUN))
    return path


def _empty_log(path: Path) -> Path:
    with Log(path) as log:  # a Log with no verdicts -> session_qa fails ("no verdicts")
        log.append(CycleCompleted(ts=offset_iso(BASE, 0.0), cycle=0, stages=(), run_id=RUN))
    return path


def _config(path: Path, max_unknown=0.9) -> Path:
    data = {**HEALTH_CONFIG, "qa": {"max_unknown_fraction": max_unknown}}
    path.write_text(yaml.safe_dump(data))
    return path


def _flag(flag_id: str, kind: str = "missing_part") -> dict:
    return {
        "flag_id": flag_id, "kind": kind, "target": "rail_pos_1", "station_id": "station-1",
        "run_id": RUN, "opened_ts": BASE, "closed_ts": None, "frame_ids": [1],
    }


def _audit_dir(tmp_path, flags, marks=()) -> Path:
    audit = tmp_path / "audit"
    audit.mkdir()
    (audit / "flags.json").write_text(json.dumps(flags) + "\n")
    if marks:
        (audit / "verdicts.jsonl").write_text(
            "".join(json.dumps(m) + "\n" for m in marks)
        )
    return audit


def _mark(flag_id, verdict, note="") -> dict:
    return {"flag_id": flag_id, "verdict": verdict, "note": note, "reviewer": "alice", "ts": BASE}


def _measurements(tmp_path) -> None:
    for sub in ("synthetic", "v1", "v2"):
        (tmp_path / "measurements" / sub).mkdir(parents=True)


# --------------------------------------------------------------------------- #
# AC5: precision 2/3, reviewed 3, unreviewed 1 (subprocess, hand-computed)
# --------------------------------------------------------------------------- #
def test_score_three_reviewed_two_correct_one_incorrect_one_unreviewed(tmp_path):
    _measurements(tmp_path)
    flags = [_flag(f"{RUN}:missing_part:rail_pos_1:{i}") for i in range(1, 5)]
    marks = [
        _mark(flags[0]["flag_id"], "correct"),
        _mark(flags[1]["flag_id"], "correct"),
        _mark(flags[2]["flag_id"], "incorrect"),
    ]  # flags[3] unreviewed
    audit = _audit_dir(tmp_path, flags, marks)
    cfg = _config(tmp_path / "station.yaml")
    log = _healthy_log(tmp_path / "log.db")

    result = subprocess.run(
        [sys.executable, "-m", "station_watch", "audit", "score", "--audit", str(audit),
         "--config", str(cfg), "--log", str(log), "--dataset-kind", "synthetic",
         "--out", "measurements/synthetic/score.json"],
        cwd=tmp_path, capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads((tmp_path / "measurements/synthetic/score.json").read_text())
    kind = payload["metrics"]["by_kind"]["missing_part"]
    assert kind["flags"] == 4
    assert kind["reviewed"] == 3
    assert kind["correct"] == 2
    assert kind["incorrect"] == 1
    assert kind["unreviewed"] == 1
    assert abs(kind["precision"] - 2 / 3) < 1e-9
    assert payload["metrics"]["overall"]["precision"] == pytest.approx(2 / 3)
    assert payload["metrics"]["qa"]["status"] == "pass"
    assert payload["provenance"]["dataset_kind"] == "synthetic"
    assert payload["provenance"]["dataset"] == RUN


# --------------------------------------------------------------------------- #
# AC6: no marks -> no_reviewed_flags; QA failing -> qa_failed; neither has metrics
# --------------------------------------------------------------------------- #
def test_no_marks_writes_no_reviewed_flags_and_no_metrics(tmp_path, monkeypatch):
    _measurements(tmp_path)
    monkeypatch.chdir(tmp_path)
    audit = _audit_dir(tmp_path, [_flag(f"{RUN}:missing_part:rail_pos_1:1")])
    rc = score_audit(
        audit_dir=audit, config_path=_config(tmp_path / "c.yaml"),
        log_path=_healthy_log(tmp_path / "log.db"), dataset_kind="synthetic",
        out="measurements/synthetic/score.json", force_out=False,
    )
    assert rc == 0
    payload = json.loads((tmp_path / "measurements/synthetic/score.json").read_text())
    assert payload["status"] == "no_reviewed_flags"
    assert "metrics" not in payload


def test_qa_failing_session_writes_qa_failed_and_no_metrics(tmp_path, monkeypatch):
    _measurements(tmp_path)
    monkeypatch.chdir(tmp_path)
    fid = f"{RUN}:missing_part:rail_pos_1:1"
    audit = _audit_dir(tmp_path, [_flag(fid)], [_mark(fid, "correct")])
    rc = score_audit(
        audit_dir=audit, config_path=_config(tmp_path / "c.yaml", max_unknown=0.1),
        log_path=_empty_log(tmp_path / "log.db"), dataset_kind="synthetic",
        out="measurements/synthetic/score.json", force_out=False,
    )
    assert rc == 0
    payload = json.loads((tmp_path / "measurements/synthetic/score.json").read_text())
    assert payload["status"] == "qa_failed"
    assert "no verdicts" in payload["reason"]
    assert "metrics" not in payload


# --------------------------------------------------------------------------- #
# AC7: --out confinement; --force-out overrides with a warning; writes nothing on refuse
# --------------------------------------------------------------------------- #
def test_synthetic_out_outside_tree_refused_and_writes_nothing(tmp_path, monkeypatch, capsys):
    _measurements(tmp_path)
    (tmp_path / "outside").mkdir()
    monkeypatch.chdir(tmp_path)
    audit = _audit_dir(tmp_path, [_flag(f"{RUN}:missing_part:rail_pos_1:1")])
    rc = score_audit(
        audit_dir=audit, config_path=_config(tmp_path / "c.yaml"),
        log_path=_healthy_log(tmp_path / "log.db"), dataset_kind="synthetic",
        out="measurements/v1/score.json", force_out=False,  # v1 is for real, not synthetic
    )
    assert rc == 2
    assert "synthetic" in capsys.readouterr().err
    assert not (tmp_path / "measurements/v1/score.json").exists()


def test_real_out_outside_tree_refused(tmp_path, monkeypatch, capsys):
    _measurements(tmp_path)
    monkeypatch.chdir(tmp_path)
    audit = _audit_dir(tmp_path, [_flag(f"{RUN}:missing_part:rail_pos_1:1")])
    rc = score_audit(
        audit_dir=audit, config_path=_config(tmp_path / "c.yaml"),
        log_path=_healthy_log(tmp_path / "log.db"), dataset_kind="real",
        out="measurements/synthetic/score.json", force_out=False,
    )
    assert rc == 2
    assert "real" in capsys.readouterr().err


def test_force_out_writes_outside_with_a_warning(tmp_path, monkeypatch, capsys):
    _measurements(tmp_path)
    (tmp_path / "outside").mkdir()
    monkeypatch.chdir(tmp_path)
    fid = f"{RUN}:missing_part:rail_pos_1:1"
    audit = _audit_dir(tmp_path, [_flag(fid)], [_mark(fid, "correct")])
    rc = score_audit(
        audit_dir=audit, config_path=_config(tmp_path / "c.yaml"),
        log_path=_healthy_log(tmp_path / "log.db"), dataset_kind="synthetic",
        out="outside/score.json", force_out=True,
    )
    assert rc == 0
    assert "WARNING" in capsys.readouterr().err
    assert (tmp_path / "outside/score.json").exists()


# --------------------------------------------------------------------------- #
# AC8: the Log's bytes are unchanged after score; provenance manifest hash is defined
# --------------------------------------------------------------------------- #
def test_log_bytes_unchanged_and_manifest_sha_is_verdicts_plus_flags(tmp_path, monkeypatch):
    _measurements(tmp_path)
    monkeypatch.chdir(tmp_path)
    fid = f"{RUN}:missing_part:rail_pos_1:1"
    audit = _audit_dir(tmp_path, [_flag(fid)], [_mark(fid, "correct")])
    log = _healthy_log(tmp_path / "log.db")
    before = hashlib.sha256(log.read_bytes()).hexdigest()

    score_audit(
        audit_dir=audit, config_path=_config(tmp_path / "c.yaml"), log_path=log,
        dataset_kind="synthetic", out="measurements/synthetic/score.json", force_out=False,
    )
    assert hashlib.sha256(log.read_bytes()).hexdigest() == before

    payload = json.loads((tmp_path / "measurements/synthetic/score.json").read_text())
    expected = hashlib.sha256(
        (audit / "verdicts.jsonl").read_bytes() + (audit / "flags.json").read_bytes()
    ).hexdigest()
    assert payload["provenance"]["manifest_sha256"] == expected
