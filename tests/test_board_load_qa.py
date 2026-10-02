"""Board QA under load: a 200k-row Log, a real server, and never OK on stale.

Playwright is not a dev dependency, so this is a headless HTTP + ``html.parser``
suite against the *real* ``make_server``. A module-scoped fixture bulk-builds one
large Log (>= 200,000 rows across frames, observations, verdicts, blind episodes
and alarm evaluations) through the real :class:`~station_watch.log.Log` writer,
whose head rows describe a live FAULT station (a stall flag citing frame ids, an
open ``dark`` blind, open alarm episodes, a fresh frame). We then assert:

* the page and the ``/view.json`` both show the state, every flag with its cited
  frame ids, the frame age, the open blind reasons and the alarm episodes;
* render time p95 over 20 sequential requests, and over 4 concurrent clients,
  stays under a generous printed bound;
* a Log whose newest row is undecodable renders UNKNOWN in the page;
* if the ``playwright`` package is importable, a headless browser sees the same
  status text (skipped otherwise -- no dependency is added); and
* AC5 end-to-end: a real ``station-watch board`` subprocess serves the same Log
  and the same page/JSON assertions pass over HTTP.
"""

from __future__ import annotations

import http.client
import json
import re
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from station_watch.board import make_server
from station_watch.clock import offset_iso, utc_now_iso
from station_watch.config import StationConfig
from station_watch.log import Log
from station_watch.records import (
    AlarmEvaluated,
    BlindReason,
    BlindRecord,
    BlindState,
    Fault,
    FaultKind,
    FrameRecord,
    Observation,
    ObservationKind,
    Verdict,
    VerdictState,
)

sys.path.insert(0, str(Path(__file__).parent))
from helpers.board_page import parse_status  # noqa: E402
from helpers.records import HEALTH_CONFIG  # noqa: E402

RUN = "run-load"
HEAD_FID = 500_000
STALL_FRAMES = (HEAD_FID - 1, HEAD_FID)
EPISODES = ("stalled:zone_press", "unobservable:dark")
RENDER_BOUND_S = 2.0  # generous: a bounded, index-backed render over a 200k-row Log

# Counts across the five kinds; the sum is the "at least 200,000 rows" floor.
_FRAMES, _OBS, _VERDICTS, _BLINDS, _ALARMS = 130_000, 50_000, 15_000, 3_000, 2_000

# Generous freshness windows so a subprocess reading the Log off real wall-clock
# time seconds later still sees the head rows as fresh (the head is a live FAULT).
CONFIG = {
    **HEALTH_CONFIG,
    "liveness_window_s": 3600.0,
    "watchdog": {"cycle_window_s": 3600.0, "alarm_eval_window_s": 3600.0, "sinks": ["record"]},
}


@dataclass(frozen=True)
class BigLog:
    log_path: Path
    config_path: Path
    config: StationConfig
    now: str
    rows: int


def _bulk_build(log: Log, ts: dict[str, str]) -> None:
    for i in range(_FRAMES):
        log.append(
            FrameRecord("station-1", "cam-0", i, ts["frame"], float(i), f"fp-{i}", 100.0, 1.0, RUN)
        )
    for i in range(_OBS):
        log.append(
            Observation(
                "station-1", i, ts["obs"], ObservationKind.NO_MOTION, "bench", "m", 0.9, {}, RUN
            )
        )
    for i in range(_VERDICTS):
        log.append(Verdict("station-1", ts["verdict"], VerdictState.HEALTHY, (), (), i + 1, RUN))
    for i in range(_BLINDS):
        log.append(
            BlindRecord(
                "station-1",
                "cam-0",
                ts["blind"],
                BlindReason.FROZEN,
                {},
                None,
                BlindState.CLEARED,
                i + 1,
                RUN,
            )
        )
    for i in range(_ALARMS):
        log.append(AlarmEvaluated(ts=ts["alarm"], seq=i + 1, open_episodes=(), run_id=RUN))


