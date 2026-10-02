"""``station-watch qa`` as a command: load the config, score, print, write the file.

The scoring itself is :func:`station_watch.qa.session_qa`; this module is the
command around it: it loads and validates the config (K9, naming the missing
``qa.*`` key), confines ``--out`` to its dataset kind's measurement tree before
scoring, prints the result, and writes it through the one measurement writer.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import yaml

from station_watch.config import validate_required_keys
from station_watch.evaluate.provenance import (
    DETECTOR,
    build_provenance,
    confine_out,
    write_measurement,
)
from station_watch.qa import SessionQA, session_qa, validate_qa_config


def _load_config(path) -> dict:
    data = yaml.safe_load(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError(f"station config must be a YAML mapping, got {type(data).__name__}")
    validate_required_keys(data)
    validate_qa_config(data)
    return data


def _print_result(result: SessionQA) -> None:
    print(f"station-watch qa: {result.status.upper()}")
    print(f"  reason: {result.reason}")
    print(
        f"  unobservable_fraction: {result.unobservable_fraction:.3f} "
        f"(max {result.max_unknown_fraction})"
    )
    print(f"  observable_s: {result.observable_s:.3f}  session_s: {result.session_s:.3f}")
    print(f"  verdicts: {result.verdicts}")
    for target, frac in sorted(result.target_unknown_fraction.items()):
        print(f"  target {target}: {frac:.3f}")


def _write_measurement(
    out: Path, result: SessionQA, config: dict, config_path, dataset_kind
) -> None:
    provenance = build_provenance(
        dataset=str(config.get("station_id", "station")),
        dataset_kind=dataset_kind,
        manifest_sha256="none",
        config_sha256=hashlib.sha256(Path(config_path).read_bytes()).hexdigest(),
        detector=DETECTOR,
        clips=0,
        sessions=[],
    )
    metrics = {
        "status": result.status,
        "reason": result.reason,
        "session_s": result.session_s,
        "observable_s": result.observable_s,
        "unobservable_fraction": result.unobservable_fraction,
        "target_unknown_fraction": result.target_unknown_fraction,
        "verdicts": result.verdicts,
        "max_unknown_fraction": result.max_unknown_fraction,
        "max_target_unknown_fraction": result.max_target_unknown_fraction,
    }
    write_measurement(out, provenance, metrics)


def run_qa(*, config_path, log_path, out, dataset_kind, force_out) -> int:
    """Drive ``station-watch qa``: 0 pass, 1 fail, 2 startup error."""
    try:
        config = _load_config(config_path)
    except (OSError, KeyError, ValueError) as exc:
        print(f"station-watch: {exc}", file=sys.stderr)
        return 2
    resolved_out = None
    if out is not None:
        if dataset_kind is None:
            print("station-watch: qa --out requires --dataset-kind real|synthetic", file=sys.stderr)
            return 2
        try:
            resolved_out = confine_out(Path(out), dataset_kind, force_out=force_out)
        except ValueError as exc:
            print(f"station-watch: {exc}", file=sys.stderr)
            return 2
    result = session_qa(config, log_path)
    _print_result(result)
    if resolved_out is not None:
        _write_measurement(resolved_out, result, config, config_path, dataset_kind)
    return 0 if result.status == "pass" else 1


__all__ = ["run_qa"]
