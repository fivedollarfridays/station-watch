"""Argument wiring for the ``station-watch preflight`` subcommand.

Kept out of :mod:`station_watch.cli` (like ``evaluate``, ``board`` and ``soak``) so
building the parser never pulls in OpenCV, the Board reader or the alarm stack: the
checks and their heavy imports live in :mod:`station_watch.preflight.runner`, imported
lazily inside :func:`handle`.

``preflight`` reads the camera and the Log, writes nothing but a probe file it removes,
prints one PASS/FAIL/WARN/SKIP line per check (or ``--json``), and exits 0 only when no
check is a FAIL.
"""

from __future__ import annotations


def add_parser(sub) -> None:
    """Add the ``preflight`` subparser and its arguments to ``sub``."""
    preflight = sub.add_parser(
        "preflight",
        help="PREFLIGHT: PASS or FAIL per item before going live",
        description="Run the ordered go-live checks (config, camera, live frames, fiducial, "
        "keep-out weights, Log writability, disk, clock, alarm sinks, Board) against one "
        "station. Reads the camera and the Log, writes nothing but a probe file it removes, "
        "and exits 0 only when no check is a FAIL.",
    )
    preflight.add_argument("--config", required=True, help="path to the station config YAML")
    preflight.add_argument(
        "--source", required=True, help="camera device index or a recorded file path"
    )
    preflight.add_argument("--log", required=True, help="path to the append-only Log database")
    preflight.add_argument(
        "--board-port",
        type=int,
        default=None,
        help="loopback port of a running Board to probe (omit to SKIP the board check)",
    )
    preflight.add_argument(
        "--json", action="store_true", dest="as_json", help="print the results as JSON"
    )


def handle(args) -> int:
    """Run preflight for parsed ``preflight`` ``args``; exit 0 only on no FAIL."""
    from station_watch.preflight.runner import (
        preflight_ok,
        render_json,
        render_text,
        run_preflight,
    )

    results = run_preflight(args.config, args.source, args.log, board_port=args.board_port)
    print(render_json(results) if args.as_json else render_text(results))
    return 0 if preflight_ok(results) else 1


__all__ = ["add_parser", "handle"]
