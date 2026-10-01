"""The ``station-watch board`` subcommand: a read-only operator screen.

``board --once`` prints the plain-text view and exits; otherwise it serves one
auto-refreshing HTML page and a JSON view on loopback. The config is required
(the view's windows come from it) and is loaded fail-loud, naming the missing
piece (K9); a missing or unreadable *Log*, by contrast, is not a startup failure
-- it renders UNKNOWN, so the screen is still useful while the run is starting.
"""

from __future__ import annotations

from pathlib import Path

from station_watch.board.render import render_text
from station_watch.board.server import make_server
from station_watch.board.view import build_view
from station_watch.clock import utc_now_iso
from station_watch.config import load_station_config


def add_parser(sub) -> None:
    board = sub.add_parser(
        "board",
        help="a read-only operator screen built from the Log",
        description="Open the Log read-only and render one view per station: state, frame age, "
        "blind reasons, fault flags and open alarm episodes. Never writes; a missing or stale "
        "Log reads as UNKNOWN, never OK.",
    )
    board.add_argument("--config", required=True, help="path to the station config YAML")
    board.add_argument("--log", required=True, help="path to the run's append-only Log")
    board.add_argument("--port", type=int, default=8765, help="loopback port for the HTTP screen")
    board.add_argument(
        "--once",
        action="store_true",
        help="print the view as plain text once and exit (no server)",
    )


def _load_config(path: str):
    if not Path(path).exists():
        raise SystemExit(f"station-watch: config file not found: {path}")
    try:
        return load_station_config(path)
    except KeyError as exc:
        raise SystemExit(f"station-watch: {exc.args[0]}") from exc
    except (ValueError, OSError) as exc:
        raise SystemExit(f"station-watch: could not read config {path}: {exc}") from exc


def handle(args) -> int:
    config = _load_config(args.config)
    if args.once:
        print(render_text(build_view(config, args.log, now=utc_now_iso())))
        return 0
    server = make_server(config, args.log, port=args.port)
    host, port = server.server_address[:2]
    print(
        f"station-watch board: serving {config.station_id} "
        f"on http://{host}:{port}/ (Ctrl-C to stop)"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


__all__ = ["add_parser", "handle"]
