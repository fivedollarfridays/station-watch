"""Argument wiring for the ``station-watch preflight`` subcommand.

Kept out of :mod:`station_watch.cli` (like ``evaluate``, ``board`` and ``soak``) so
building the parser never pulls in OpenCV, the Board reader or the alarm stack: the
checks and their heavy imports live in :mod:`station_watch.preflight.runner`, imported
lazily inside :func:`handle`.

``preflight`` reads the camera and the Log, writes nothing but a probe file it removes,
prints one PASS/FAIL/WARN/SKIP line per check (or ``--json``), and exits 0 only when no
check is a FAIL. ``--list-cameras`` is a separate mode that only enumerates the device
indexes that open (how the operator finds the right ``--source``) and always exits 0.
``--cold-start`` is the third mode: it launches a real ``run`` and writes how long the
station took to its first frame, verdict and healthy verdict
(:mod:`station_watch.preflight.cold_start_report`).
"""

from __future__ import annotations

import sys


def add_parser(sub) -> None:
    """Add the ``preflight`` subparser and its arguments to ``sub``."""
    preflight = sub.add_parser(
        "preflight",
        help="PREFLIGHT: PASS or FAIL per item before going live",
        description="Run the ordered go-live checks (config, camera, live frames, fiducial, "
        "camera stability, keep-out weights, Log writability, disk, clock, alarm sinks, Board) "
        "against one station. Reads the camera and the Log, writes nothing but a probe file it "
        "removes, and exits 0 only when no check is a FAIL. With --list-cameras it instead "
        "enumerates the device indexes that open and exits 0.",
    )
    # --config/--source/--log are not required at parse time because --list-cameras
    # needs none of them; `handle` requires all three for the actual checks.
    preflight.add_argument("--config", help="path to the station config YAML")
    preflight.add_argument("--source", help="camera device index or a recorded file path")
    preflight.add_argument("--log", help="path to the append-only Log database")
    preflight.add_argument(
        "--board-port",
        type=int,
        default=None,
        help="loopback port of a running Board to probe (omit to SKIP the board check)",
    )
    preflight.add_argument(
        "--drift-s",
        type=float,
        default=5.0,
        help="seconds the camera_stability check samples the fiducial center for drift",
    )
    preflight.add_argument(
        "--list-cameras",
        action="store_true",
        help="enumerate device indexes 0..N that open (find the right --source) and exit 0",
    )
    preflight.add_argument(
        "--max-index",
        type=int,
        default=5,
        help="highest device index --list-cameras tries (default 5)",
    )
    preflight.add_argument(
        "--json", action="store_true", dest="as_json", help="print the results as JSON"
    )
    _add_cold_start_args(preflight)


def _add_cold_start_args(preflight) -> None:
    """``--cold-start``: time a real ``run`` from launch to its first healthy verdict."""
    preflight.add_argument(
        "--cold-start",
        action="store_true",
        help="launch `station-watch run` and measure launch-to-first frame, verdict and "
        "healthy verdict; writes a measurement file to --out",
    )
    preflight.add_argument("--out", help="--cold-start: measurement file to write")
    preflight.add_argument(
        "--dataset-kind",
        choices=["real", "synthetic"],
        help="--cold-start: real (a camera) writes under measurements/v1|v2/, synthetic "
        "(a generated clip) under measurements/synthetic/",
    )
    preflight.add_argument(
        "--force-out",
        action="store_true",
        help="--cold-start: write --out outside its dataset kind's tree (prints a warning)",
    )
    preflight.add_argument(
        "--timeout-s",
        type=float,
        default=60.0,
        help="--cold-start: give up with status no_healthy_verdict after this many seconds",
    )


def handle(args) -> int:
    """Run preflight (or --list-cameras) for parsed ``preflight`` ``args``."""
    if args.cold_start:
        from station_watch.preflight.cold_start_report import handle_cold_start

        return handle_cold_start(args)
    if args.list_cameras:
        from station_watch.preflight.cameras import probe_cameras, render_camera_list

        print(render_camera_list(probe_cameras(args.max_index)))
        return 0

    missing = [name for name in ("config", "source", "log") if getattr(args, name) is None]
    if missing:
        print(
            "station-watch preflight: " + ", ".join(f"--{name}" for name in missing) + " required "
            "(or use --list-cameras)",
            file=sys.stderr,
        )
        return 2

    from station_watch.preflight.runner import (
        preflight_ok,
        render_json,
        render_text,
        run_preflight,
    )

    results = run_preflight(
        args.config, args.source, args.log, board_port=args.board_port, drift_s=args.drift_s
    )
    print(render_json(results) if args.as_json else render_text(results))
    return 0 if preflight_ok(results) else 1


__all__ = ["add_parser", "handle"]
