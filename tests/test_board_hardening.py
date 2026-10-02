"""Board hardening from PR #6 review r2: a safe ``mode=ro`` URI and Host checks.

* A Log path containing ``?``, ``#`` or ``%`` must neither truncate the path nor
  displace ``mode=ro`` -- the reader still reads the right file and still cannot
  write.
* The loopback HTTP server rejects a request whose ``Host`` is not
  ``127.0.0.1:<port>`` or ``localhost:<port>`` (DNS-rebinding defence).

HF3.2 extends the server hardening: every response carries ``nosniff`` and
``no-store``; a foreign ``Origin`` is refused by the code; the server threads so
one stalled connection cannot starve the page; and rejections -- not successful
GETs -- write one stderr line each.
"""

from __future__ import annotations

import http.client
import socket
import sqlite3
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from station_watch.board import LogReader, make_server
from station_watch.log import Log
from station_watch.records import Verdict, VerdictState

sys.path.insert(0, str(Path(__file__).parent))
from helpers.records import frame_at, health_config  # noqa: E402

EPOCH = "2026-09-28T00:00:00.000000+00:00"
RUN = "run-hardening"


def _log_at(path: Path) -> Path:
    with Log(path) as log:
        log.append(frame_at(EPOCH, RUN, 0))
        log.append(
            Verdict(
                station_id="station-1",
                ts=EPOCH,
                state=VerdictState.HEALTHY,
                faults=(),
                blind_reasons=(),
                seq=1,
                run_id=RUN,
            )
        )
    return path


@pytest.mark.parametrize("dirname", ["we?ird", "has#hash", "pct%3Fdir", "a?mode=rw"])
def test_reader_opens_a_path_with_uri_metacharacters_read_only(tmp_path, dirname):
    folder = tmp_path / dirname
    folder.mkdir()
    log_path = _log_at(folder / "log.db")
    before = sorted(p.name for p in tmp_path.iterdir())

    with LogReader(log_path) as reader:
        assert reader.newest("verdict").state == VerdictState.HEALTHY
        with pytest.raises(sqlite3.OperationalError):
            reader.connection.execute(
                "INSERT INTO records (record_id, kind, ts, run_id, body) "
                "VALUES ('x', 'frame', 't', 'r', '{}')"
            )
            reader.connection.commit()

    assert sorted(p.name for p in tmp_path.iterdir()) == before  # no stray truncated-path db


class _Served:
    def __init__(self, log_path: Path):
        self.server = make_server(health_config(), log_path, port=0, clock=lambda: EPOCH)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> int:
        self.thread.start()
        return self.server.server_address[1]

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def _get(port: int, host_header: str | None, path: str = "/view.json") -> tuple[int, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.putrequest("GET", path, skip_host=True)
        if host_header is not None:
            conn.putheader("Host", host_header)
        conn.endheaders()
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def test_a_foreign_host_header_is_rejected_403(tmp_path):
    log_path = _log_at(tmp_path / "log.db")
    with _Served(log_path) as port:
        for host in (f"evil.example:{port}", "evil.example", f"127.0.0.1:{port + 1}", "localhost"):
            for path in ("/", "/view.json"):
                status, body = _get(port, host, path)
                assert status == 403, (host, path)
                assert b"station-1" not in body, (host, path)


def test_loopback_host_headers_are_served(tmp_path):
    log_path = _log_at(tmp_path / "log.db")
    with _Served(log_path) as port:
        for host in (f"127.0.0.1:{port}", f"localhost:{port}", f"LOCALHOST:{port}"):
            status, body = _get(port, host)
            assert status == 200, host
            assert b"station-1" in body, host


def test_a_missing_host_header_is_rejected_403(tmp_path):
    log_path = _log_at(tmp_path / "log.db")
    with _Served(log_path) as port:
        status, body = _get(port, None)
        assert status == 403
        assert b"station-1" not in body


def _request(
    port: int,
    *,
    method: str = "GET",
    path: str = "/view.json",
    host: str | None = None,
    origin: str | None = None,
):
    """A raw request letting a test set Host/Origin independently of the client."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.putrequest(method, path, skip_host=True)
        conn.putheader("Host", host if host is not None else f"127.0.0.1:{port}")
        if origin is not None:
            conn.putheader("Origin", origin)
        conn.endheaders()
        resp = conn.getresponse()
        return resp.status, dict(resp.getheaders()), resp.read()
    finally:
        conn.close()


# --- AC2: every response carries nosniff and no-store ---------------------------


def test_every_response_carries_the_security_headers(tmp_path):
    log_path = _log_at(tmp_path / "log.db")
    with _Served(log_path) as port:
        cases = [
            dict(path="/view.json"),  # 200
            dict(host="evil.example"),  # 403 (bad host)
            dict(origin="http://evil.example"),  # 403 (bad origin)
            dict(path="/nope"),  # 404
            dict(method="POST", path="/"),  # 405
        ]
        for case in cases:
            status, headers, _ = _request(port, **case)
            assert headers.get("X-Content-Type-Options") == "nosniff", (case, status)
            assert headers.get("Cache-Control") == "no-store", (case, status)


# --- AC3: a foreign Origin is refused by the code -------------------------------


def test_a_foreign_origin_is_rejected_403(tmp_path):
    log_path = _log_at(tmp_path / "log.db")
    with _Served(log_path) as port:
        status, _, body = _request(port, origin="http://evil.example")
        assert status == 403
        assert b"station-1" not in body


def test_the_boards_own_origin_or_no_origin_is_served(tmp_path):
    log_path = _log_at(tmp_path / "log.db")
    with _Served(log_path) as port:
        for origin in (None, f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
            status, _, body = _request(port, origin=origin)
            assert status == 200, origin
            assert b"station-1" in body, origin


# --- AC4: a stalled connection does not starve a second client ------------------


def test_the_server_is_a_threading_server(tmp_path):
    log_path = _log_at(tmp_path / "log.db")
    server = make_server(health_config(), log_path, port=0, clock=lambda: EPOCH)
    try:
        assert isinstance(server, ThreadingHTTPServer)
        assert server.daemon_threads is True
    finally:
        server.server_close()


def test_a_silent_client_does_not_block_a_second_clients_get(tmp_path):
    log_path = _log_at(tmp_path / "log.db")
    with _Served(log_path) as port:
        stalled = socket.create_connection(("127.0.0.1", port), timeout=5)
        try:
            # The stalled client sends nothing. A second client's GET must return.
            start = time.monotonic()
            status, body = _get(port, f"127.0.0.1:{port}")
            elapsed = time.monotonic() - start
            assert status == 200
            assert elapsed < 2.0, f"second GET blocked for {elapsed:.2f}s behind a silent client"
        finally:
            stalled.close()


# --- AC5: rejections write one stderr line each; a 200 GET writes none ----------


def test_rejections_log_one_line_each_and_a_get_stays_quiet(tmp_path, capfd):
    log_path = _log_at(tmp_path / "log.db")
    with _Served(log_path) as port:
        assert _request(port, host="evil.example", path="/bad-host-probe")[0] == 403
        assert _request(port, method="POST", path="/")[0] == 405
        assert _request(port, path="/view.json")[0] == 200  # the quiet, successful GET
    err_lines = [line for line in capfd.readouterr().err.splitlines() if line.strip()]
    assert len(err_lines) == 2, err_lines
    joined = "\n".join(err_lines)
    assert "403" in joined and "405" in joined
    assert "POST" in joined  # the method is named
    assert "/bad-host-probe" in joined  # the path is named
    assert "/view.json" not in joined  # the successful GET writes no line
