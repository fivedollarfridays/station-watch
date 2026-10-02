"""Reviewer verdicts: idempotent append, newest-line-wins, and the ``audit mark`` CLI.

The verdicts layer is the one path the review server, the ``audit mark`` command and
``audit score`` all write through: a mark that equals a flag's effective verdict
appends nothing, a changed mark appends one line that records the new reviewer and ts,
and the effective verdict is always the newest line naming the flag.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from station_watch.audit.verdicts import append_mark, effective, flag_ids, read_all

RUN = "run-verdicts"


def _flag(flag_id: str, kind: str = "missing_part", target: str = "rail_pos_1") -> dict:
    return {
        "flag_id": flag_id,
        "kind": kind,
        "target": target,
        "station_id": "station-1",
        "run_id": RUN,
        "opened_ts": "2026-10-01T00:00:00.000000+00:00",
        "closed_ts": None,
        "frame_ids": [1, 2],
    }


def _audit_dir(tmp_path, flags) -> Path:
    audit = tmp_path / "audit"
    audit.mkdir()
    (audit / "flags.json").write_text(json.dumps(flags) + "\n")
    return audit


def _lines(audit: Path) -> list[str]:
    path = audit / "verdicts.jsonl"
    return [line for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def test_append_then_identical_mark_appends_nothing(tmp_path):
    audit = _audit_dir(tmp_path, [_flag(f"{RUN}:missing_part:rail_pos_1:1")])
    fid = f"{RUN}:missing_part:rail_pos_1:1"

    assert append_mark(audit, fid, "correct", "", "alice", ts="t1") is True
    assert append_mark(audit, fid, "correct", "", "alice", ts="t2") is False  # identical -> no-op
    assert len(_lines(audit)) == 1
    assert effective(audit)[fid]["verdict"] == "correct"
    assert effective(audit)[fid]["ts"] == "t1"


def test_a_changed_mark_appends_one_line_recording_new_reviewer_and_ts(tmp_path):
    audit = _audit_dir(tmp_path, [_flag(f"{RUN}:missing_part:rail_pos_1:1")])
    fid = f"{RUN}:missing_part:rail_pos_1:1"

    append_mark(audit, fid, "correct", "", "alice", ts="t1")
    assert append_mark(audit, fid, "incorrect", "a typo", "bob", ts="t2") is True
    assert len(_lines(audit)) == 2
    eff = effective(audit)[fid]
    assert eff["verdict"] == "incorrect"
    assert eff["reviewer"] == "bob"
    assert eff["ts"] == "t2"
    assert eff["note"] == "a typo"
    # Both lines survive, so who marked what and when is never lost.
    assert [json.loads(line)["reviewer"] for line in _lines(audit)] == ["alice", "bob"]


def test_flag_ids_reads_flags_json(tmp_path):
    audit = _audit_dir(tmp_path, [_flag("a"), _flag("b")])
    assert flag_ids(audit) == {"a", "b"}


def test_read_all_is_empty_before_any_mark(tmp_path):
    audit = _audit_dir(tmp_path, [_flag("a")])
    assert read_all(audit) == []


# --------------------------------------------------------------------------- #
# audit mark CLI: same idempotence, and an unknown flag id is refused
# --------------------------------------------------------------------------- #
def _mark_cli(audit, flag_id, verdict, *extra):
    return subprocess.run(
        [sys.executable, "-m", "station_watch", "audit", "mark", "--audit", str(audit),
         flag_id, verdict, *extra],
        capture_output=True, text=True, timeout=60,
    )


def test_audit_mark_is_idempotent_and_rejects_unknown_flag(tmp_path):
    fid = f"{RUN}:missing_part:rail_pos_1:1"
    audit = _audit_dir(tmp_path, [_flag(fid)])

    first = _mark_cli(audit, fid, "correct", "--reviewer", "alice")
    assert first.returncode == 0, first.stderr
    again = _mark_cli(audit, fid, "correct", "--reviewer", "alice")
    assert again.returncode == 0, again.stderr
    assert len(_lines(audit)) == 1, "an identical CLI mark appends nothing"
    assert "unchanged" in again.stdout

    changed = _mark_cli(audit, fid, "incorrect", "--reviewer", "alice")
    assert changed.returncode == 0
    assert len(_lines(audit)) == 2

    unknown = _mark_cli(audit, "no-such-flag", "correct")
    assert unknown.returncode == 2
    assert "unknown flag_id" in unknown.stderr
