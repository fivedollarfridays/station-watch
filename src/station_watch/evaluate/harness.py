"""Orchestrate an evaluation: load the set, run every clip, write the files.

``run_evaluation`` is the one entry the CLI calls. It enforces the honesty rules
first (no labeled set, or a manifest naming a missing clip, writes nothing and
exits non-zero with a distinct code), then drives each clip through the real
pipeline, scores it against ground truth, computes the same numbers for the naive
baseline, and writes the measurement files -- ``detect.json``, ``baseline.json``
and the measured ``step_times.json`` -- each stamped with provenance. ``--synthetic``
takes the same path over a set generated at run time and tagged
``dataset_kind: synthetic``.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from station_watch.evaluate.baseline import baseline_outcome
from station_watch.evaluate.clip_run import run_clip
from station_watch.evaluate.extract import extract_outcome
from station_watch.evaluate.manifest import (
    ManifestError,
    NoLabeledSetError,
    load_manifest,
    sha256_file,
)
from station_watch.evaluate.metrics import assemble_metrics
from station_watch.evaluate.provenance import (
    BASELINE_DETECTOR,
    DETECTOR,
    build_provenance,
    write_measurement,
)
from station_watch.evaluate.synthetic import SPEED, build_synthetic_set
from station_watch.runner.startup import load_config
from station_watch.steps import step_durations, step_stats, write_step_times

EXIT_NO_LABELED_SET = 3
EXIT_MISSING_CLIP = 4


def _write_json(path: Path, provenance: dict, metrics: dict) -> None:
    write_measurement(path, provenance, metrics)


def _run_clips(config, manifest, work_dir: Path, speed: float, backends):
    """Run every clip; return (detector outcomes, baseline outcomes, all steps)."""
    outcomes, baselines, steps = [], [], []
    for index, clip in enumerate(manifest.clips):
        run = run_clip(
            config,
            clip.clip_path,
            work_dir / f"clip-{index}",
            session=clip.session,
            rel_path=clip.rel_path,
            speed=speed,
            keepout_backend=backends[index],
        )
        outcomes.append(extract_outcome(clip, run))
        baselines.append(baseline_outcome(clip, config, clip.clip_path))
        steps.extend(step_durations(run.observations))
    return outcomes, baselines, steps


def _write_outputs(out_dir, *, dataset, dataset_kind, manifest_sha, config_sha, sessions, bundle):
    """Write detect.json, baseline.json and step_times.json, each with provenance."""
    outcomes, baselines, steps = bundle
    out_dir.mkdir(parents=True, exist_ok=True)

    def prov(detector: str) -> dict:
        return build_provenance(
            dataset=dataset,
            dataset_kind=dataset_kind,
            manifest_sha256=manifest_sha,
            config_sha256=config_sha,
            detector=detector,
            clips=len(outcomes),
            sessions=sessions,
        )

    _write_json(out_dir / "detect.json", prov(DETECTOR), assemble_metrics(outcomes))
    _write_json(out_dir / "baseline.json", prov(BASELINE_DETECTOR), assemble_metrics(baselines))
    write_step_times(out_dir / "step_times.json", step_stats(steps), prov(DETECTOR))


def _evaluate(
    config, manifest, out_dir, *, dataset_kind, manifest_sha, config_sha, speed, backends
):
    with tempfile.TemporaryDirectory() as work:
        bundle = _run_clips(config, manifest, Path(work), speed, backends)
    _write_outputs(
        out_dir,
        dataset=manifest.dataset,
        dataset_kind=dataset_kind,
        manifest_sha=manifest_sha,
        config_sha=config_sha,
        sessions=manifest.sessions,
        bundle=bundle,
    )


def _run_real(config_path, manifest_path, clips_dir, out_dir, speed) -> int:
    from station_watch.runner.startup import build_keepout_backend

    try:
        manifest = load_manifest(manifest_path, clips_dir)
    except NoLabeledSetError as exc:
        print(str(exc))
        return EXIT_NO_LABELED_SET
    except ManifestError as exc:
        print(f"station-watch: {exc}", file=sys.stderr)
        return EXIT_MISSING_CLIP
    config = load_config(config_path)
    backend = build_keepout_backend(config)
    _evaluate(
        config,
        manifest,
        Path(out_dir),
        dataset_kind="real",
        manifest_sha=manifest.sha256,
        config_sha=sha256_file(config_path),
        speed=speed,
        backends=[backend] * len(manifest.clips),
    )
    return 0


def _run_synthetic(out_dir, speed) -> int:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as work:
        synthetic = build_synthetic_set(Path(work))
        # Commit the generated config + manifest beside the numbers, so the proving
        # set is self-contained and reproducible (and the hashes below match them).
        (out / "config.yaml").write_text(synthetic.config_path.read_text())
        (out / "manifest.yaml").write_text(synthetic.manifest_path.read_text())
        _evaluate(
            synthetic.config,
            synthetic.manifest,
            out,
            dataset_kind="synthetic",
            manifest_sha=sha256_file(synthetic.manifest_path),
            config_sha=sha256_file(synthetic.config_path),
            speed=speed,
            backends=synthetic.backends,
        )
    return 0


def run_evaluation(*, config_path, manifest_path, out_dir, synthetic, clips_dir, speed=None) -> int:
    """Run an evaluation; return the process exit code (0 on success)."""
    if synthetic:
        return _run_synthetic(out_dir, SPEED if speed is None else speed)
    real_speed = 1.0 if speed is None else speed
    return _run_real(config_path, manifest_path, clips_dir, out_dir, real_speed)


__all__ = ["run_evaluation", "EXIT_NO_LABELED_SET", "EXIT_MISSING_CLIP"]