def _append_head(log: Log, now: str) -> None:
    """The newest row of every kind: a live FAULT station the Board must surface."""
    log.append(
        FrameRecord(
            "station-1", "cam-0", HEAD_FID, now, float(HEAD_FID), "fp-head", 100.0, 1.0, RUN
        )
    )
    log.append(
        Verdict(
            "station-1",
            now,
            VerdictState.FAULT,
            (Fault(FaultKind.STALLED, "zone_press", STALL_FRAMES),),
            (),
            900_000,
            RUN,
        )
    )
    log.append(
        BlindRecord(
            "station-1", "cam-0", now, BlindReason.DARK, {}, None, BlindState.OPENED, 900_001, RUN
        )
    )
    log.append(AlarmEvaluated(ts=now, seq=900_002, open_episodes=EPISODES, run_id=RUN))


@pytest.fixture(scope="module")
def big_log(tmp_path_factory) -> BigLog:
    base = tmp_path_factory.mktemp("board-load")
    log_path = base / "log.db"
    now = utc_now_iso()
    ts = {
        kind: offset_iso(now, -off)
        for kind, off in (("frame", 30), ("obs", 29), ("verdict", 28), ("blind", 27), ("alarm", 26))
    }
    with Log(log_path) as log:
        _bulk_build(log, ts)
        _append_head(log, now)
    config_path = base / "station.yaml"
    config_path.write_text(yaml.safe_dump(CONFIG))
    total = _FRAMES + _OBS + _VERDICTS + _BLINDS + _ALARMS + 5
    return BigLog(log_path, config_path, StationConfig.from_mapping(CONFIG), now, total)


# --- a server fixture over the big Log, with the fixed build-time clock ---------


class _Served:
    def __init__(self, config, log_path: Path, now: str):
        self.server = make_server(config, log_path, port=0, clock=lambda: now)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> int:
        self.thread.start()
        return self.server.server_address[1]

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def _get(port: int, path: str) -> tuple[int, str]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, resp.read().decode()
    finally:
        conn.close()


def _assert_full_page(html: str) -> None:
    status, h1 = parse_status(html)
    assert status == "NOT OK", h1
    assert "FAULT" in h1
    for token in ("stalled", "zone_press", str(HEAD_FID), "dark", *EPISODES):
        assert token in html, f"{token!r} missing from the page"
    assert re.search(r"\d+\.\d+s old", html), "the page shows no frame age"


def _assert_full_json(payload: dict) -> None:
    assert payload["station_id"] == "station-1"
    assert payload["ok"] is False
    assert payload["state"] == "FAULT"
    assert payload["frame_age_s"] is not None
    assert "dark" in payload["blind_reasons"]
    assert set(payload["alarm_open_episodes"]) == set(EPISODES)
    flag = payload["flags"][0]
    assert flag["kind"] == "stalled" and flag["target"] == "zone_press"
    assert flag["frame_ids"] == list(STALL_FRAMES)


# --- AC1: proving test over the 200k Log -- page, JSON, and the render bound -----


def test_proving_page_and_json_show_everything(big_log):
    assert big_log.rows >= 200_000, big_log.rows
    with _Served(big_log.config, big_log.log_path, big_log.now) as port:
        status, html = _get(port, "/")
        assert status == 200
        _assert_full_page(html)
        status, body = _get(port, "/view.json")
        assert status == 200
        _assert_full_json(json.loads(body))


def _p95(samples: list[float]) -> float:
    return statistics.quantiles(samples, n=20)[-1] if len(samples) > 1 else samples[0]


