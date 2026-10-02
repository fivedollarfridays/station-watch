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
import sys
from datetime import UTC, datetime
from pathlib import Path

DETECTOR = "station_watch.detect:real-pipeline:v1"
BASELINE_DETECTOR = "frame_diff_baseline:v1"

# Each dataset kind owns its measurement subtree: a synthetic (proving) number can
# never be written where a real number lives, and vice versa.
_CONFINE_DIRS = {
    "synthetic": ("measurements/synthetic",),
    "real": ("measurements/v1", "measurements/v2"),
}


def confine_out(out: Path, dataset_kind: str, *, force_out: bool = False) -> Path:
    """Resolve ``out`` only if it lands in ``dataset_kind``'s own measurement tree.

    The returned path is fully resolved (``..`` normalized, symlinks followed), so a
    ``..`` segment or a symlink that escapes the allowed tree is caught here rather
    than silently writing elsewhere. A disallowed path raises ``ValueError`` naming
    the rule; ``force_out`` overrides it with one warning line on stderr.
    """
    bases = _CONFINE_DIRS.get(dataset_kind)
    if bases is None:
        raise ValueError(
            f"unknown dataset_kind {dataset_kind!r}; expected one of {sorted(_CONFINE_DIRS)}"
        )
    resolved = Path(out).resolve()
    allowed = [Path(base).resolve() for base in bases]
    if any(resolved == base or base in resolved.parents for base in allowed):
        return resolved
    rule = (
        f"a {dataset_kind} measurement must be written inside "
        f"{' or '.join(base + '/' for base in bases)}"
    )
    if force_out:
        sys.stderr.write(f"station-watch: WARNING: --force-out writes {resolved} outside {rule}\n")
        return resolved
    raise ValueError(f"refusing to write {resolved}: {rule}")


def write_measurement(path: str | Path, provenance: dict, metrics: dict) -> None:
    """Write one measurement file in the one format: ``{provenance, metrics}``.

    The single writer every measurement file goes through (the evaluate harness and
    the fault drill both call it), so there is never a second on-disk shape for the
    claims test to chase -- metric keys stay addressable as dotted paths under
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
    split: str | None = None,
    clip_sha256s: list[str] | None = None,
    recorded_on: dict | None = None,
    calibration: object | None = None,
) -> dict:
    """Assemble the provenance block for a measurement file (one detector's numbers).

    The HF3.8 split fields (``split``, ``clip_sha256s``, ``recorded_on`` and, for a
    held-out run, ``calibration``) are added only when supplied, so callers that do
    not track a split -- the fault drill and the physics scripts -- write exactly the
    block they always have.
    """
    provenance = {
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
    if split is not None:
        provenance["split"] = split
    if clip_sha256s is not None:
        provenance["clip_sha256s"] = sorted(clip_sha256s)
    if recorded_on is not None:
        provenance["recorded_on"] = dict(recorded_on)
    if calibration is not None:
        provenance["calibration"] = calibration
    return provenance


__all__ = [
    "DETECTOR",
    "BASELINE_DETECTOR",
    "git_commit",
    "build_provenance",
    "write_measurement",
    "confine_out",
]
