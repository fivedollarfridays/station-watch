"""Rejected-request log lines cannot be forged or leak a query token (both HTTP faces)."""

from __future__ import annotations

import socket
import sys
import threading
from pathlib import Path

from station_watch.board import make_server
from station_watch.log import Log
from station_watch.logsafe import loggable_path

sys.path.insert(0, str(Path(__file__).parent))
from helpers.records import frame_at, health_config  # noqa: E402

EPOCH = "2026-10-01T00:00:00.000000+00:00"


def test_loggable_path_drops_the_query_and_escapes_control_characters():
    assert loggable_path("/view.json?token=secret") == "/view.json"
    assert loggable_path("/a\x1b[2J\x00b") == "/a\\x1b[2J\\x00b"
    assert len(loggable_path("/" + "x" * 1000)) == 200


def test_board_rejection_log_escapes_a_control_character_path(tmp_path, capfd):
    log_path = tmp_path / "log.db"
    with Log(log_path) as log:
        log.append(frame_at(EPOCH, "run-1"))
    server = make_server(health_config(), log_path, port=0, clock=lambda: EPOCH)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with socket.create_connection(server.server_address[:2], timeout=5) as sock:
            sock.sendall(b"GET /\x1b[2Jforged?k=v HTTP/1.1\r\nHost: evil.example\r\n\r\n")
            sock.recv(4096)
    finally:
        server.shutdown()
        server.server_close()
    err = capfd.readouterr().err
    assert "station-watch board: 403" in err
    assert "\x1b" not in err and "\\x1b[2Jforged" in err and "k=v" not in err
