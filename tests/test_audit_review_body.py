"""``audit review``'s POST /verdict body handling: bounded, and a 400 on bad input.

A request carrying the session token can still send a malformed or huge
``Content-Length`` or a ``flag_id`` that is a JSON array or object. Each is a clean
4xx that appends nothing, never an unbounded read or a crashed handler; the server
keeps answering afterwards.
"""

from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

from station_watch.audit.review_server import MAX_BODY_BYTES, make_review_server

RUN = "run-body"
FID = f"{RUN}:missing_part:rail_pos_1:1"
TS = "2026-10-01T00:00:00.000000+00:00"
TOKEN = "tok-body"


def _serve(tmp_path: Path):
    flag = {
        "flag_id": FID,
        "kind": "missing_part",
        "target": "rail_pos_1",
        "station_id": "station-1",
        "run_id": RUN,
        "opened_ts": TS,
        "closed_ts": None,
        "frame_ids": [1],
    }
    audit = tmp_path / "audit"
    audit.mkdir()
    (audit / "flags.json").write_text(json.dumps([flag]))
    server = make_review_server(
        audit, [flag], [FID], "alice", port=0, token=TOKEN, clock=lambda: TS
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, audit


def _raw_post(port: int, length_header: str, body: bytes) -> int:
    """POST /verdict with a hand-written Content-Length; return the status code."""
    head = (
        f"POST /verdict HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
        f"X-Audit-Token: {TOKEN}\r\nContent-Type: application/json\r\n"
        f"Content-Length: {length_header}\r\nConnection: close\r\n\r\n"
    )
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(head.encode() + body)
        reply = sock.recv(4096).decode("latin-1")
    return int(reply.split()[1])


def _post_json(port: int, payload) -> int:
    body = json.dumps(payload).encode()
    return _raw_post(port, str(len(body)), body)


def test_malformed_oversized_and_unhashable_bodies_get_4xx_and_append_nothing(tmp_path):
    server, audit = _serve(tmp_path)
    port = server.server_address[1]
    try:
        assert _raw_post(port, "not-a-number", b"{}") == 400
        assert _raw_post(port, "-5", b"") == 400
        assert _raw_post(port, str(MAX_BODY_BYTES + 1), b"{}") == 413
        assert _post_json(port, {"flag_id": ["a", "list"], "verdict": "correct"}) == 400
        assert _post_json(port, {"flag_id": {"an": "object"}, "verdict": "correct"}) == 400
        # Still serving after every bad request.
        assert _post_json(port, {"flag_id": FID, "verdict": "correct"}) == 200
    finally:
        server.shutdown()
        server.server_close()
    lines = (audit / "verdicts.jsonl").read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["flag_id"] == FID
