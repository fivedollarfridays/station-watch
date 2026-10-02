"""``audit review`` gates every data-bearing route on the session token.

The page carries evidence thumbnails, flag targets and reviewer notes, so ``GET /``
without the token is 401 and with a wrong one 403 (constant-time compare). The
operator signs in once through ``/?token=...`` (the URL printed at startup), which
sets the HttpOnly cookie and redirects to a clean ``/``; the cookie or the
``X-Audit-Token`` header then serves the page. Rejected requests are logged with the
path only (no query string, so never a token) and with control characters escaped,
so a request cannot forge log lines. A deeply nested JSON body is a 400, not a
crashed handler.
"""

from __future__ import annotations

import json
import socket
import threading

import pytest

from station_watch.audit.review_server import make_review_server

RUN = "run-auth"
FID = f"{RUN}:missing_part:rail_pos_1:1"
TS = "2026-10-01T00:00:00.000000+00:00"
TOKEN = "tok-auth-secret"


@pytest.fixture
def port(tmp_path):
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
    server = make_review_server(audit, [flag], [FID], "alice", port=0, token=TOKEN)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def _raw(
    port: int, request_line: str, headers: dict | None = None, body: bytes = b"", host=None
) -> str:
    lines = [request_line, f"Host: {host or f'127.0.0.1:{port}'}", "Connection: close"]
    lines += [f"{name}: {value}" for name, value in (headers or {}).items()]
    if body:
        lines.append(f"Content-Length: {len(body)}")
    data = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(data)
        chunks = []
        while chunk := sock.recv(65536):
            chunks.append(chunk)
    return b"".join(chunks).decode("latin-1")


def _status(reply: str) -> int:
    return int(reply.split()[1])


def test_get_page_without_a_token_is_401_and_reveals_nothing(port):
    reply = _raw(port, "GET / HTTP/1.1")
    assert _status(reply) == 401
    assert FID not in reply and "rail_pos_1" not in reply


def test_get_page_with_a_wrong_token_is_403(port):
    assert _status(_raw(port, "GET / HTTP/1.1", {"Cookie": "audit_token=wrong"})) == 403
    assert _status(_raw(port, "GET / HTTP/1.1", {"X-Audit-Token": "wrong"})) == 403
    assert _status(_raw(port, "GET /?token=wrong HTTP/1.1")) == 403


def test_login_url_sets_the_cookie_and_redirects_to_a_clean_path(port):
    reply = _raw(port, f"GET /?token={TOKEN} HTTP/1.1")
    assert _status(reply) == 303
    assert "\r\nLocation: /\r\n" in reply
    cookie_line = next(ln for ln in reply.split("\r\n") if ln.startswith("Set-Cookie:"))
    assert f"audit_token={TOKEN}" in cookie_line and "HttpOnly" in cookie_line
    assert FID not in reply  # the redirect itself carries no sheet


def test_cookie_or_header_with_the_token_serves_the_page(port):
    by_cookie = _raw(port, "GET / HTTP/1.1", {"Cookie": f"audit_token={TOKEN}"})
    by_header = _raw(port, "GET / HTTP/1.1", {"X-Audit-Token": TOKEN})
    assert _status(by_cookie) == 200 and FID in by_cookie
    assert _status(by_header) == 200 and FID in by_header
    assert TOKEN not in by_cookie.split("\r\n\r\n", 1)[1]  # never rendered into the body


def test_rejected_request_log_has_no_query_token_and_no_control_characters(port, capfd):
    _raw(port, "GET /?token=wrong-secret HTTP/1.1")
    _raw(port, "GET /\x1b[2Jforged HTTP/1.1", host="evil.example")  # a logged 403
    err = capfd.readouterr().err
    assert "wrong-secret" not in err
    assert "\x1b" not in err and "\\x1b" in err


def test_deeply_nested_json_body_is_a_400(port):
    body = ("[" * 30000 + "]" * 30000).encode()
    headers = {"X-Audit-Token": TOKEN, "Content-Type": "application/json"}
    assert _status(_raw(port, "POST /verdict HTTP/1.1", headers, body)) == 400
    ok = json.dumps({"flag_id": FID, "verdict": "correct"}).encode()
    assert _status(_raw(port, "POST /verdict HTTP/1.1", headers, ok)) == 200
