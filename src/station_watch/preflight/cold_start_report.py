"""``preflight --cold-start`` as a command: confine ``--out``, measure, write the file.

``--out`` is resolved through :func:`~station_watch.evaluate.provenance.confine_out`
*before* any child starts, so a refused path costs nothing and touches no Log. The
measurement goes through the one writer, ``write_measurement``: the three timings
under ``metrics`` on success, or ``status: no_healthy_verdict`` and a reason with no
``metrics`` key. The provenance names the source by file name only (a device index
for a camera), never a full path, because these files are committed.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import yaml

from station_watch.evaluate.provenance import (
    DETECTOR,
    build_provenance,
    confine_out,
    write_measurement,
)
from station_watch.preflight.cold_start import ColdStart, measure_cold_start
from station_watch.runner.startup import parse_source


def _source_label(source: str) -> str:
    """A device index as ``device index N``; a file by its name only."""
    spec = parse_source(source)
    return f"device index {spec}" if isinstance(spec, int) else Path(spec).name


def cold_start_provenance(config_path: str, source: str, dataset_kind: str) -> dict:
    """The provenance block: station, kind, config hash, commit, and the source label."""
    raw = Path(config_path).read_bytes()
    config = yaml.safe_load(raw) or {}
    is_file = not isinstance(parse_source(source), int)
    provenance = build_provenance(
        dataset=f"cold_start:{config.get('station_id', 'station')}",
        dataset_kind=dataset_kind,
        manifest_sha256="none",
        config_sha256=hashlib.sha256(raw).hexdigest(),
        detector=DETECTOR,
        clips=1 if is_file else 0,
        sessions=[],
    )
    provenance["source"] = _source_label(source)
    return provenance


def _check_args(args) -> None:
    missing = [
        f"--{name.replace('_', '-')}"
        for name in ("config", "source", "log", "out", "dataset_kind")
        if getattr(args, name, None) is None
    ]
    if missing:
        raise ValueError(f"preflight --cold-start requires {', '.join(missing)}")


def _report(result: ColdStart, out: Path) -> None:
    if result.metrics is None:
        print(f"cold start: {result.status}: {result.reason}")
    else:
        for key, value in result.metrics.items():
            print(f"{key:<26}  {value:.2f}")
    print(f"wrote {out}")


def handle_cold_start(args) -> int:
    """Run ``--cold-start``: 0 with timings, 1 with no healthy verdict, 2 on bad args."""
    try:
        _check_args(args)
        out = confine_out(Path(args.out), args.dataset_kind, force_out=args.force_out)
        provenance = cold_start_provenance(args.config, args.source, args.dataset_kind)
    except (ValueError, OSError, yaml.YAMLError) as exc:
        print(f"station-watch preflight: {exc}", file=sys.stderr)
        return 2
    result = measure_cold_start(args.config, args.source, args.log, timeout_s=args.timeout_s)
    out.parent.mkdir(parents=True, exist_ok=True)
    if result.metrics is None:
        write_measurement(out, provenance, status=result.status, reason=result.reason)
    else:
        write_measurement(out, provenance, result.metrics)
    _report(result, out)
    return 0 if result.metrics is not None else 1


__all__ = ["handle_cold_start", "cold_start_provenance"]
