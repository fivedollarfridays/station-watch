"""Component 7 (Board): the read-only operator screen built from the Log.

These tests build a Log with the real :class:`~station_watch.log.Log` writer, then
render it through the Board's read-only path (:func:`build_view`, the text/HTML
renderers and the HTTP server). They prove the proving case (healthy -> dark blind
-> stall fault), that rendering never touches the Log, the UNKNOWN/STALE cases,
the HTTP method and bind rules, and that the Board imports nothing from the alarm
sinks or the runner.
"""

from __future__ import annotations

import ast
import hashlib
import json
import sqlite3
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from station_watch.board import (
    LogReader,
    build_view,
    make_server,
    render_html,
    render_text,
    render_view_json,
)
from station_watch.clock import offset_iso
from station_watch.log import Log
from station_watch.records import (
    AlarmEvaluated,
    BlindReason,
    BlindState,
    Fault,
    FaultKind,
    Verdict,
    VerdictState,
)

sys.path.insert(0, str(Path(__file__).parent))
from helpers.records import blind_at, frame_at, health_config  # noqa: E402

EPOCH = "2026-09-28T00:00:00.000000+00:00"
RUN = "run-board"


def _t(offset: float) -> str:
    return offset_iso(EPOCH, offset)


def _proving_log(path: Path) -> Path:
    """A healthy run, then an open ``dark`` blind record, then a stall-fault verdict."""
    with Log(path) as log:
        log.append(frame_at(_t(0), RUN, 0))
        log.append(frame_at(_t(1), RUN, 1))
        log.append(
            Verdict(
                station_id="station-1",
                ts=_t(1),
                state=VerdictState.HEALTHY,
                faults=(),
                blind_reasons=(),
                seq=1,
                run_id=RUN,
            )
        )
        log.append(blind_at(_t(2), RUN, BlindReason.DARK, BlindState.OPENED, 1))
        log.append(frame_at(_t(3), RUN, 2))
        log.append(
            Verdict(
                station_id="station-1",
                ts=_t(3),
                state=VerdictState.FAULT,
                faults=(Fault(kind=FaultKind.STALLED, target="zone_press", frame_ids=(11, 12)),),
                blind_reasons=(),
                seq=2,
                run_id=RUN,
            )
        )
        log.append(
            AlarmEvaluated(
                ts=_t(3),
                seq=2,
                open_episodes=("stalled:zone_press", "unobservable:dark"),
                run_id=RUN,
            )
        )
    return path


# --- AC1: the proving case renders in both --once text and the HTML page -------


def test_proving_case_renders_state_frame_age_blind_flag_and_episodes(tmp_path):
    log_path = _proving_log(tmp_path / "log.db")
    config = health_config()
    view = build_view(config, log_path, now=_t(3.5))

    assert view.state == "FAULT"
    assert view.ok is False
    assert view.blind_reasons == ("dark",)
    assert view.flags[0].kind == "stalled"
    assert view.flags[0].target == "zone_press"
    assert view.flags[0].frame_ids == (11, 12)
    assert view.frame_age_s == pytest.approx(0.5, abs=0.01)
    assert view.frame_stale is False
    assert set(view.alarm_open_episodes) == {"stalled:zone_press", "unobservable:dark"}

    text = render_text(view)
    html = render_html(view)
    for token in (
        "FAULT",
        "dark",
        "stalled",
        "zone_press",
        "11",
        "12",
        "stalled:zone_press",
        "unobservable:dark",
        "NOT OK",
    ):
        assert token in text, f"{token!r} missing from --once text"
        assert token in html, f"{token!r} missing from HTML page"
    assert "0.5s old" in text and "0.5s old" in html  # the frame age
    assert "<meta http-equiv='refresh'" in html  # the page auto-refreshes


# --- AC2: rendering never writes; a write through the reader raises ------------


def _row_count(path: Path) -> int:
    with Log(path) as log:
        return len(log.since(EPOCH, ["frame", "blind", "verdict", "alarm_eval", "cycle", "obs"]))


def test_rendering_leaves_the_log_bytes_and_row_count_unchanged(tmp_path):
    log_path = _proving_log(tmp_path / "log.db")
    config = health_config()
    before_bytes = hashlib.sha256(log_path.read_bytes()).hexdigest()
    before_rows = _row_count(log_path)

    for _ in range(3):  # render repeatedly -- still read-only
        render_text(build_view(config, log_path, now=_t(4)))
        render_html(build_view(config, log_path, now=_t(4)))
        json.dumps(render_view_json(build_view(config, log_path, now=_t(4))))

    assert hashlib.sha256(log_path.read_bytes()).hexdigest() == before_bytes
    assert _row_count(log_path) == before_rows


def test_a_write_through_the_boards_reader_raises(tmp_path):
    log_path = _proving_log(tmp_path / "log.db")
    with LogReader(log_path) as reader:
        assert reader.newest("verdict").state == VerdictState.FAULT  # reads fine
        with pytest.raises(sqlite3.OperationalError):
            reader.connection.execute(
                "INSERT INTO records (record_id, kind, ts, run_id, body) "
                "VALUES ('x', 'frame', 't', 'r', '{}')"
            )
            reader.connection.commit()


# --- AC3: missing / empty / stale verdict / stale alarm each UNKNOWN or STALE --


