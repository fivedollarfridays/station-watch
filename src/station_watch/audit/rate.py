"""``audit rate``: flags per hour of normal operation, split true/false/unreviewed.

Reads ``flags.json`` (HF3.5) and ``verdicts.jsonl`` (HF3.6) under the audit dir and
the session's QA (HF3.3), and writes one measurement file through
:func:`~station_watch.evaluate.provenance.write_measurement` -- the same format family
``audit score`` writes, no second shape.

The denominator is the session's *observable* hours (``observable_s / 3600``): blind
time is not "normal operation" and never inflates the hours. Per detection flag kind
and overall it reports ``flags`` and, from the HF3.6 verdicts, ``true`` (correct),
``false`` (incorrect) and ``unreviewed`` counts, each also divided by the observable
hours. Unreviewed flags are never counted true or false. Blind episodes (a camera
fault, not a false alarm of the detector) are reported per hour in their own ``blind``
block, never folded into the detection rates.

Status rules (each with no ``metrics`` key): a session that fails QA writes
``status: qa_failed`` (with the QA reason); a session whose observable hours are below
``--min-hours`` writes ``status: insufficient_duration`` naming both numbers. ``--out``
is confined to the dataset kind's own measurement tree; the Log is only ever read.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import yaml

from station_watch.audit.flags import FLAGS_FILE, load_flags_json
from station_watch.audit.verdicts import effective, verdicts_path
from station_watch.board.reader import BoardLogError, LogReader
from station_watch.config import validate_required_keys
from station_watch.evaluate.provenance import (
    DETECTOR,
    build_provenance,
    confine_out,
    write_measurement,
)
from station_watch.qa import session_qa, validate_qa_config

_BLIND_PREFIX = "unobservable:"


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


def _partition(flags: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split flags into detection flags and blind (``unobservable:*``) episodes."""
    detection, blind = [], []
    for flag in flags:
        (blind if flag["kind"].startswith(_BLIND_PREFIX) else detection).append(flag)
    return detection, blind


def _count_kind(flags: list[dict], verdicts: dict) -> dict:
    """``kind -> {flags, true, false}`` over the detection flags (true=correct)."""
    by_kind: dict[str, dict] = {}
    for flag in flags:
        bucket = by_kind.setdefault(flag["kind"], {"flags": 0, "true": 0, "false": 0})
        bucket["flags"] += 1
        verdict = (verdicts.get(flag["flag_id"]) or {}).get("verdict")
        if verdict == "correct":
            bucket["true"] += 1
        elif verdict == "incorrect":
            bucket["false"] += 1
    return by_kind


def _per_hour(bucket: dict, hours: float) -> dict:
    """A count bucket plus its unreviewed count and every count divided by ``hours``."""
    unreviewed = bucket["flags"] - bucket["true"] - bucket["false"]
    return {
        "flags": bucket["flags"],
        "true": bucket["true"],
        "false": bucket["false"],
        "unreviewed": unreviewed,
        "flags_per_hour": bucket["flags"] / hours,
        "true_per_hour": bucket["true"] / hours,
        "false_per_hour": bucket["false"] / hours,
        "unreviewed_per_hour": unreviewed / hours,
    }


def _overall(by_kind: dict) -> dict:
    total = {"flags": 0, "true": 0, "false": 0}
    for bucket in by_kind.values():
        for key in total:
            total[key] += bucket[key]
    return total


def _blind_block(blind_flags: list[dict], hours: float) -> dict:
    """Blind episodes per hour, overall and per reason -- their own block, not a rate."""
    by_kind: dict[str, int] = {}
    for flag in blind_flags:
        by_kind[flag["kind"]] = by_kind.get(flag["kind"], 0) + 1
    return {
        "episodes": len(blind_flags),
        "per_hour": len(blind_flags) / hours,
        "by_kind": {
            kind: {"episodes": n, "per_hour": n / hours} for kind, n in sorted(by_kind.items())
        },
    }


def _metrics(flags, verdicts, qa, observed_hours, session_hours) -> dict:
    detection, blind = _partition(flags)
    by_kind = _count_kind(detection, verdicts)
    return {
        "observed_hours": observed_hours,
        "session_hours": session_hours,
        "per_kind": {kind: _per_hour(bucket, observed_hours) for kind, bucket in by_kind.items()},
        "overall": _per_hour(_overall(by_kind), observed_hours),
        "blind": _blind_block(blind, observed_hours),
        "qa": {
            "status": qa.status,
            "reason": qa.reason,
            "unobservable_fraction": qa.unobservable_fraction,
        },
    }


def _write(out, provenance, flags, verdicts, qa, observed_hours, session_hours, min_hours) -> None:
    if qa.status == "fail":
        write_measurement(out, provenance, status="qa_failed", reason=qa.reason)
    elif observed_hours < min_hours or observed_hours <= 0.0:
        reason = (
            f"observed_hours {observed_hours:.4f} is below --min-hours {min_hours}; "
            "too little normal operation to measure a rate"
        )
        write_measurement(out, provenance, status="insufficient_duration", reason=reason)
    else:
        metrics = _metrics(flags, verdicts, qa, observed_hours, session_hours)
        write_measurement(out, provenance, metrics)


def rate_audit(*, audit_dir, config_path, log_path, dataset_kind, out, min_hours, force_out) -> int:
    """Drive ``station-watch audit rate``; 0 on success, 2 on a startup/confinement error."""
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
    observed_hours = qa.observable_s / 3600.0
    session_hours = qa.session_s / 3600.0
    provenance = _provenance(audit_dir, log_path, config_path, dataset_kind, flags)
    _write(
        resolved_out, provenance, flags, verdicts, qa, observed_hours, session_hours, min_hours
    )
    print(f"station-watch audit rate: wrote {resolved_out}")
    return 0


__all__ = ["rate_audit"]
