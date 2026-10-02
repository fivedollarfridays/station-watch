"""Argument wiring for the ``station-watch evaluate`` subcommand.

Kept out of :mod:`station_watch.cli` so the CLI module stays small, and kept light
(argparse only) so building the parser never pulls in OpenCV or the harness: the
heavy :func:`run_evaluation` is imported lazily inside :func:`handle`.
"""

from __future__ import annotations

import sys


def add_parser(sub) -> None:
    """Add the ``evaluate`` subparser and its arguments to ``sub``."""
    evaluate = sub.add_parser(
        "evaluate",
        help="score a labeled clip set through the real pipeline and write measurement files",
        description="Drive every clip a manifest lists through the real Capture -> Detect -> "
        "Judge -> Alarm path and write committed JSON measurement files (precision/recall, "
        "confusion matrix, latency, time-to-alarm, step_times.json) plus the naive "
        "frame-difference baseline, each stamped with provenance. With no --manifest it refuses "
        "to invent numbers; --synthetic proves the harness on a run-time labeled set.",
    )
    evaluate.add_argument("--config", help="station config YAML (required unless --synthetic)")
    evaluate.add_argument(
        "--manifest", help="labeled-set manifest YAML; absent/missing -> 'no labeled set present'"
    )
    evaluate.add_argument(
        "--clips-dir",
        default="data/local/clips",
        help="directory the manifest's clip paths are relative to (clips stay local/gitignored)",
    )
    evaluate.add_argument(
        "--out",
        help="output dir (default: measurements/synthetic for --synthetic, else measurements)",
    )
    evaluate.add_argument(
        "--synthetic",
        action="store_true",
        help="generate a labeled set at run time and tag files dataset_kind: synthetic",
    )
    evaluate.add_argument(
        "--speed", type=float, help="playback speed (default: accelerated for --synthetic)"
    )
    evaluate.add_argument(
        "--split",
        choices=["calibration", "held_out"],
        default="calibration",
        help="which split's clips to score; held_out runs the calibration-leak check first",
    )
    evaluate.add_argument(
        "--force-out", action="store_true", help="override --out confinement (one warning line)"
    )


def handle(args) -> int:
    """Run the evaluation described by parsed ``evaluate`` ``args``; return an exit code."""
    from pathlib import Path

    from station_watch.evaluate.harness import run_evaluation
    from station_watch.evaluate.provenance import confine_out

    if not args.synthetic and args.config is None:
        print("station-watch: evaluate requires --config (or --synthetic)", file=sys.stderr)
        return 2
    if args.synthetic:
        out, dataset_kind = args.out or "measurements/synthetic", "synthetic"
    elif args.out is None:
        # A real run that overwrites the previous one in measurements/ is how PR #5's
        # numbers collided; make the operator name a destination (K13 honesty).
        print(
            "station-watch: evaluate requires --out (a real run must not overwrite another's "
            "numbers; write to measurements/v1 or measurements/v2)",
            file=sys.stderr,
        )
        return 2
    else:
        out, dataset_kind = args.out, "real"
    # Every --out (real and synthetic) obeys the same Output confinement rule.
    try:
        resolved_out = confine_out(Path(out), dataset_kind, force_out=args.force_out)
    except ValueError as exc:
        print(f"station-watch: {exc}", file=sys.stderr)
        return 2
    return run_evaluation(
        config_path=args.config,
        manifest_path=args.manifest,
        out_dir=str(resolved_out),
        synthetic=args.synthetic,
        clips_dir=args.clips_dir,
        speed=args.speed,
        split=args.split,
    )


__all__ = ["add_parser", "handle"]
