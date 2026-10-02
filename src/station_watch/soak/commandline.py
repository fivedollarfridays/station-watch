"""Argument wiring for the ``station-watch soak`` subcommand.

Kept out of :mod:`station_watch.cli` (like ``evaluate``, ``board`` and ``drill``) so
the CLI module stays small and building the parser never pulls in the supervisor or
its subprocess machinery: the heavy :mod:`station_watch.soak.supervisor` is imported
lazily inside :func:`handle`.

A soak needs exactly one duration -- ``--hours`` or ``--minutes`` -- and a confined
``--out`` for its ``--dataset-kind``. A missing ``soak.*`` or ``qa.*`` config key, or
an ``--out`` outside the allowed measurement tree, fails loud and non-zero (K9), never
limps along.
"""

from __future__ import annotations

import argparse
import sys

DEFAULT_SAMPLE_S = 30.0
DEFAULT_BOARD_PORT = 8765


def add_parser(sub) -> None:
    """Add the ``soak`` subparser and its arguments to ``sub``."""
    soak = sub.add_parser(
        "soak",
        help="SOAK: run the real pipeline for hours and judge it for leaks and stalls",
        description="Start the real `run`, `watchdog` and `board` children against one Log, sample "
        "memory/Log-size/Board-latency/verdict-cadence every --sample-s, and judge growth against "
        "the required soak: thresholds plus HF3.3 session QA. Exits 0 only on a clean soak.",
    )
    soak.add_argument("--config", required=True, help="path to the station config YAML")
    soak.add_argument("--log", required=True, help="path to the append-only Log database")
    # --out/--dataset-kind/--source/duration describe a soak the supervisor drives; the
    # hidden --child entry below runs the runner child alone and needs none of them, so
    # requiredness is enforced in `handle` per mode rather than by argparse.
    soak.add_argument("--out", help="measurement file to write (table written beside it)")
    soak.add_argument(
        "--dataset-kind",
        choices=["real", "synthetic"],
        help="which measurement tree --out must land in (real -> measurements/v1|v2, "
        "synthetic -> measurements/synthetic)",
    )
    soak.add_argument(
        "--force-out", action="store_true", help="override --out confinement (one warning line)"
    )
    soak.add_argument("--source", help="camera device index or a recorded file path")
    soak.add_argument(
        "--synthetic-loop",
        action="store_true",
        help="run against a looping synthetic normal-work source instead of --source "
        "(requires --dataset-kind synthetic; every fault it raises is a false flag)",
    )
    soak.add_argument("--hours", type=float, help="soak duration in hours")
    soak.add_argument("--minutes", type=float, help="soak duration in minutes")
    soak.add_argument(
        "--sample-s",
        type=float,
        default=DEFAULT_SAMPLE_S,
        help="seconds between samples (default 30)",
    )
    soak.add_argument(
        "--board-port",
        type=int,
        default=DEFAULT_BOARD_PORT,
        help="loopback port for the Board child",
    )
    # Hidden: the runner child the supervisor spawns as `station-watch soak --child`.
    # Importing the child module here keeps it reached by the orphan check.
    soak.add_argument("--child", action="store_true", help=argparse.SUPPRESS)


def _runner_argv(args) -> list[str]:
    """The ``run`` child argv for a recorded ``--source`` soak.

    ``--no-evidence``, like the ``--synthetic-loop`` child: both soak modes measure
    the same pipeline, and an hours-long soak does not fill the evidence dir.
    """
    return [
        sys.executable,
        "-m",
        "station_watch",
        "run",
        "--config",
        args.config,
        "--source",
        str(args.source),
        "--log",
        args.log,
        "--no-evidence",
    ]


def _child_argv(args) -> list[str]:
    """The runner-child argv for a ``--synthetic-loop`` soak: a real `soak --child`."""
    return [
        sys.executable,
        "-m",
        "station_watch",
        "soak",
        "--child",
        "--config",
        args.config,
        "--log",
        args.log,
    ]


def _resolve_runner_argv(args) -> list[str]:
    """Pick the runner-child argv for the soak, or fail loud naming the broken rule.

    Exactly one source is required: ``--synthetic-loop`` (a looping synthetic
    normal-work source) or ``--source`` (a camera/recording). ``--synthetic-loop`` is
    synthetic by construction, so it refuses any ``--dataset-kind`` but ``synthetic``.
    Raises :class:`ValueError` whose message names the rule.
    """
    if not args.out or not args.dataset_kind:
        raise ValueError("soak requires --out and --dataset-kind")
    if args.synthetic_loop and args.source is not None:
        raise ValueError("--source and --synthetic-loop are mutually exclusive; give one")
    if not args.synthetic_loop and args.source is None:
        raise ValueError("a soak needs a source: give --synthetic-loop or --source")
    if args.hours is None and args.minutes is None:
        raise ValueError("a soak needs a duration: give --hours or --minutes")
    if args.synthetic_loop:
        if args.dataset_kind != "synthetic":
            raise ValueError(
                "--synthetic-loop runs a synthetic source; it requires --dataset-kind "
                f"synthetic, not {args.dataset_kind}"
            )
        return _child_argv(args)
    return _runner_argv(args)


def handle(args) -> int:
    """Run the soak (or its runner child) described by parsed ``args``; return a code."""
    from station_watch.runner.startup import StartupError
    from station_watch.soak.child import run_child
    from station_watch.soak.supervisor import run_soak

    if args.child:
        return run_child(args)
    try:
        runner_argv = _resolve_runner_argv(args)
        return run_soak(args, runner_argv=runner_argv)
    except (StartupError, KeyError, ValueError, OSError) as exc:
        message = exc.args[0] if exc.args else str(exc)
        print(f"station-watch: {message}", file=sys.stderr)
        return 2


__all__ = ["add_parser", "handle"]
