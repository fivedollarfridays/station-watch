"""The system-facing preflight checks: config, weights, Log, disk, clock, alarms, Board.

These read everything a live run leans on besides the camera: the config loads and
validates (K9 message on failure); the keep-out weights are present and hash-verified
(only when zones are configured, else SKIP naming why); the Log's directory takes a
probe SQLite file that is then removed and an existing Log opens read-only; free disk
at the Log and evidence dirs clears ``preflight.min_free_gb``; the wall clock is not
behind the newest Log row and sits in a plausible year (NTP itself is not checked);
every configured alarm sink constructs and the bundled tone asset is present; and,
given ``--board-port``, a running Board answers its JSON view on loopback.

Preflight only reads the camera and the Log: nothing here writes anything but the
``log_writable`` probe file, which it removes.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import yaml

from station_watch.alarm.tone import default_tone_path
from station_watch.board.reader import BoardLogError, LogReader
from station_watch.clock import to_iso
from station_watch.detect.yolox import WeightsError, default_model_path, verify_weights
from station_watch.preflight.context import PreflightContext
from station_watch.preflight.result import FAIL, PASS, SKIP, CheckResult
from station_watch.runner.startup import (
    StartupError,
    build_alarm_sinks,
    default_evidence_dir,
    load_config,
)

# Free space a passing preflight wants at both the Log and evidence dirs, in GiB,
# unless the config overrides it with ``preflight.min_free_gb``.
_DEFAULT_MIN_FREE_GB = 1.0
_BOARD_TIMEOUT_S = 2.0
_LOOPBACK = "127.0.0.1"


def check_config(ctx: PreflightContext) -> CheckResult:
    """The config loads and validates; a failure carries the K9 dotted key."""
    try:
        ctx.station_config = load_config(ctx.config_path)
    except StartupError as exc:
        ctx.config_error = str(exc)
        return CheckResult("config", FAIL, str(exc))
    try:
        ctx.raw_config = yaml.safe_load(Path(ctx.config_path).read_text())
    except (OSError, yaml.YAMLError):
        ctx.raw_config = {}
    return CheckResult("config", PASS, f"loaded station {ctx.station_config.station_id}")


def check_model_weights(ctx: PreflightContext) -> CheckResult:
    """Keep-out weights present and SHA-256 verified -- only when zones need them."""
    if ctx.station_config is None:
        return CheckResult("model_weights", SKIP, "config did not load")
    if not ctx.station_config.keepout_zones:
        return CheckResult("model_weights", SKIP, "no keep-out zones configured")
    keepout = ctx.station_config.detect.get("keepout", {})
    model_path = keepout.get("model_path", str(default_model_path()))
    try:
        verify_weights(model_path)
    except WeightsError as exc:
        return CheckResult("model_weights", FAIL, str(exc))
    return CheckResult("model_weights", PASS, f"weights present and SHA-256 verified: {model_path}")


def check_log_writable(ctx: PreflightContext) -> CheckResult:
    """The Log's dir takes (and releases) a probe SQLite file; an existing Log opens."""
    log_path = Path(ctx.log_path)
    directory = log_path.parent
    if not directory.exists():
        return CheckResult("log_writable", FAIL, f"log directory does not exist: {directory}")
    probe = directory / f".preflight-probe-{os.getpid()}.db"
    try:
        conn = sqlite3.connect(str(probe))
        conn.execute("CREATE TABLE probe (x)")
        conn.commit()
        conn.close()
    except (sqlite3.Error, OSError) as exc:
        return CheckResult("log_writable", FAIL, f"cannot write a probe file in {directory}: {exc}")
    finally:
        try:
            probe.unlink()
        except OSError:
            pass
    if log_path.exists():
        try:
            LogReader(log_path).close()
        except BoardLogError as exc:
            return CheckResult("log_writable", FAIL, f"existing log will not open: {exc}")
    return CheckResult("log_writable", PASS, f"probe written and removed in {directory}")


def _free_gb(path: str | Path) -> float:
    """Free GiB on the filesystem holding ``path`` (or its nearest existing parent)."""
    resolved = Path(path)
    while not resolved.exists() and resolved != resolved.parent:
        resolved = resolved.parent
    return shutil.disk_usage(resolved).free / (1024**3)


