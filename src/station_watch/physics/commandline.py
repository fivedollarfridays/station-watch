"""Argument wiring for the ``station-watch measure`` subcommand.

Kept out of :mod:`station_watch.cli` (like ``evaluate``, ``board`` and ``drill``) so the
CLI module stays small and building the parser never pulls in OpenCV or the physics
runner: the heavy :mod:`station_watch.physics.runner` is imported lazily in :func:`handle`.
"""

from __future__ import annotations

import sys


def add_parser(sub) -> None:
    """Add the ``measure`` subparser and its arguments to ``sub``."""
    measure = sub.add_parser(
        "measure",
        help="run a physics measurement script over the labeled clip set",
        description="Run a named physics script (e.g. fps_sweep) over the HF2.8 manifest, "
        "selecting its clips by their optional per-clip tags. With no clips, an absent "
        "manifest, or no clips tagged for the measurement, it writes a 'no_input' file "
        "(no metrics key) and exits zero -- a physics table that does not exist yet says "
        "so in the repo. Real results write the HF2.8 format under measurements/v1/.",
    )
    measure.add_argument("name", help="physics script name (e.g. fps_sweep)")
    measure.add_argument(
        "--clips", help="HF2.8 manifest YAML; absent/missing/untagged -> 'no input present'"
    )
    measure.add_argument(
        "--clips-dir",
        default="data/local/clips",
        help="directory the manifest's clip paths are relative to (clips stay local/gitignored)",
    )
    measure.add_argument(
        "--out",
        help="output file (default: measurements/physics/<name>.json for no_input, "
        "measurements/v1/<name>.json for real results)",
    )
    measure.add_argument(
        "--config", help="station config YAML (required when the manifest has clips to measure)"
    )
    measure.add_argument(
        "--synthetic",
        action="store_true",
        help="TEST ONLY: generate a labeled set at run time, write only into --out, and stamp "
        "every output dataset_kind: synthetic",
    )


def _command(args) -> str:
    """The command that reproduces this measurement, recorded in the output file."""
    parts = ["station-watch", "measure", args.name]
    if args.synthetic:
        parts.append("--synthetic")
    if args.clips:
        parts += ["--clips", args.clips]
    return " ".join(parts)


def handle(args) -> int:
    """Run the measurement described by parsed ``measure`` ``args``; return an exit code."""
    from station_watch.evaluate.manifest import ManifestError, NoLabeledSetError
    from station_watch.physics.runner import UnknownScriptError, run_measure

    try:
        return run_measure(
            name=args.name,
            clips_path=args.clips,
            clips_dir=args.clips_dir,
            out=args.out,
            config_path=args.config,
            synthetic=args.synthetic,
            command=_command(args),
        )
    except UnknownScriptError as exc:
        print(f"station-watch: {exc}", file=sys.stderr)
        return 2
    except (ManifestError, NoLabeledSetError, OSError) as exc:
        print(f"station-watch: {exc}", file=sys.stderr)
        return 1


__all__ = ["add_parser", "handle"]
