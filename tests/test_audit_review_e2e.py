"""`audit review`/`audit score` E2E: run -> build -> review (HTTP mark) -> score.

Real subprocesses: a ``station-watch run`` over a synthetic clip with a missing part,
``audit build`` of its Log, an ``audit review`` server marked by an HTTP POST carrying
the page's cookie token, and ``audit score`` -- and the score file's ``correct`` count
matches the mark. The Log the run wrote is unchanged after review and score.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from helpers.synth_station import write_synth_station_clip  # noqa: E402

RAIL = {
    "rail_pos_1": [[0.7, 1.9], [1.7, 1.9], [1.7, 2.5], [0.7, 2.5]],
    "rail_pos_2": [[2.5, 1.9], [3.5, 1.9], [3.5, 2.5], [2.5, 2.5]],
}
STATION_ZONE = {"id": "bench", "region": [[0.3, 1.5], [5.5, 1.5], [5.5, 3.5], [0.3, 3.5]]}

BASE = {
    "station_id": "station-1",
    "camera_id": "cam-0",
    "takt_s": 30.0,
    "grace_s": 5.0,
    "required_slots": list(RAIL),
    "keepout_zones": [],
    "liveness_window_s": 2.0,
    "dark_luma_threshold": 40.0,
    "dark_window_s": 0.2,
    "frozen_frames": 10_000,
    "recover_good_frames": 3,
    "cycle_interval_s": 0.05,
    "recover_healthy_verdicts": 2,
    "fiducial": {
        "dictionary_id": "DICT_4X4_50",
        "marker_id": 0,
        "expected_center_px": [58, 58],
        "tolerance_px": 10,
        "window_s": 10_000.0,
    },
    "alarm": {"sinks": ["record"]},
    "watchdog": {"cycle_window_s": 1.0, "alarm_eval_window_s": 1.0, "sinks": ["record"]},
    "qa": {"max_unknown_fraction": 0.9},
    "detect": {
        "persistence_frames": 2,
        "emit_interval_s": 5.0,
        "rail_positions": RAIL,
        "station_zone": STATION_ZONE,
        "keepout_rois": {},
        "blur_threshold": 100.0,
        "darkness_threshold": 40.0,
        "occlusion_threshold": 0.5,
        "unknown_grace_s": 10.0,
    },
}


def _run_cli(*args, cwd=None, timeout=180):
    return subprocess.run(
        [sys.executable, "-m", "station_watch", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _clip(dir_path):
    bright = [{"positions": {"rail_pos_1": "absent", "rail_pos_2": "present"}} for _ in range(40)]
    dark = [
        {"positions": {"rail_pos_1": "present", "rail_pos_2": "present"}, "dim": True}
        for _ in range(30)
    ]
    clip, _truth = write_synth_station_clip(dir_path, bright + dark, rail_positions=RAIL, fps=20.0)
    return clip


def _request(port, *, method="GET", path="/", cookie=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.putrequest(method, path, skip_host=True)
        conn.putheader("Host", f"127.0.0.1:{port}")
        if cookie is not None:
            conn.putheader("Cookie", cookie)
        if body is not None:
            conn.putheader("Content-Type", "application/json")
        raw = body.encode() if body is not None else b""
        conn.putheader("Content-Length", str(len(raw)))
        conn.endheaders()
        if raw:
            conn.send(raw)
        resp = conn.getresponse()
        return resp.status, dict(resp.getheaders()), resp.read()
    finally:
        conn.close()


def _start_review(audit):
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "station_watch",
            "audit",
            "review",
            "--audit",
            str(audit),
            "--port",
            "0",
            "--reviewer",
            "e2e",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    port = int(re.search(r"http://127\.0\.0\.1:(\d+)/", proc.stdout.readline()).group(1))
    token = re.search(r"/\?token=(\S+)", proc.stderr.readline()).group(1)
    return proc, port, token


def _run_then_build(tmp_path):
    """A real ``run`` on the clip, then ``audit build``: (cfg, Log, audit dir, the flag)."""
    cfg = tmp_path / "station.yaml"
    cfg.write_text(yaml.safe_dump(BASE))
    clip = _clip(tmp_path / "clip")
    logdb, evidence, audit = tmp_path / "log.db", tmp_path / "evidence", tmp_path / "audit"
    run_args = ["--config", str(cfg), "--source", str(clip), "--log", str(logdb)]
    run_args += ["--alarm-record", str(tmp_path / "alarm.jsonl"), "--evidence-dir", str(evidence)]
    run = _run_cli("run", *run_args)
    assert run.returncode == 0, run.stderr
    build_args = ["--config", str(cfg), "--log", str(logdb), "--evidence-dir", str(evidence)]
    build = _run_cli("audit", "build", *build_args, "--out", str(audit))
    assert build.returncode == 0, build.stderr
    flags = json.loads((audit / "flags.json").read_text())
    missing = next(f for f in flags if f["kind"] == "missing_part")
    return cfg, logdb, audit, missing


def test_run_build_review_mark_then_score_correct_count_matches(tmp_path):
    cfg, logdb, audit, missing = _run_then_build(tmp_path)

    log_before = hashlib.sha256(logdb.read_bytes()).hexdigest()

    # Mark the missing_part flag correct over HTTP, using the page's cookie token.
    proc, port, token = _start_review(audit)
    try:
        # Sign in through the printed URL, as the operator's browser would.
        _, headers, body = _request(port, path=f"/?token={token}")
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        status, _, resp = _request(
            port,
            method="POST",
            path="/verdict",
            cookie=cookie,
            body=json.dumps({"flag_id": missing["flag_id"], "verdict": "correct"}),
        )
        assert status == 200, resp
        assert json.loads(resp)["appended"] is True
    finally:
        proc.terminate()
        proc.wait(timeout=10)

    (tmp_path / "measurements" / "synthetic").mkdir(parents=True)
    score = _run_cli(
        "audit",
        "score",
        "--audit",
        str(audit),
        "--config",
        str(cfg),
        "--log",
        str(logdb),
        "--dataset-kind",
        "synthetic",
        "--out",
        "measurements/synthetic/score.json",
        cwd=tmp_path,
    )
    assert score.returncode == 0, score.stderr

    payload = json.loads((tmp_path / "measurements/synthetic/score.json").read_text())
    assert payload["metrics"]["overall"]["correct"] == 1, payload
    assert payload["metrics"]["by_kind"]["missing_part"]["correct"] == 1

    # The Log the run wrote is untouched by review and score.
    assert hashlib.sha256(logdb.read_bytes()).hexdigest() == log_before
