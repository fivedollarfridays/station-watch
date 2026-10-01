"""The ``station-watch`` console script: parse args and drive the runner.

``station-watch run`` wires Capture, Log, Judge and Alarm into one process (see
:mod:`station_watch.runner.pipeline`). Startup fails loud and non-zero when a
precondition is missing (K9); the message names the piece.
"""

from __future__ import annotations

import argparse
import signal
import sys

from station_watch.run import new_run_id
from station_watch.runner.pipeline import Runner
from station_watch.runner.startup import StartupError, build_context
from station_watch.watchdog import build_watchdog


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="station-watch", description="Camera-health watch for one assembly station."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    _add_run_parser(sub)
    _add_watchdog_parser(sub)
    _add_fetch_model_parser(sub)
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
        "--stop-stage-after",
        type=int,
        default=0,
        metavar="N",
        help="TEST AND DRILL USE ONLY: run the full pipeline for N cycles before --stop-stage "
        "takes effect, so the stopped stage's rows exist and then go stale",
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


def _add_watchdog_parser(sub) -> None:
    watchdog = sub.add_parser(
        "watchdog",
        help="a second clock on its own rail that fires when the run goes silent",
        description="Judge the run's cycle and alarm rails on the Watchdog's own clock and "
        "sinks (K7, K8); a stale, absent, or unreadable Log is an alarm, not a pass.",
    )
    watchdog.add_argument("--config", required=True, help="path to the station config YAML")
    watchdog.add_argument("--log", required=True, help="path to the run's append-only Log")
    watchdog.add_argument(
        "--alarm-record",
        help="output file for the 'record' watchdog sink (one JSON line per alarm and recovery)",
    )
    watchdog.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="seconds between checks; a stale rail fires within its window plus one such tick",
    )
    watchdog.add_argument(
        "--max-checks",
        type=int,
        help="stop after this many checks (bounds a drill or test run)",
    )


def _add_fetch_model_parser(sub) -> None:
    fetch = sub.add_parser(
        "fetch-model",
        help="download the keep-out person model (Apache-2.0 YOLOX) and verify its hash",
        description="Download the YOLOX-Nano ONNX weights (Apache-2.0) from the official "
        "release into data/local/models/ and verify the SHA-256. This is the only network "
        "call in the package; `run` never makes it. Weights are never committed.",
    )
    fetch.add_argument(
        "--dest",
        help="where to write the weights (default: data/local/models/yolox_nano.onnx)",
    )


def _fetch_model(args) -> int:
    from station_watch.detect.yolox import (
        MODEL_LICENSE,
        MODEL_NAME,
        MODEL_SOURCE_URL,
        WeightsError,
        fetch_model,
    )

    print(f"station-watch: fetching {MODEL_NAME} ({MODEL_LICENSE}) from {MODEL_SOURCE_URL}")
    try:
        path = fetch_model(args.dest)
    except (WeightsError, OSError) as exc:
        print(f"station-watch: fetch-model failed: {exc}", file=sys.stderr)
        return 1
    print(f"station-watch: verified weights written to {path}")
    return 0


def _run(args) -> int:
    try:
        context = build_context(
            config_path=args.config,
            source_spec=args.source,
            log_path=args.log,
            alarm_record=args.alarm_record,
            observations_path=args.observations,
        )
    except StartupError as exc:
        print(f"station-watch: {exc}", file=sys.stderr)
        return 1
    runner = Runner(
        context,
        run_id=new_run_id(),
        stop_stage=args.stop_stage,
        stop_stage_after=args.stop_stage_after,
        observations_path=args.observations,
        max_cycles=args.max_cycles,
        speed=args.speed,
    )
    # SIGTERM (a service manager stopping the watch) finishes the current cycle
    # and shuts down cleanly, like Ctrl-C does.
    previous = signal.signal(signal.SIGTERM, lambda _sig, _frame: runner.stop())
    try:
        runner.run()
    finally:
        signal.signal(signal.SIGTERM, previous)
        context.log.close()
    return 0


def _watchdog(args) -> int:
    try:
        watchdog = build_watchdog(args.config, args.log, record_path=args.alarm_record)
    except StartupError as exc:
        print(f"station-watch: {exc}", file=sys.stderr)
        return 1
    try:
        watchdog.run(interval_s=args.interval, max_checks=args.max_checks)
    except KeyboardInterrupt:
        pass
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return _run(args)
    if args.command == "watchdog":
        return _watchdog(args)
    if args.command == "fetch-model":
        return _fetch_model(args)
    parser.error(f"unknown command: {args.command}")
    return 2


__all__ = ["main", "build_parser"]
