"""The shared runner behind ``station-watch measure <name>`` -- every physics script.

The honesty contract (stricter than ``evaluate``'s, which writes nothing): when no
input is present -- ``--clips`` missing, the manifest absent, or it lists no clips
tagged for this measurement -- the runner prints ``no input present: <reason>``,
writes a ``no_input`` file with **no** ``metrics`` key (so the K13 claims test can
never cite it) and exits zero. It never substitutes synthetic or estimated numbers.

A physics table that does not exist yet therefore *says so in the repo*: the committed
``measurements/physics/<name>.json`` is that ``no_input`` stub. Real results write the
HF2.8 measurement format under ``measurements/v1/``; ``--synthetic`` (test only) writes
a scored run only into the given ``--out``, stamped ``dataset_kind: synthetic``.
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from station_watch.evaluate.manifest import load_manifest
from station_watch.evaluate.provenance import (
    DETECTOR,
    build_provenance,
    git_commit,
    write_measurement,
)
from station_watch.physics.exposure_blur import ExposureBlur
from station_watch.physics.flicker import Flicker
from station_watch.physics.fps_sweep import FpsSweep
from station_watch.physics.occlusion import Occlusion

SCRIPTS = {
    FpsSweep.name: FpsSweep(),
    Occlusion.name: Occlusion(),
    ExposureBlur.name: ExposureBlur(),
    Flicker.name: Flicker(),
}

PHYSICS_DIR = Path("measurements/physics")
V1_DIR = Path("measurements/v1")


class UnknownScriptError(RuntimeError):
    """The named measurement script does not exist."""


def _resolve_input(script, clips_path, clips_dir):
    """``(reason, selected, manifest)`` -- ``reason`` is set only when there is no input."""
    if clips_path is None:
        return "no --clips given", None, None
    if not Path(clips_path).exists():
        return f"manifest not present: {clips_path}", None, None
    manifest = load_manifest(clips_path, clips_dir)
    selected = [clip for clip in manifest.clips if script.selects(clip)]
    if not selected:
        return f"no clips tagged for {script.name}", None, None
    return None, selected, manifest


def _write_no_input(dest: Path, name: str, reason: str, command: str) -> None:
    """Write the ``no_input`` stub: a status and a reason, and never a ``metrics`` key."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": "no_input",
        "reason": reason,
        "command": command,
        "provenance": {
            "git_commit": git_commit(),
            "date_utc": datetime.now(UTC).date().isoformat(),
        },
    }
    dest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _measurement(script, selected, manifest, config, dataset_kind, config_sha):
    """Score ``selected`` and write one HF2.8-format measurement file's parts."""
    metrics = script.measure(selected, config, _real_backend_factory(config))
    provenance = build_provenance(
        dataset=manifest.dataset,
        dataset_kind=dataset_kind,
        manifest_sha256=manifest.sha256,
        config_sha256=config_sha,
        detector=DETECTOR,
        clips=len(selected),
        sessions=manifest.sessions,
    )
    return provenance, metrics


def _real_backend_factory(config):
    from station_watch.runner.startup import build_keepout_backend

    backend = build_keepout_backend(config)
    return lambda _clip: backend


def _run_real(script, selected, manifest, config_path, dest: Path) -> int:
    from station_watch.evaluate.manifest import sha256_file
    from station_watch.runner.startup import load_config

    config = load_config(config_path)
    provenance, metrics = _measurement(
        script, selected, manifest, config, "real", sha256_file(config_path)
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    write_measurement(dest, provenance, metrics)
    return 0


def _run_synthetic(script, dest: Path) -> int:
    with tempfile.TemporaryDirectory() as work:
        synthetic = script.build_synthetic(Path(work))
        selected = [clip for clip in synthetic.manifest.clips if script.selects(clip)]
        metrics = script.measure(selected, synthetic.config, synthetic.backend_factory)
        provenance = build_provenance(
            dataset=synthetic.manifest.dataset,
            dataset_kind="synthetic",
            manifest_sha256=synthetic.manifest.sha256,
            config_sha256=synthetic.config_sha,
            detector=DETECTOR,
            clips=len(selected),
            sessions=synthetic.manifest.sessions,
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    write_measurement(dest, provenance, metrics)
    return 0


def run_measure(*, name, clips_path, clips_dir, out, config_path, synthetic, command) -> int:
    """Run physics measurement ``name``; return the process exit code (0 on success)."""
    script = SCRIPTS.get(name)
    if script is None:
        raise UnknownScriptError(f"unknown measurement: {name!r} (have: {', '.join(SCRIPTS)})")
    if synthetic:
        if out is None:
            print("station-watch: measure --synthetic requires --out", file=sys.stderr)
            return 2
        return _run_synthetic(script, Path(out))
    reason, selected, manifest = _resolve_input(script, clips_path, clips_dir)
    if reason is not None:
        dest = Path(out) if out is not None else PHYSICS_DIR / f"{name}.json"
        _write_no_input(dest, name, reason, command)
        print(f"no input present: {reason}")
        return 0
    if config_path is None:
        print("station-watch: measure requires --config when clips are present", file=sys.stderr)
        return 2
    dest = Path(out) if out is not None else V1_DIR / f"{name}.json"
    return _run_real(script, selected, manifest, config_path, dest)


__all__ = ["run_measure", "SCRIPTS", "UnknownScriptError", "PHYSICS_DIR", "V1_DIR"]
