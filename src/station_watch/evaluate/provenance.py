"""Provenance: the stamp every measurement file carries so numbers are traceable.

Each measurement file is ``{"provenance": {...}, "metrics": {...}}``. The
provenance block records *what was measured and from what*: the dataset name and
kind (``real`` or ``synthetic``), the manifest and config SHA-256, the git commit
the harness ran at, the detector method and version, the clip count, the session
ids, and the UTC date. A number with no provenance is a number you cannot trust;
the synthetic kind in particular keeps a proving run from ever being read as real
performance.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

DETECTOR = "station_watch.detect:real-pipeline:v1"
BASELINE_DETECTOR = "frame_diff_baseline:v1"


def write_measurement(path: str | Path, provenance: dict, metrics: dict) -> None:
    """Write one measurement file in the one format: ``{provenance, metrics}``.

    The single writer every measurement file goes through (the evaluate harness and
    the fault drill both call it), so there is never a second on-disk shape for the
    HF2.9 claims test to chase -- metric keys stay addressable as dotted paths under
    ``metrics``.
    """
    payload = {"provenance": provenance, "metrics": metrics}
    Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def git_commit() -> str:
    """The current git commit, or ``"unknown"`` when not in a working tree."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def build_provenance(
    *,
    dataset: str,
    dataset_kind: str,
    manifest_sha256: str,
    config_sha256: str,
    detector: str,
    clips: int,
    sessions: list[str],
) -> dict:
    """Assemble the provenance block for a measurement file (one detector's numbers)."""
    return {
        "dataset": dataset,
        "dataset_kind": dataset_kind,
        "manifest_sha256": manifest_sha256,
        "config_sha256": config_sha256,
        "git_commit": git_commit(),
        "detector": detector,
        "clips": clips,
        "sessions": list(sessions),
        "date_utc": datetime.now(UTC).date().isoformat(),
    }


__all__ = [
    "DETECTOR",
    "BASELINE_DETECTOR",
    "git_commit",
    "build_provenance",
    "write_measurement",
]
