"""``audit score``: reviewed precision from a session's flags and reviewer verdicts.

Reads ``flags.json`` (HF3.5) and ``verdicts.jsonl`` (HF3.6) under the audit dir and
the session's QA (HF3.3), and writes one measurement file through
:func:`~station_watch.evaluate.provenance.write_measurement` -- no second format.
Per flag kind and overall it reports ``flags``, ``reviewed``, ``correct``,
``incorrect``, ``unreviewed`` and ``precision = correct / reviewed`` (null when
nothing of that kind was reviewed), plus the session's QA result. A session that fails
QA writes ``status: qa_failed`` (with the QA reason) and no ``metrics``; a session with
no reviewed flags writes ``status: no_reviewed_flags`` and no ``metrics``. ``--out`` is
confined to the dataset kind's own measurement tree; the Log is only ever read.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import yaml

from station_watch.audit.flags import FLAGS_FILE, load_flags_json
from station_watch.audit.verdicts import VERDICTS_FILE, effective, verdicts_path
from station_watch.board.reader import BoardLogError, LogReader
from station_watch.config import validate_required_keys
from station_watch.evaluate.provenance import (
    DETECTOR,
    build_provenance,
    confine_out,
    write_measurement,
)
from station_watch.qa import session_qa, validate_qa_config


def _load_config(path) -> dict:
    data = yaml.safe_load(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError(f"station config must be a YAML mapping, got {type(data).__name__}")
    validate_required_keys(data)
    validate_qa_config(data)
    return data


def _run_ids(flags: list[dict], log_path) -> list[str]:
    ids = sorted({f["run_id"] for f in flags if f.get("run_id")})
    if ids:
        return ids
    try:
        with LogReader(log_path) as reader:
            for kind in ("verdict", "frame", "cycle", "blind"):
                record = reader.newest(kind)
                if record is not None:
                    return [record.run_id]
    except BoardLogError:
        pass
    return []


def _manifest_sha256(audit_dir) -> str:
    """SHA-256 of ``verdicts.jsonl`` and ``flags.json`` concatenated (verdicts first)."""
    digest = hashlib.sha256()
    vpath = verdicts_path(audit_dir)
    digest.update(vpath.read_bytes() if vpath.exists() else b"")
    digest.update((Path(audit_dir) / FLAGS_FILE).read_bytes())
    return digest.hexdigest()


def _tally(flags: list[dict], verdicts: dict) -> dict:
    """``kind -> {flags, reviewed, correct, incorrect}`` over every flag of the session."""
    by_kind: dict[str, dict] = {}
    for flag in flags:
        bucket = by_kind.setdefault(
            flag["kind"], {"flags": 0, "reviewed": 0, "correct": 0, "incorrect": 0}
        )
        bucket["flags"] += 1
        mark = verdicts.get(flag["flag_id"])
        if mark is not None:
            bucket["reviewed"] += 1
            if mark.get("verdict") in ("correct", "incorrect"):
                bucket[mark["verdict"]] += 1
    return by_kind


def _finalize(bucket: dict) -> dict:
    reviewed = bucket["reviewed"]
    return {
        **bucket,
        "unreviewed": bucket["flags"] - reviewed,
        "precision": (bucket["correct"] / reviewed) if reviewed else None,
    }


def _overall(by_kind: dict) -> dict:
    total = {"flags": 0, "reviewed": 0, "correct": 0, "incorrect": 0}
    for bucket in by_kind.values():
        for key in total:
            total[key] += bucket[key]
    return _finalize(total)


def _provenance(audit_dir, log_path, config_path, dataset_kind, flags) -> dict:
    run_ids = _run_ids(flags, log_path)
    return build_provenance(
        dataset=",".join(run_ids) or "unknown",
        dataset_kind=dataset_kind,
        manifest_sha256=_manifest_sha256(audit_dir),
        config_sha256=hashlib.sha256(Path(config_path).read_bytes()).hexdigest(),
        detector=DETECTOR,
        clips=0,
        sessions=run_ids,
    )


def _metrics(flags: list[dict], verdicts: dict, qa) -> dict:
    tally = _tally(flags, verdicts)
    return {
        "by_kind": {kind: _finalize(bucket) for kind, bucket in tally.items()},
        "overall": _overall(tally),
        "qa": {
            "status": qa.status,
            "reason": qa.reason,
            "unobservable_fraction": qa.unobservable_fraction,
        },
    }


def _write(out, provenance, flags, verdicts, qa) -> None:
    if qa.status == "fail":
        write_measurement(out, provenance, status="qa_failed", reason=qa.reason)
    elif not verdicts:
        write_measurement(out, provenance, status="no_reviewed_flags")
    else:
        write_measurement(out, provenance, _metrics(flags, verdicts, qa))


def score_audit(*, audit_dir, config_path, log_path, dataset_kind, out, force_out) -> int:
    """Drive ``station-watch audit score``; 0 on success, 2 on a startup/confinement error."""
    try:
        config = _load_config(config_path)
        resolved_out = confine_out(Path(out), dataset_kind, force_out=force_out)
        flags = load_flags_json(audit_dir)
    except (OSError, KeyError, ValueError, BoardLogError) as exc:
        print(f"station-watch: {exc}", file=sys.stderr)
        return 2
    known = {f["flag_id"] for f in flags}
    verdicts = {fid: v for fid, v in effective(audit_dir).items() if fid in known}
    qa = session_qa(config, log_path)
    provenance = _provenance(audit_dir, log_path, config_path, dataset_kind, flags)
    _write(resolved_out, provenance, flags, verdicts, qa)
    print(f"station-watch audit score: wrote {resolved_out}")
    return 0


__all__ = ["score_audit", "VERDICTS_FILE"]
