"""Session QA: a per-session unknown budget a mostly blind session cannot pass.

``session_qa`` reads one session's Log read-only (through ``LogReader``) and
time-weights each verdict from its ts to the next; the last verdict holds to the
newest cycle row's ts. A verdict gap longer than ``watchdog.cycle_window_s`` is
unobservable for its whole length (missing data is not healthy, K1), and so is
any ``unobservable`` verdict. A session passes only when its unobservable
fraction is within ``qa.max_unknown_fraction`` and every required slot / keep-out
zone is within ``qa.max_target_unknown_fraction`` (when set). The CLI exits 0 on
pass, 1 on fail, 2 on a startup error; it never changes the Log's bytes.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import yaml

from station_watch.clock import offset_iso
from station_watch.config import StationConfig
from station_watch.log import Log
from station_watch.qa import SessionQA, session_qa
from station_watch.records import (
    BlindReason,
    CycleCompleted,
    Observation,
    ObservationKind,
    Verdict,
    VerdictState,
)

sys.path.insert(0, str(Path(__file__).parent))
from helpers.records import HEALTH_CONFIG  # noqa: E402
from helpers.synth_video import write_synth_clip  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BASE = "2026-10-01T00:00:00.000000+00:00"
RUN_ID = "run-qa-test"


# --------------------------------------------------------------------------- #
# Log + config builders
# --------------------------------------------------------------------------- #
def _verdict(offset: float, state: VerdictState, seq: int) -> Verdict:
    reasons = (BlindReason.DARK,) if state is VerdictState.UNOBSERVABLE else ()
    return Verdict(
        station_id=HEALTH_CONFIG["station_id"],
        ts=offset_iso(BASE, offset),
        state=state,
        faults=(),
        blind_reasons=reasons,
        seq=seq,
        run_id=RUN_ID,
    )


def _observation(offset: float, kind: ObservationKind, target: str, frame_id: int) -> Observation:
    return Observation(
        station_id=HEALTH_CONFIG["station_id"],
        frame_id=frame_id,
        ts=offset_iso(BASE, offset),
        kind=kind,
        target=target,
        method="test",
        confidence_ceiling=1.0,
        detector_output={},
        run_id=RUN_ID,
    )


def build_log(path, *, verdicts, cycle_offset=None, observations=()):
    """Write a Log of ``(offset, state)`` verdicts (plus optional cycle/obs rows)."""
    with Log(path) as log:
        for seq, (offset, state) in enumerate(verdicts):
            log.append(_verdict(offset, state, seq))
        for frame_id, (offset, kind, target) in enumerate(observations):
            log.append(_observation(offset, kind, target, frame_id))
        if cycle_offset is not None:
            log.append(
                CycleCompleted(ts=offset_iso(BASE, cycle_offset), cycle=0, stages=(), run_id=RUN_ID)
            )


QA = {"max_unknown_fraction": 0.5}


def _config(**over) -> dict:
    data = {**HEALTH_CONFIG, **over}
    return data


def _write_config(path, qa=None, **over) -> Path:
    data = _config(**over)
    if qa is not None:
        data["qa"] = qa
    path.write_text(yaml.safe_dump(data))
    return path


def _entry_point() -> list[str]:
    script = Path(sys.executable).parent / "station-watch"
    return [str(script)] if script.exists() else [sys.executable, "-m", "station_watch"]


def _run_cli(args, cwd=None):
    return subprocess.run(
        [*_entry_point(), *args], cwd=cwd, capture_output=True, text=True, timeout=120
    )


# --------------------------------------------------------------------------- #
# Proving test (subprocess exit codes)
# --------------------------------------------------------------------------- #
def _eighty_percent_unobservable(path):
    # [0,1),[1,2) healthy (2 s observable); [2,10) unobservable (8 s). cycle at 10.
    verdicts = [(0.0, VerdictState.HEALTHY), (1.0, VerdictState.HEALTHY)]
    verdicts += [(float(t), VerdictState.UNOBSERVABLE) for t in range(2, 10)]
    build_log(path, verdicts=verdicts, cycle_offset=10.0)


def test_proving_80pct_unobservable_fails_tight_budget_passes_loose(tmp_path):
    log = tmp_path / "station.db"
    _eighty_percent_unobservable(log)
    tight = _write_config(tmp_path / "tight.yaml", qa={"max_unknown_fraction": 0.2})
    loose = _write_config(tmp_path / "loose.yaml", qa={"max_unknown_fraction": 0.9})

    fail = _run_cli(["qa", "--config", str(tight), "--log", str(log)])
    assert fail.returncode == 1, fail.stderr
    assert "0.800" in fail.stdout, fail.stdout

    ok = _run_cli(["qa", "--config", str(loose), "--log", str(log)])
    assert ok.returncode == 0, ok.stderr


# --------------------------------------------------------------------------- #
# Gap longer than cycle_window_s is unobservable (hand-computed)
# --------------------------------------------------------------------------- #
def test_gap_longer_than_cycle_window_counts_unobservable(tmp_path):
    log = tmp_path / "station.db"
    # healthy at 0,1,10; cycle at 11. Gap [1,10) is 9 s > cycle_window 2 -> unobservable.
    build_log(
        log,
        verdicts=[
            (0.0, VerdictState.HEALTHY),
            (1.0, VerdictState.HEALTHY),
            (10.0, VerdictState.HEALTHY),
        ],
        cycle_offset=11.0,
    )
    config = _config(watchdog={**HEALTH_CONFIG["watchdog"], "cycle_window_s": 2.0}, qa=QA)
    result = session_qa(config, log)
    assert result.session_s == 11.0
    assert result.observable_s == 2.0  # [0,1) and [10,11)
    assert abs(result.unobservable_fraction - 9.0 / 11.0) < 1e-9


# --------------------------------------------------------------------------- #
# No verdicts / missing / undecodable each fail, never pass
# --------------------------------------------------------------------------- #
def test_no_verdicts_fails(tmp_path):
    log = tmp_path / "station.db"
    build_log(log, verdicts=[], cycle_offset=5.0)
    config = _config(qa={"max_unknown_fraction": 0.5})
    result = session_qa(config, log)
    assert result.status == "fail"
    assert "no verdicts" in result.reason


def test_missing_log_fails(tmp_path):
    config = _config(qa=QA)
    result = session_qa(config, tmp_path / "does-not-exist.db")
    assert result.status == "fail"
    assert "missing" in result.reason.lower()


def test_undecodable_row_fails_never_passes(tmp_path):
    import sqlite3

    log = tmp_path / "station.db"
    build_log(log, verdicts=[(0.0, VerdictState.HEALTHY)], cycle_offset=1.0)
    import json

    conn = sqlite3.connect(str(log))
    # Valid JSON (the Log's expression index must compute json_extract over it) but
    # missing every Verdict field, so rebuilding the record raises -> undecodable.
    rid = f"{RUN_ID}:verdict:station-1:999"
    conn.execute(
        "INSERT INTO records (record_id, kind, ts, run_id, body) VALUES (?,?,?,?,?)",
        (rid, "verdict", offset_iso(BASE, 9.0), RUN_ID, json.dumps({"record_id": rid})),
    )
    conn.commit()
    conn.close()
    result = session_qa(_config(qa=QA), log)
    assert result.status == "fail"
    assert str(log) in result.reason or "undecodable" in result.reason.lower()


# --------------------------------------------------------------------------- #
# Per-target: a slot unknown for most of the session fails, named
# --------------------------------------------------------------------------- #
SLOT_DETECT = {
    **HEALTH_CONFIG["detect"],
    "rail_positions": {"rail_pos_1": [[-1, -1], [1, -1], [1, 1], [-1, 1]]},
}


def test_slot_mostly_unknown_fails_target_budget_names_slot(tmp_path):
    log = tmp_path / "station.db"
    # Fully observable (healthy throughout), but rail_pos_1 is part_unknown all along.
    build_log(
        log,
        verdicts=[(float(t), VerdictState.HEALTHY) for t in range(0, 10)],
        cycle_offset=10.0,
        observations=[(0.0, ObservationKind.PART_UNKNOWN, "rail_pos_1")],
    )
    config = _config(
        required_slots=["rail_pos_1"],
        detect=SLOT_DETECT,
        qa={"max_unknown_fraction": 0.9, "max_target_unknown_fraction": 0.3},
    )
    result = session_qa(config, log)
    assert result.status == "fail"
    assert "rail_pos_1" in result.reason
    assert result.target_unknown_fraction["rail_pos_1"] > 0.9


def test_slot_mostly_known_passes_target_budget(tmp_path):
    log = tmp_path / "station.db"
    build_log(
        log,
        verdicts=[(float(t), VerdictState.HEALTHY) for t in range(0, 10)],
        cycle_offset=10.0,
        observations=[(0.0, ObservationKind.PART_PRESENT, "rail_pos_1")],
    )
    config = _config(
        required_slots=["rail_pos_1"],
        detect=SLOT_DETECT,
        qa={"max_unknown_fraction": 0.9, "max_target_unknown_fraction": 0.3},
    )
    result = session_qa(config, log)
    assert result.status == "pass", result.reason
    assert result.target_unknown_fraction["rail_pos_1"] == 0.0


# --------------------------------------------------------------------------- #
# K9: qa without max_unknown_fraction exits 2; run ignores the section
# --------------------------------------------------------------------------- #
def test_qa_without_max_unknown_fraction_exits_2_naming_key(tmp_path):
    log = tmp_path / "station.db"
    build_log(log, verdicts=[(0.0, VerdictState.HEALTHY)], cycle_offset=1.0)
    config = _write_config(tmp_path / "noqa.yaml")  # no qa section
    result = _run_cli(["qa", "--config", str(config), "--log", str(log)])
    assert result.returncode == 2, result.stdout
    assert "qa.max_unknown_fraction" in result.stderr


def test_run_config_unaffected_by_qa_section():
    # The run path builds a StationConfig, which ignores an unknown qa section.
    with_qa = StationConfig.from_mapping({**HEALTH_CONFIG, "qa": {"max_unknown_fraction": 0.5}})
    without = StationConfig.from_mapping(dict(HEALTH_CONFIG))
    assert with_qa == without


# --------------------------------------------------------------------------- #
# The Log's bytes are unchanged after qa runs
# --------------------------------------------------------------------------- #
def test_log_bytes_unchanged_after_qa(tmp_path):
    log = tmp_path / "station.db"
    _eighty_percent_unobservable(log)
    before = hashlib.sha256(log.read_bytes()).hexdigest()
    session_qa(_config(qa={"max_unknown_fraction": 0.5}), log)
    _run_cli(
        [
            "qa",
            "--config",
            str(_write_config(tmp_path / "c.yaml", qa={"max_unknown_fraction": 0.5})),
            "--log",
            str(log),
        ]
    )
    after = hashlib.sha256(log.read_bytes()).hexdigest()
    assert before == after


# --------------------------------------------------------------------------- #
# --out writes a measurement file (provenance + metrics incl. status)
# --------------------------------------------------------------------------- #
def test_qa_out_writes_measurement_file(tmp_path):
    (tmp_path / "measurements" / "synthetic").mkdir(parents=True)
    log = tmp_path / "station.db"
    _eighty_percent_unobservable(log)
    config = _write_config(tmp_path / "c.yaml", qa={"max_unknown_fraction": 0.9})
    out_rel = "measurements/synthetic/qa.json"
    result = _run_cli(
        [
            "qa",
            "--config",
            str(config),
            "--log",
            str(log),
            "--out",
            out_rel,
            "--dataset-kind",
            "synthetic",
        ],
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    import json

    payload = json.loads((tmp_path / out_rel).read_text())
    assert payload["provenance"]["dataset_kind"] == "synthetic"
    assert payload["metrics"]["status"] == "pass"
    assert abs(payload["metrics"]["unobservable_fraction"] - 0.8) < 1e-9


# --------------------------------------------------------------------------- #
# E2E conformance: real run then real qa on a dark clip vs a clean clip
# --------------------------------------------------------------------------- #
def _e2e_config(tmp_path) -> Path:
    import shutil

    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    data = yaml.safe_load((ROOT / "config" / "station-example.yaml").read_text())
    data["qa"] = {"max_unknown_fraction": 0.5}
    cfg = tmp_path / "config" / "station.yaml"
    cfg.write_text(yaml.safe_dump(data))
    shutil.copy(ROOT / "config" / "station-example.yaml", tmp_path / "config" / "example.yaml")
    return cfg


def _run_and_qa(tmp_path, clip, cfg, label):
    log = tmp_path / f"{label}.db"
    run = _run_cli(
        [
            "run",
            "--config",
            str(cfg),
            "--source",
            str(clip),
            "--log",
            str(log),
            "--alarm-record",
            str(tmp_path / f"{label}.jsonl"),
        ],
        cwd=tmp_path,
    )
    assert run.returncode == 0, run.stderr
    return _run_cli(["qa", "--config", str(cfg), "--log", str(log)], cwd=tmp_path)


def test_e2e_dark_clip_fails_clean_clip_passes(tmp_path):
    cfg = _e2e_config(tmp_path)
    dark = write_synth_clip(tmp_path / "dark", frames=60, dark_from=6, dark_until=60)
    clean = write_synth_clip(tmp_path / "clean", frames=60)

    dark_qa = _run_and_qa(tmp_path, dark, cfg, "dark")
    assert dark_qa.returncode == 1, dark_qa.stdout + dark_qa.stderr
    assert "unobservable" in dark_qa.stdout.lower()

    clean_qa = _run_and_qa(tmp_path, clean, cfg, "clean")
    assert clean_qa.returncode == 0, clean_qa.stdout + clean_qa.stderr


# --------------------------------------------------------------------------- #
# SessionQA shape (interface consumed by HF3.6/7/10)
# --------------------------------------------------------------------------- #
def test_sessionqa_is_frozen_with_interface_fields(tmp_path):
    log = tmp_path / "station.db"
    _eighty_percent_unobservable(log)
    result = session_qa(_config(qa={"max_unknown_fraction": 0.5}), log)
    assert isinstance(result, SessionQA)
    for field in (
        "status",
        "reason",
        "session_s",
        "observable_s",
        "unobservable_fraction",
        "target_unknown_fraction",
        "verdicts",
        "max_unknown_fraction",
    ):
        assert hasattr(result, field)
    assert result.verdicts == 10