def check_disk_space(ctx: PreflightContext) -> CheckResult:
    """Free space at the Log and evidence dirs clears ``preflight.min_free_gb``."""
    raw = ctx.raw_config or {}
    min_free = float(raw.get("preflight", {}).get("min_free_gb", _DEFAULT_MIN_FREE_GB))
    dirs = {"log": Path(ctx.log_path).parent, "evidence": Path(default_evidence_dir())}
    bits, low = [], []
    for label, path in dirs.items():
        free = _free_gb(path)
        bits.append(f"{label}={free:.1f}GB")
        if free < min_free:
            low.append(f"{label} ({free:.1f}GB)")
    if low:
        return CheckResult("disk_space", FAIL, f"below {min_free}GB free at: {', '.join(low)}")
    return CheckResult("disk_space", PASS, f"free {', '.join(bits)} (min {min_free}GB)")


def _newest_log_ts(log_path: str) -> str | None:
    """The newest ts across all Log rows, or ``None`` if the Log is absent/empty."""
    if not Path(log_path).exists():
        return None
    try:
        reader = LogReader(log_path)
    except BoardLogError:
        return None
    try:
        row = reader.connection.execute("SELECT MAX(ts) FROM records").fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None
    finally:
        reader.close()


def check_clock(ctx: PreflightContext) -> CheckResult:
    """Wall clock not behind the newest Log row and in a plausible year (NTP not checked)."""
    moment = datetime.now(UTC)
    now = to_iso(moment)
    newest = _newest_log_ts(ctx.log_path)
    suffix = "NTP sync is not checked"
    if newest is not None and now < newest:
        return CheckResult(
            "clock", FAIL, f"wall clock {now} is behind newest log ts {newest}; {suffix}"
        )
    if not (2020 <= moment.year <= 2100):
        return CheckResult("clock", FAIL, f"implausible year {moment.year}; {suffix}")
    seen = f"newest log ts={newest}" if newest is not None else "empty log"
    return CheckResult("clock", PASS, f"now={now}, {seen}; {suffix}")


def check_alarm_sinks(ctx: PreflightContext) -> CheckResult:
    """Every configured alarm sink constructs and the bundled tone asset is present."""
    if ctx.station_config is None:
        return CheckResult("alarm_sinks", FAIL, "config did not load")
    try:
        sinks = build_alarm_sinks(ctx.station_config, record_path=os.devnull)
    except StartupError as exc:
        return CheckResult("alarm_sinks", FAIL, str(exc))
    names = ctx.station_config.alarm.get("sinks", [])
    if "sound" in names and not default_tone_path().exists():
        return CheckResult("alarm_sinks", FAIL, f"alarm sound asset missing: {default_tone_path()}")
    return CheckResult(
        "alarm_sinks", PASS, f"{len(sinks)} sink(s): {', '.join(names)}; sound asset present"
    )


def check_board(ctx: PreflightContext) -> CheckResult:
    """With ``--board-port``, the Board's JSON view answers 200 on loopback within 2 s."""
    if ctx.board_port is None:
        return CheckResult("board", SKIP, "no --board-port given")
    url = f"http://{_LOOPBACK}:{ctx.board_port}/view.json"
    try:
        with urllib.request.urlopen(url, timeout=_BOARD_TIMEOUT_S) as response:
            status = response.status
    except (urllib.error.URLError, OSError) as exc:
        return CheckResult(
            "board", FAIL, f"board not answering on {_LOOPBACK}:{ctx.board_port}: {exc}"
        )
    if status != 200:
        return CheckResult(
            "board", FAIL, f"board returned HTTP {status} on {_LOOPBACK}:{ctx.board_port}"
        )
    return CheckResult("board", PASS, f"GET /view.json -> 200 on {_LOOPBACK}:{ctx.board_port}")


__all__ = [
    "check_config",
    "check_model_weights",
    "check_log_writable",
    "check_disk_space",
    "check_clock",
    "check_alarm_sinks",
    "check_board",
]