def test_missing_log_renders_unknown_never_ok(tmp_path):
    view = build_view(health_config(), tmp_path / "never-created.db", now=_t(0))
    assert view.state == "UNKNOWN"
    assert view.ok is False
    assert "missing" in view.state_detail
    assert "UNKNOWN" in render_text(view) and "NOT OK" in render_text(view)


def test_empty_log_renders_unknown_no_verdict_never_ok(tmp_path):
    log_path = tmp_path / "log.db"
    with Log(log_path):  # schema created, no rows
        pass
    view = build_view(health_config(), log_path, now=_t(0))
    assert view.state == "UNKNOWN"
    assert view.ok is False
    assert "no verdict yet" in view.state_detail


def test_stale_verdict_renders_unknown_stale_never_ok(tmp_path):
    log_path = tmp_path / "log.db"
    with Log(log_path) as log:
        log.append(
            Verdict(
                station_id="station-1",
                ts=_t(0),
                state=VerdictState.HEALTHY,
                faults=(),
                blind_reasons=(),
                seq=1,
                run_id=RUN,
            )
        )
    # cycle_window_s is 10.0; the verdict is 100s old.
    view = build_view(health_config(), log_path, now=_t(100))
    assert view.state == "UNKNOWN"
    assert view.ok is False
    assert "STALE" in view.state_detail
    assert "NOT OK" in render_text(view)


def test_stale_alarm_evaluated_row_renders_stale_never_ok(tmp_path):
    log_path = tmp_path / "log.db"
    with Log(log_path) as log:
        log.append(AlarmEvaluated(ts=_t(0), seq=1, open_episodes=(), run_id=RUN))
        # A fresh healthy verdict and frame so only the alarm rail is stale.
        log.append(frame_at(_t(100), RUN, 0))
        log.append(
            Verdict(
                station_id="station-1",
                ts=_t(100),
                state=VerdictState.HEALTHY,
                faults=(),
                blind_reasons=(),
                seq=1,
                run_id=RUN,
            )
        )
    view = build_view(health_config(), log_path, now=_t(100.1))
    assert view.state == "HEALTHY"  # the verdict rail is fresh
    assert view.alarm_stale is True
    assert view.ok is False  # a stale alarm rail is never OK (K1)
    assert "STALE" in view.alarm_detail
    assert "NOT OK" in render_text(view)


# --- AC: an unknown fault kind / episode renders by name, never crashes, not OK -


def test_an_unknown_kind_renders_by_name_and_is_never_ok(tmp_path):
    log_path = tmp_path / "log.db"
    with Log(log_path) as log:
        log.append(frame_at(_t(0), RUN, 0))
        log.append(
            Verdict(
                station_id="station-1",
                ts=_t(0),
                state=VerdictState.FAULT,
                faults=(Fault(kind=FaultKind.CYCLE_TIME_CREEP, target="bench", frame_ids=(5,)),),
                blind_reasons=(),
                seq=1,
                run_id=RUN,
            )
        )
        log.append(
            AlarmEvaluated(ts=_t(0), seq=1, open_episodes=("some_future_kind:widget",), run_id=RUN)
        )
    view = build_view(health_config(), log_path, now=_t(0.1))
    text = render_text(view)
    assert "cycle_time_creep" in text  # HF2.5 slope fault, rendered by name
    assert "some_future_kind:widget" in text  # a cause string the Board never special-cases
    assert view.ok is False


# --- AC4: the HTTP server -- GET serves, non-GET is 405, binds loopback only ----


class _ServerThread:
    def __init__(self, config, log_path):
        self.server = make_server(config, log_path, port=0, clock=lambda: _t(3.5))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self.server

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def _request(url: str, method: str = "GET"):
    req = urllib.request.Request(url, method=method)
    return urllib.request.urlopen(req, timeout=5)


def test_http_server_binds_loopback_serves_get_and_rejects_non_get(tmp_path):
    log_path = _proving_log(tmp_path / "log.db")
    with _ServerThread(health_config(), log_path) as server:
        assert server.server_address[0] == "127.0.0.1"
        host, port = server.server_address[:2]
        base = f"http://{host}:{port}"

        with _request(f"{base}/") as page:
            assert page.status == 200
            body = page.read().decode()
        assert "station-1" in body and "FAULT" in body

        with _request(f"{base}/view.json") as view_resp:
            assert view_resp.status == 200
            payload = json.loads(view_resp.read().decode())
        assert payload["station_id"] == "station-1"
        assert payload["ok"] is False

        for method in ("POST", "PUT", "DELETE"):
            with pytest.raises(urllib.error.HTTPError) as caught:
                _request(f"{base}/", method=method)
            assert caught.value.code == 405, method


# --- AC5: the Board imports nothing from the alarm sinks or the runner ----------


def test_board_modules_import_nothing_from_alarm_or_runner():
    board_dir = Path(__file__).resolve().parent.parent / "src" / "station_watch" / "board"
    forbidden = ("station_watch.alarm", "station_watch.runner")
    offenders: list[str] = []
    for file in sorted(board_dir.rglob("*.py")):
        tree = ast.parse(file.read_text())
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
            elif isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            for name in names:
                if any(name == f or name.startswith(f + ".") for f in forbidden):
                    offenders.append(f"{file.name}: {name}")
    assert offenders == [], f"Board must not import alarm/runner: {offenders}"
