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
    soak.add_argument(
        "--out", required=True, help="measurement file to write (table written beside it)"
    )
    soak.add_argument(
        "--dataset-kind",
        required=True,
        choices=["real", "synthetic"],
        help="which measurement tree --out must land in (real -> measurements/v1|v2, "
        "synthetic -> measurements/synthetic)",
    )
    soak.add_argument(
        "--force-out", action="store_true", help="override --out confinement (one warning line)"
    )
    soak.add_argument("--source", required=True, help="camera device index or a recorded file path")
    duration = soak.add_mutually_exclusive_group(required=True)
    duration.add_argument("--hours", type=float, help="soak duration in hours")
    duration.add_argument("--minutes", type=float, help="soak duration in minutes")
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


def _runner_argv(args) -> list[str]:
    """The default ``run`` child argv; HF3.11 swaps this seam for its synthetic loop."""
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
    ]


def handle(args) -> int:
    """Run the soak described by parsed ``soak`` ``args``; return an exit code."""
    from station_watch.runner.startup import StartupError
    from station_watch.soak.supervisor import run_soak

    try:
        return run_soak(args, runner_argv=_runner_argv(args))
    except (StartupError, KeyError, ValueError, OSError) as exc:
        message = exc.args[0] if exc.args else str(exc)
        print(f"station-watch: {message}", file=sys.stderr)
        return 2


__all__ = ["add_parser", "handle"]
