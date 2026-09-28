"""The ``station-watch`` console script: parse args and drive the runner.

``station-watch run`` wires Capture, Log, Judge and Alarm into one process (see
:mod:`station_watch.runner.pipeline`). Startup fails loud and non-zero when a
precondition is missing (K9); the message names the piece.
"""

from __future__ import annotations

import argparse
import sys

from station_watch.run import new_run_id
from station_watch.runner.pipeline import Runner
from station_watch.runner.startup import StartupError, build_context


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="station-watch", description="Camera-health watch for one assembly station."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    _add_run_parser(sub)
    return parser


def _add_run_parser(sub) -> None:
    run = sub.add_parser(
        "run",
        help="wire Capture, Log, Judge and Alarm into one process",
        description="Wire Capture -> Log -> Judge -> Alarm and record a cycle each interval.",
    )
    run.add_argument("--config", required=True, help="path to the station config YAML")
    run.add_argument("--source", required=True, help="camera device index or a recorded file path")
    run.add_argument("--log", required=True, help="path to the append-only Log database")
    run.add_argument(
        "--observations",
        help="FIXTURE INPUT: a JSONL of observations, rebased onto this run's start and "
        "fed to the Judge like any other input as each ts arrives (Detect replaces this in HF2)",
    )
    run.add_argument(
        "--stop-stage",
        choices=["alarm"],
        help="TEST AND DRILL USE ONLY: keep Capture and the cycle loop running while the named "
        "stage stops (alarm: the Alarm stops evaluating) -- the condition the Watchdog catches",
    )
    run.add_argument(
        "--alarm-record",
        help="output file for the 'record' alarm sink (one JSON line per alarm and recovery)",
    )
    run.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="file playback speed factor (>1 faster; a replay/test aid, ignored for live devices)",
    )
    run.add_argument(
        "--max-cycles",
        type=int,
        help="stop after this many cycles (bounds a live-device or drill run)",
    )


def _run(args) -> int:
    try:
        context = build_context(
            config_path=args.config,
            source_spec=args.source,
            log_path=args.log,
            alarm_record=args.alarm_record,
        )
    except StartupError as exc:
        print(f"station-watch: {exc}", file=sys.stderr)
        return 1
    runner = Runner(
        context,
        run_id=new_run_id(),
        stop_stage=args.stop_stage,
        observations_path=args.observations,
        max_cycles=args.max_cycles,
        speed=args.speed,
    )
    try:
        runner.run()
    finally:
        context.log.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return _run(args)
    parser.error(f"unknown command: {args.command}")
    return 2


__all__ = ["main", "build_parser"]
