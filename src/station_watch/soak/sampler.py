"""One :class:`Sample` per soak interval: memory, Log size, rows, Board latency.

A sample is a cheap, stdlib-only snapshot taken every ``--sample-s`` while the soak
runs. RSS per named child comes from ``ps -o rss= -p <pid>`` (macOS and Linux); a
dead pid is recorded as ``None`` (dead), never as ``0`` -- a crashed child must not
read as a child using no memory. The Log's file and WAL bytes come from ``stat``;
row counts per kind and the verdict total/newest ts come from a read-only
:class:`~station_watch.board.reader.LogReader`. The Board's ``/view.json`` render
time is a timed loopback GET; a failed render is recorded as a reason
(:attr:`Sample.board_error`), never as a fast time.
"""

from __future__ import annotations

import http.client
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from station_watch.board.reader import BoardLogError, LogReader

# SQLite reads LIMIT -1 as "every row"; the row-count query is a GROUP BY instead.
_HTTP_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class Sample:
    """One interval's snapshot (consumed by :mod:`station_watch.soak.verdict`)."""

    elapsed_s: float
    rss_kb: dict  # process name -> resident KB, or None when the pid is dead
    log_bytes: int
    wal_bytes: int
    rows: dict  # Log kind -> row count
    board_render_s: float | None  # seconds for a successful /view.json GET, else None
    board_error: str | None  # the reason a render failed, else None
    verdicts: int
    newest_verdict_ts: str | None


def _rss_kb(pid: int) -> int | None:
    """Resident set size in KB for ``pid`` via ``ps``, or ``None`` if it is gone."""
    try:
        result = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=_HTTP_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = result.stdout.strip()
    if result.returncode != 0 or not text:
        return None
    try:
        return int(text.split()[0])
    except (ValueError, IndexError):
        return None


def _log_sizes(log_path: Path) -> tuple[int, int]:
    """``(log_bytes, wal_bytes)`` for the Log db and its WAL side file (0 if absent)."""
    log_bytes = log_path.stat().st_size if log_path.exists() else 0
    wal = Path(str(log_path) + "-wal")
    wal_bytes = wal.stat().st_size if wal.exists() else 0
    return log_bytes, wal_bytes


def _rows_and_verdicts(log_path: Path) -> tuple[dict, int, str | None]:
    """Row counts per kind, the verdict total, and the newest verdict ts (read-only).

    A missing or unreadable Log -- the run child may not have created it yet when the
    first sample is taken -- yields empty counts rather than raising, so sampling never
    kills the soak over a Log that simply is not there yet.
    """
    try:
        with LogReader(log_path) as reader:
            rows = {
                kind: count
                for kind, count in reader.connection.execute(
                    "SELECT kind, COUNT(*) FROM records GROUP BY kind"
                )
            }
            newest = reader.newest("verdict")
    except BoardLogError:
        return {}, 0, None
    return rows, rows.get("verdict", 0), (newest.ts if newest is not None else None)


def _time_board_render(port: int) -> tuple[float | None, str | None]:
    """Time a loopback ``GET /view.json``; return ``(seconds, None)`` or ``(None, reason)``.

    The ``Host`` header is the loopback the Board allows, so the request is not
    rejected as a cross-host read. A non-200, a connection refused, or any socket
    error is a failure recorded as its reason, never a (misleadingly fast) time.
    """
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=_HTTP_TIMEOUT_S)
    start = time.monotonic()
    try:
        conn.request("GET", "/view.json", headers={"Host": f"127.0.0.1:{port}"})
        response = conn.getresponse()
        body = response.read()
        elapsed = time.monotonic() - start
        if response.status != 200:
            return None, f"HTTP {response.status}"
        if not body:
            return None, "empty body"
        return elapsed, None
    except (OSError, http.client.HTTPException) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    finally:
        conn.close()


def take_sample(*, elapsed_s: float, pids: dict, log_path, board_port: int | None) -> Sample:
    """Snapshot RSS per child, Log sizes and rows, and Board render time at ``elapsed_s``."""
    log_path = Path(log_path)
    rss_kb = {name: _rss_kb(pid) for name, pid in pids.items()}
    log_bytes, wal_bytes = _log_sizes(log_path)
    rows, verdicts, newest_verdict_ts = _rows_and_verdicts(log_path)
    if board_port is None:
        board_render_s, board_error = None, None
    else:
        board_render_s, board_error = _time_board_render(board_port)
    return Sample(
        elapsed_s=elapsed_s,
        rss_kb=rss_kb,
        log_bytes=log_bytes,
        wal_bytes=wal_bytes,
        rows=rows,
        board_render_s=board_render_s,
        board_error=board_error,
        verdicts=verdicts,
        newest_verdict_ts=newest_verdict_ts,
    )


__all__ = ["Sample", "take_sample"]
