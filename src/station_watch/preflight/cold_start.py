"""``preflight --cold-start``: seconds from launching ``run`` to its first rows.

Launches the real ``station-watch run`` as a child (``python -m station_watch run``),
then polls the Log read-only (SQLite ``mode=ro``) for this child's first
``FrameRecord``, first verdict, and first ``healthy`` verdict. Each timing is the
row's own ``ts`` (``run`` stamps rows with the wall clock) minus the wall-clock
launch instant. Rows an earlier run left in the same Log never count: only rows
inserted after the launch (``rowid`` above the pre-launch high-water mark) do.

The poll is bounded by ``timeout_s``. When no healthy verdict appears in time, or
the child exits before one does, the result is ``no_healthy_verdict`` with a
reason and no numbers (K13): a cold start that never finished has no cold-start
time. The child is always stopped (SIGTERM, then a kill) before returning.
"""

from __future__ import annotations

import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

NO_HEALTHY = "no_healthy_verdict"
DEFAULT_TIMEOUT_S = 60.0
_POLL_S = 0.05
_STOP_GRACE_S = 10.0

# (metric key, record kind, required verdict state or None), in the order they land.
_MILESTONES = (
    ("launch_to_first_frame_s", "frame", None),
    ("launch_to_first_verdict_s", "verdict", None),
    ("launch_to_first_healthy_s", "verdict", "healthy"),
)


@dataclass(frozen=True)
class ColdStart:
    """One cold-start outcome: the timings, or a status and a reason with none."""

    status: str
    metrics: dict | None
    reason: str | None = None


def run_command(config: str, source: str, log: str) -> list[str]:
    """The child: the real ``run`` subcommand, through the package entry point."""
    return [
        sys.executable,
        "-m",
        "station_watch",
        "run",
        "--config",
        config,
        "--source",
        source,
        "--log",
        log,
    ]


def _connect(log_path: Path) -> sqlite3.Connection | None:
    if not log_path.exists():
        return None
    try:
        return sqlite3.connect(f"file:{log_path.resolve()}?mode=ro", uri=True)
    except sqlite3.Error:
        return None


def _high_water(log_path: Path) -> int:
    """The largest ``rowid`` already in the Log (0 for a new or empty Log)."""
    conn = _connect(log_path)
    if conn is None:
        return 0
    try:
        return conn.execute("SELECT coalesce(max(rowid), 0) FROM records").fetchone()[0]
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


def _first_ts(conn: sqlite3.Connection, after: int, kind: str, state: str | None) -> str | None:
    sql = "SELECT ts FROM records WHERE rowid > ? AND kind = ?"
    params: list = [after, kind]
    if state is not None:
        sql += " AND json_extract(body, '$.state') = ?"
        params.append(state)
    try:
        row = conn.execute(sql + " ORDER BY rowid LIMIT 1", params).fetchone()
    except sqlite3.Error:  # schema not created yet by the child
        return None
    return row[0] if row else None


def _seconds_since(launch: datetime, ts: str) -> float:
    return (datetime.fromisoformat(ts) - launch).total_seconds()


def _poll(log_path: Path, after: int, launch: datetime, found: dict) -> None:
    """Fill ``found`` with every milestone now visible in the Log."""
    conn = _connect(log_path)
    if conn is None:
        return
    try:
        for key, kind, state in _MILESTONES:
            if key not in found:
                ts = _first_ts(conn, after, kind, state)
                if ts is not None:
                    found[key] = _seconds_since(launch, ts)
    finally:
        conn.close()


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=_STOP_GRACE_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def _tail(path: Path, lines: int = 3) -> str:
    text = path.read_text(errors="replace").strip().splitlines()
    return " | ".join(text[-lines:])


def _watch(proc, log_path: Path, after: int, launch: datetime, timeout_s: float, err) -> ColdStart:
    found: dict = {}
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        exited = proc.poll() is not None
        _poll(log_path, after, launch, found)
        if len(found) == len(_MILESTONES):
            return ColdStart("ok", {key: found[key] for key, _, _ in _MILESTONES})
        if exited:
            reason = f"run exited with code {proc.returncode} before a healthy verdict"
            detail = _tail(Path(err.name))
            return ColdStart(NO_HEALTHY, None, f"{reason}: {detail}" if detail else reason)
        time.sleep(_POLL_S)
    return ColdStart(NO_HEALTHY, None, f"no healthy verdict within {timeout_s:g}s of launch")


def measure_cold_start(
    config: str, source: str, log: str, *, timeout_s: float = DEFAULT_TIMEOUT_S
) -> ColdStart:
    """Launch ``run``, time its first frame, verdict and healthy verdict, then stop it."""
    log_path = Path(log)
    after = _high_water(log_path)
    with tempfile.NamedTemporaryFile("w+", suffix=".stderr") as err:
        launch = datetime.now(UTC)
        proc = subprocess.Popen(
            run_command(config, source, log), stdout=subprocess.DEVNULL, stderr=err
        )
        try:
            return _watch(proc, log_path, after, launch, timeout_s, err)
        finally:
            _stop(proc)


__all__ = ["ColdStart", "measure_cold_start", "run_command", "NO_HEALTHY", "DEFAULT_TIMEOUT_S"]