def test_render_time_p95_sequential_and_concurrent_under_bound(big_log):
    with _Served(big_log.config, big_log.log_path, big_log.now) as port:
        seq = []
        for _ in range(20):
            start = time.monotonic()
            assert _get(port, "/")[0] == 200
            seq.append(time.monotonic() - start)

        def one_request(_i):
            start = time.monotonic()
            assert _get(port, "/")[0] == 200
            return time.monotonic() - start

        with ThreadPoolExecutor(max_workers=4) as pool:
            conc = list(pool.map(one_request, range(20)))

    seq_p95, conc_p95 = _p95(seq), _p95(conc)
    print(
        f"\n[HF3.14] 200k-row Log render p95: sequential={seq_p95 * 1000:.1f}ms "
        f"4-way concurrent={conc_p95 * 1000:.1f}ms (bound {RENDER_BOUND_S * 1000:.0f}ms)"
    )
    assert seq_p95 < RENDER_BOUND_S, f"sequential p95 {seq_p95:.3f}s over bound"
    assert conc_p95 < RENDER_BOUND_S, f"concurrent p95 {conc_p95:.3f}s over bound"


# --- AC3: an undecodable newest row shows UNKNOWN in the page -------------------


def test_undecodable_newest_row_shows_unknown_in_the_page(tmp_path):
    log_path = tmp_path / "log.db"
    with Log(log_path) as log:
        log.append(FrameRecord("station-1", "cam-0", 0, _old(), 0.0, "fp", 100.0, 1.0, RUN))
        log.append(Verdict("station-1", _old(), VerdictState.HEALTHY, (), (), 1, RUN))
    bad_id = f"{RUN}:verdict:station-1:999"
    conn = sqlite3.connect(str(log_path))
    try:
        newest_ts = conn.execute("SELECT MAX(ts) FROM records").fetchone()[0]
        conn.execute(
            "INSERT INTO records (record_id, kind, ts, run_id, body) VALUES (?, ?, ?, ?, ?)",
            (bad_id, "verdict", newest_ts + "9", RUN, '{"record_id": "' + bad_id + '"}'),
        )
        conn.commit()
    finally:
        conn.close()
    with _Served(StationConfig.from_mapping(CONFIG), log_path, utc_now_iso()) as port:
        status, html = _get(port, "/")
    assert status == 200
    page_status, h1 = parse_status(html)
    assert "UNKNOWN" in h1
    assert page_status == "NOT OK"
    assert bad_id in html  # the undecodable record is named, not swallowed


def _old() -> str:
    return offset_iso(utc_now_iso(), -5.0)


# --- AC4: an optional headless-browser check, skipped when playwright is absent --


def test_optional_playwright_sees_the_same_status(big_log):
    playwright = pytest.importorskip("playwright.sync_api")
    with _Served(big_log.config, big_log.log_path, big_log.now) as port:
        try:
            with playwright.sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
        except Exception as exc:  # noqa: BLE001 -- browser binaries may be absent
            pytest.skip(f"playwright present but no browser available: {exc}")
        try:
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/")
            body = page.content()
        finally:
            browser.close()
    status, h1 = parse_status(body)
    assert status == "NOT OK" and "FAULT" in h1


# --- AC5: a real station-watch board subprocess serves the same Log over HTTP ----


def _spawn_board(big_log: BigLog) -> tuple[subprocess.Popen, int]:
    proc = subprocess.Popen(
        # -u: unbuffered, so the child flushes its "serving on ..." line to the
        # pipe before entering serve_forever (block buffering would deadlock us).
        [
            sys.executable,
            "-u",
            "-m",
            "station_watch",
            "board",
            "--config",
            str(big_log.config_path),
            "--log",
            str(big_log.log_path),
            "--port",
            "0",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        match = re.search(r"http://127\.0\.0\.1:(\d+)/", line)
        if match:
            return proc, int(match.group(1))
    proc.kill()
    raise AssertionError(f"board did not announce a port; stderr:\n{proc.stderr.read()}")


def test_e2e_real_board_subprocess_serves_the_200k_log(big_log):
    proc, port = _spawn_board(big_log)
    try:
        status, html = _get(port, "/")
        assert status == 200
        _assert_full_page(html)
        status, body = _get(port, "/view.json")
        assert status == 200
        _assert_full_json(json.loads(body))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
