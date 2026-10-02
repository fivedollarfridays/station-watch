"""Orchestrate an evaluation: load the set, run every clip, write the files.

``run_evaluation`` is the one entry the CLI calls. It enforces the honesty rules
first (no labeled set, or a manifest naming a missing clip, writes nothing and
exits non-zero with a distinct code), then drives each clip through the real
pipeline, scores it against ground truth, computes the same numbers for the naive
baseline, and writes the measurement files -- ``detect.json``, ``baseline.json``
and the measured ``step_times.json`` -- each stamped with provenance. ``--synthetic``
takes the same path over a set generated at run time and tagged
``dataset_kind: synthetic``.

``--split`` (``calibration`` default, or ``held_out``) scores only that split's
clips. A ``held_out`` run first runs the calibration check (see
:mod:`station_watch.evaluate.calibration`): if a held-out clip, session or date is
traceable into the calibration provenance the config names, it writes nothing and
exits :data:`EXIT_CALIBRATION_LEAK`.
"""

from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from station_watch.evaluate.baseline import baseline_outcome
from station_watch.evaluate.calibration import CalibrationLeakError, held_out_calibration
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
EXIT_CALIBRATION_LEAK = 5


@dataclass(frozen=True)
class _Prov:
    """The provenance inputs computed from one run's scored clips."""

    dataset: str
    dataset_kind: str
    manifest_sha: str
    config_sha: str
    sessions: list
    split: str
    clip_sha256s: list
    recorded_on: dict
    calibration: object | None


def _sessions_of(clips) -> list:
    """Distinct session ids among ``clips``, in first-seen order."""
    seen: list = []
    for clip in clips:
        if clip.session not in seen:
            seen.append(clip.session)
    return seen


def _scored(manifest, split: str) -> list:
    """The clips of ``split`` in ``manifest``; refuse a split with no clips to score."""
    clips = [clip for clip in manifest.clips if clip.split == split]
    if not clips:
        raise ManifestError(f"manifest lists no clips for split {split!r}")
    return clips


def _run_clips(config, clips, work_dir: Path, speed: float, backends):
    """Run every clip; return (detector outcomes, baseline outcomes, all steps)."""
    outcomes, baselines, steps = [], [], []
    for index, clip in enumerate(clips):
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


def _write_outputs(out_dir: Path, *, meta: _Prov, bundle) -> None:
    """Write detect.json, baseline.json and step_times.json, each with provenance."""
    outcomes, baselines, steps = bundle
    out_dir.mkdir(parents=True, exist_ok=True)

    def prov(detector: str) -> dict:
        return build_provenance(
            dataset=meta.dataset,
            dataset_kind=meta.dataset_kind,
            manifest_sha256=meta.manifest_sha,
            config_sha256=meta.config_sha,
            detector=detector,
            clips=len(outcomes),
            sessions=meta.sessions,
            split=meta.split,
            clip_sha256s=meta.clip_sha256s,
            recorded_on=meta.recorded_on,
            calibration=meta.calibration,
        )

    write_measurement(out_dir / "detect.json", prov(DETECTOR), assemble_metrics(outcomes))
    write_measurement(out_dir / "baseline.json", prov(BASELINE_DETECTOR), assemble_metrics(baselines))
    write_step_times(out_dir / "step_times.json", step_stats(steps), prov(DETECTOR))


def _evaluate(config, scored, out_dir: Path, *, dataset, dataset_kind, manifest_sha,
              config_sha, speed, backends, split, calibration) -> None:
    with tempfile.TemporaryDirectory() as work:
        bundle = _run_clips(config, scored, Path(work), speed, backends)
    meta = _Prov(
        dataset=dataset,
        dataset_kind=dataset_kind,
        manifest_sha=manifest_sha,
        config_sha=config_sha,
        sessions=_sessions_of(scored),
        split=split,
        clip_sha256s=[clip.clip_sha256 for clip in scored],
        recorded_on={clip.session: clip.recorded_on for clip in scored},
        calibration=calibration,
    )
    _write_outputs(out_dir, meta=meta, bundle=bundle)


def _run_real(config_path, manifest_path, clips_dir, out_dir, speed, split) -> int:
    from station_watch.runner.startup import build_keepout_backend

    try:
        manifest = load_manifest(manifest_path, clips_dir)
    except NoLabeledSetError as exc:
        print(str(exc))
        return EXIT_NO_LABELED_SET
    except ManifestError as exc:
        print(f"station-watch: {exc}", file=sys.stderr)
        return EXIT_MISSING_CLIP
    calibration = None
    if split == "held_out":
        try:
            calibration = held_out_calibration(config_path, manifest)
        except CalibrationLeakError as exc:
            print(f"station-watch: {exc}", file=sys.stderr)
            return EXIT_CALIBRATION_LEAK
    try:
        scored = _scored(manifest, split)
    except ManifestError as exc:
        print(f"station-watch: {exc}", file=sys.stderr)
        return EXIT_MISSING_CLIP
    config = load_config(config_path)
    # One backend per clip, as the synthetic path already does: a shared,
    # possibly-stateful person detector must not carry state between clips.
    backends = [build_keepout_backend(config) for _ in scored]
    _evaluate(
        config,
        scored,
        Path(out_dir),
        dataset=manifest.dataset,
        dataset_kind="real",
        manifest_sha=manifest.sha256,
        config_sha=sha256_file(config_path),
        speed=speed,
        backends=backends,
        split=split,
        calibration=calibration,
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
            synthetic.manifest.clips,
            out,
            dataset=synthetic.manifest.dataset,
            dataset_kind="synthetic",
            manifest_sha=sha256_file(synthetic.manifest_path),
            config_sha=sha256_file(synthetic.config_path),
            speed=speed,
            backends=synthetic.backends,
            split="calibration",
            calibration=None,
        )
    return 0


def run_evaluation(
    *, config_path, manifest_path, out_dir, synthetic, clips_dir, speed=None, split="calibration"
) -> int:
    """Run an evaluation; return the process exit code (0 on success)."""
    if synthetic:
        return _run_synthetic(out_dir, SPEED if speed is None else speed)
    real_speed = 1.0 if speed is None else speed
    return _run_real(config_path, manifest_path, clips_dir, out_dir, real_speed, split)


__all__ = [
    "run_evaluation",
    "EXIT_NO_LABELED_SET",
    "EXIT_MISSING_CLIP",
    "EXIT_CALIBRATION_LEAK",
]
