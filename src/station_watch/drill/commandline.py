"""Argument wiring for the ``station-watch drill`` subcommand.

Kept out of :mod:`station_watch.cli` (like ``evaluate`` and ``board``) so the CLI
module stays small and building the parser never pulls in OpenCV or the renderer: the
heavy :mod:`station_watch.drill.pipeline` is imported lazily inside :func:`handle`.

A drill needs exactly one fault source -- a synthetic ``--schedule`` or ``--live`` on a
real camera. The missing piece is named on a non-zero exit (K9): a synthetic run with no
schedule, or a live run with no source, never limps along.
"""

from __future__ import annotations

import shlex
import sys

DEFAULT_FPS = 20.0


def add_parser(sub) -> None:
    """Add the ``drill`` subparser and its arguments to ``sub``."""
    drill = sub.add_parser(
        "drill",
        help="DRILL: inject camera faults and measure time-to-alarm",
        description="Run the real `run` pipeline (Capture, Log, Judge, Alarm, cycle rows) against "
        "injected camera faults and write a time-to-alarm measurement file plus a markdown table. "
        "This is a DRILL: synthetic (--schedule) injects faults into rendered frames; live "
        "(--live) runs a real camera while the operator types `start`/`clear <fault>` on stdin.",
    )
    drill.add_argument("--config", required=True, help="path to the station config YAML")
    drill.add_argument("--log", required=True, help="path to the append-only Log database")
    drill.add_argument(
        "--out", required=True, help="measurement file to write (table written beside it)"
    )
    drill.add_argument("--schedule", help="synthetic fault schedule YAML (synthetic mode)")
    drill.add_argument(
        "--live", action="store_true", help="live mode: a real camera, faults injected physically"
    )
    drill.add_argument("--source", help="camera device index (required with --live)")
    drill.add_argument(
        "--fps", type=float, default=DEFAULT_FPS, help="synthetic source frame rate (default 20)"
    )
    drill.add_argument(
        "--max-cycles",
        type=int,
        help="stop a live run after this many cycles (bounds a camera run)",
    )


def _command(args) -> str:
    """The exact command that reproduces this drill, recorded in the table header."""
    parts = [
        "station-watch",
        "drill",
        "--config",
        args.config,
        "--log",
        args.log,
        "--out",
        args.out,
    ]
    if args.live:
        parts += ["--live", "--source", str(args.source)]
    else:
        parts += ["--schedule", args.schedule]
    return shlex.join(parts)  # quote paths with spaces/$ so the string re-parses exactly


def handle(args) -> int:
    """Run the drill described by parsed ``drill`` ``args``; return an exit code."""
    from station_watch.drill.pipeline import run_live, run_synthetic
    from station_watch.faults import FaultScheduleError
    from station_watch.runner.startup import StartupError

    # Name the missing piece before anything is opened (K9): a synthetic run needs a
    # schedule, a live run needs a source.
    if args.live and args.source is None:
        print("station-watch: drill --live requires --source <device index>", file=sys.stderr)
        return 2
    if not args.live and args.schedule is None:
        print("station-watch: drill requires --schedule <faults.yaml> (or --live)", file=sys.stderr)
        return 2

    command = _command(args)
    try:
        if args.live:
            return run_live(
                config_path=args.config,
                log_path=args.log,
                out_path=args.out,
                source_spec=str(args.source),
                command=command,
                max_cycles=args.max_cycles,
            )
        return run_synthetic(
            config_path=args.config,
            log_path=args.log,
            out_path=args.out,
            schedule_path=args.schedule,
            command=command,
            fps=args.fps,
        )
    except (StartupError, FaultScheduleError, OSError) as exc:
        print(f"station-watch: {exc}", file=sys.stderr)
        return 1


__all__ = ["add_parser", "handle"]
