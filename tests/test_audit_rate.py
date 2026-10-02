"""``audit rate``: false-alarm rate per hour on a normal-work session.

The denominator is the session's *observable* hours from HF3.3 (blind time never
inflates them). A fixture Log of 2 observable hours with 4 flags (1 correct, 2
incorrect, 1 unreviewed) gives 2.0 flags/hr, 0.5 true, 1.0 false, 0.5 unreviewed
per hour. An hour of blind time leaves ``observed_hours`` unchanged. Under
``--min-hours`` the file carries ``status: insufficient_duration`` and no ``metrics``;
a QA-failing session carries ``status: qa_failed``. Blind episodes are reported per
hour in their own block, never folded into the detection flag rates.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from station_watch.audit.rate import rate_audit
from station_watch.clock import offset_iso
from station_watch.log import Log
from station_watch.records import (
    CycleCompleted,
    Verdict,
    VerdictState,
)

sys.path.insert(0, str(Path(__file__).parent))
from helpers.records import HEALTH_CONFIG  # noqa: E402

BASE = "2026-10-01T00:00:00.000000+00:00"
RUN = "run-rate"
HOUR = 3600.0


def _config(path: Path, max_unknown=0.95, cycle_window_s=4000.0) -> Path:
    data = {
        **HEALTH_CONFIG,
        "qa": {"max_unknown_fraction": max_unknown},
        "watchdog": {**HEALTH_CONFIG["watchdog"], "cycle_window_s": cycle_window_s},
    }
    path.write_text(yaml.safe_dump(data))
    return path


def _log(path: Path, verdicts, end_offset) -> Path:
    """A Log of the given ``(offset_s, state)`` verdicts, closed by a cycle at ``end``."""
    with Log(path) as log:
        for seq, (offset, state) in enumerate(verdicts):
            log.append(
                Verdict(
                    station_id="station-1",
                    ts=offset_iso(BASE, offset),
                    state=state,
                    faults=(),
                    blind_reasons=(),
                    seq=seq,
                    run_id=RUN,
                )
            )
        log.append(CycleCompleted(ts=offset_iso(BASE, end_offset), cycle=0, stages=(), run_id=RUN))
    return path


def _two_observable_hours(path: Path) -> Path:
    """Three HEALTHY verdicts an hour apart: a 2-observable-hour session."""
    return _log(
        path,
        [
            (0.0, VerdictState.HEALTHY),
            (HOUR, VerdictState.HEALTHY),
            (2 * HOUR, VerdictState.HEALTHY),
        ],
        end_offset=2 * HOUR,
    )


def _flag(flag_id: str, kind: str = "missing_part", target: str = "rail_pos_1") -> dict:
    return {
        "flag_id": flag_id,
        "kind": kind,
        "target": target,
        "station_id": "station-1",
        "run_id": RUN,
        "opened_ts": BASE,
        "closed_ts": None,
        "frame_ids": [1],
    }


def _blind_flag(flag_id: str, reason: str = "dark") -> dict:
    return _flag(flag_id, kind=f"unobservable:{reason}", target=reason)


def _mark(flag_id, verdict) -> dict:
    return {"flag_id": flag_id, "verdict": verdict, "note": "", "reviewer": "alice", "ts": BASE}


def _audit_dir(tmp_path, flags, marks=()) -> Path:
    audit = tmp_path / "audit"
    audit.mkdir()
    (audit / "flags.json").write_text(json.dumps(flags) + "\n")
    if marks:
        (audit / "verdicts.jsonl").write_text("".join(json.dumps(m) + "\n" for m in marks))
    return audit


def _measurements(tmp_path) -> None:
    for sub in ("synthetic", "v1", "v2"):
        (tmp_path / "measurements" / sub).mkdir(parents=True)


def _four_flags():
    flags = [_flag(f"{RUN}:missing_part:rail_pos_1:{i}") for i in range(1, 5)]
    marks = [
        _mark(flags[0]["flag_id"], "correct"),
        _mark(flags[1]["flag_id"], "incorrect"),
        _mark(flags[2]["flag_id"], "incorrect"),
    ]  # flags[3] unreviewed
    return flags, marks


# --------------------------------------------------------------------------- #
# AC1: 2 observable hours, 4 flags -> 2.0/hr, 0.5 true, 1.0 false, 0.5 unreviewed
# --------------------------------------------------------------------------- #
def test_rate_proving_two_hours_four_flags(tmp_path):
    _measurements(tmp_path)
    flags, marks = _four_flags()
    audit = _audit_dir(tmp_path, flags, marks)
    cfg = _config(tmp_path / "station.yaml")
    log = _two_observable_hours(tmp_path / "log.db")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "station_watch",
            "audit",
            "rate",
            "--audit",
            str(audit),
            "--config",
            str(cfg),
            "--log",
            str(log),
            "--dataset-kind",
            "synthetic",
            "--out",
            "measurements/synthetic/rate.json",
            "--min-hours",
            "1.0",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads((tmp_path / "measurements/synthetic/rate.json").read_text())
    metrics = payload["metrics"]
    assert metrics["observed_hours"] == pytest.approx(2.0)
    overall = metrics["overall"]
    assert overall["flags"] == 4
    assert (overall["true"], overall["false"], overall["unreviewed"]) == (1, 2, 1)
    assert overall["flags_per_hour"] == pytest.approx(2.0)
    assert overall["true_per_hour"] == pytest.approx(0.5)
    assert overall["false_per_hour"] == pytest.approx(1.0)
    assert overall["unreviewed_per_hour"] == pytest.approx(0.5)
    kind = metrics["per_kind"]["missing_part"]
    assert kind["flags_per_hour"] == pytest.approx(2.0)
    assert kind["false_per_hour"] == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# AC2: an hour of blind time in the session does not change observed_hours
# --------------------------------------------------------------------------- #
def test_blind_time_does_not_inflate_observed_hours(tmp_path, monkeypatch):
    _measurements(tmp_path)
    monkeypatch.chdir(tmp_path)
    flags, marks = _four_flags()
    audit = _audit_dir(tmp_path, flags, marks)
    cfg = _config(tmp_path / "c.yaml")
    # Same 2 observable hours, then an extra UNOBSERVABLE hour before the final cycle.
    log = _log(
        tmp_path / "log.db",
        [
            (0.0, VerdictState.HEALTHY),
            (HOUR, VerdictState.HEALTHY),
            (2 * HOUR, VerdictState.HEALTHY),
            (2 * HOUR, VerdictState.UNOBSERVABLE),
        ],
        end_offset=3 * HOUR,
    )

    rc = rate_audit(
        audit_dir=audit,
        config_path=cfg,
        log_path=log,
        dataset_kind="synthetic",
        out="measurements/synthetic/rate.json",
        min_hours=1.0,
        force_out=False,
    )
    assert rc == 0
    metrics = json.loads((tmp_path / "measurements/synthetic/rate.json").read_text())["metrics"]
    assert metrics["observed_hours"] == pytest.approx(2.0)
    assert metrics["session_hours"] == pytest.approx(3.0)
    assert metrics["overall"]["flags_per_hour"] == pytest.approx(2.0)


# --------------------------------------------------------------------------- #
# AC3: under --min-hours -> insufficient_duration; QA fail -> qa_failed; no metrics
# --------------------------------------------------------------------------- #
def test_insufficient_duration_names_both_numbers_and_no_metrics(tmp_path, monkeypatch):
    _measurements(tmp_path)
    monkeypatch.chdir(tmp_path)
    flags, marks = _four_flags()
    audit = _audit_dir(tmp_path, flags, marks)
    cfg = _config(tmp_path / "c.yaml")
    log = _two_observable_hours(tmp_path / "log.db")  # 2.0 observed hours

    rc = rate_audit(
        audit_dir=audit,
        config_path=cfg,
        log_path=log,
        dataset_kind="synthetic",
        out="measurements/synthetic/rate.json",
        min_hours=5.0,
        force_out=False,
    )
    assert rc == 0
    payload = json.loads((tmp_path / "measurements/synthetic/rate.json").read_text())
    assert payload["status"] == "insufficient_duration"
    assert "metrics" not in payload
    assert "2" in payload["reason"] and "5" in payload["reason"]  # both numbers named


def test_qa_failing_session_writes_qa_failed_and_no_metrics(tmp_path, monkeypatch):
    _measurements(tmp_path)
    monkeypatch.chdir(tmp_path)
    flags, marks = _four_flags()
    audit = _audit_dir(tmp_path, flags, marks)
    # A long gap (> cycle_window_s) makes the session almost entirely unobservable.
    cfg = _config(tmp_path / "c.yaml", max_unknown=0.1, cycle_window_s=1.0)
    log = _log(
        tmp_path / "log.db",
        [
            (0.0, VerdictState.HEALTHY),
            (2 * HOUR, VerdictState.HEALTHY),
        ],
        end_offset=2 * HOUR,
    )

    rc = rate_audit(
        audit_dir=audit,
        config_path=cfg,
        log_path=log,
        dataset_kind="synthetic",
        out="measurements/synthetic/rate.json",
        min_hours=0.0,
        force_out=False,
    )
    assert rc == 0
    payload = json.loads((tmp_path / "measurements/synthetic/rate.json").read_text())
    assert payload["status"] == "qa_failed"
    assert "metrics" not in payload


# --------------------------------------------------------------------------- #
# AC4: blind episodes land only in their own block, never in the detection rates
# --------------------------------------------------------------------------- #
def test_blind_episodes_only_in_their_own_block(tmp_path, monkeypatch):
    _measurements(tmp_path)
    monkeypatch.chdir(tmp_path)
    detection = [_flag(f"{RUN}:missing_part:rail_pos_1:1")]
    blind = [_blind_flag(f"{RUN}:blind:dark:7"), _blind_flag(f"{RUN}:blind:dark:9")]
    audit = _audit_dir(tmp_path, detection + blind)
    cfg = _config(tmp_path / "c.yaml")
    log = _two_observable_hours(tmp_path / "log.db")

    rc = rate_audit(
        audit_dir=audit,
        config_path=cfg,
        log_path=log,
        dataset_kind="synthetic",
        out="measurements/synthetic/rate.json",
        min_hours=1.0,
        force_out=False,
    )
    assert rc == 0
    metrics = json.loads((tmp_path / "measurements/synthetic/rate.json").read_text())["metrics"]
    # Detection rates count only the one missing_part flag, not the two blind episodes.
    assert metrics["overall"]["flags"] == 1
    assert "unobservable:dark" not in metrics["per_kind"]
    assert all(not k.startswith("unobservable:") for k in metrics["per_kind"])
    # The two blind episodes live in their own block at 2 episodes / 2 hours = 1.0/hr.
    assert metrics["blind"]["episodes"] == 2
    assert metrics["blind"]["per_hour"] == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# AC3 extra: --out confinement is enforced (as in HF3.6)
# --------------------------------------------------------------------------- #
def test_out_outside_tree_refused_and_writes_nothing(tmp_path, monkeypatch, capsys):
    _measurements(tmp_path)
    monkeypatch.chdir(tmp_path)
    audit = _audit_dir(tmp_path, [_flag(f"{RUN}:missing_part:rail_pos_1:1")])
    rc = rate_audit(
        audit_dir=audit,
        config_path=_config(tmp_path / "c.yaml"),
        log_path=_two_observable_hours(tmp_path / "log.db"),
        dataset_kind="synthetic",
        out="measurements/v1/rate.json",
        min_hours=1.0,
        force_out=False,
    )
    assert rc == 2
    assert "synthetic" in capsys.readouterr().err
    assert not (tmp_path / "measurements/v1/rate.json").exists()
