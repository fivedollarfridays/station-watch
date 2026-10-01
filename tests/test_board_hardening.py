"""Board hardening from PR #6 review r2: a safe ``mode=ro`` URI and Host checks.

* A Log path containing ``?``, ``#`` or ``%`` must neither truncate the path nor
  displace ``mode=ro`` -- the reader still reads the right file and still cannot
  write.
* The loopback HTTP server rejects a request whose ``Host`` is not
  ``127.0.0.1:<port>`` or ``localhost:<port>`` (DNS-rebinding defence).
"""

from __future__ import annotations

import http.client
import sqlite3
import sys
import threading
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
