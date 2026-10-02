"""`station-watch soak`: sampler, growth verdict and the child supervisor.

The soak starts three real children against one Log (``run``, ``watchdog``,
``board``), samples them, and reduces the samples against the required ``soak:``
thresholds plus HF3.3 session QA. These tests prove the reduction (RSS slope, Log
growth, Board render, verdict cadence, false flags), the sampler against a real child
process and a temp Log, the K9 config failures, the ``--out`` confinement, and -- end
to end -- a real soak subprocess that starts all three children, samples, stops every
child, and writes a passing measurement.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from station_watch.clock import offset_iso
from station_watch.log import Log
from station_watch.records import CycleCompleted, Verdict, VerdictState
from station_watch.runner.startup import StartupError
from station_watch.soak.sampler import Sample, take_sample
from station_watch.soak.supervisor import scan_children
from station_watch.soak.verdict import load_soak_thresholds, soak_verdict

sys.path.insert(0, str(Path(__file__).parent))
from helpers.records import HEALTH_CONFIG  # noqa: E402
from helpers.synth_video import write_synth_clip  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BASE = "2026-10-01T00:00:00.000000+00:00"

THRESHOLDS = {
    "max_rss_growth_mb_per_h": 10.0,
    "max_log_mb_per_h": 100.0,
    "max_board_render_s": 1.0,
    "max_verdict_gap_s": 30.0,
    "max_false_flags": 0,
    "warmup_s": 0.0,
}


# --------------------------------------------------------------------------- #
# Sample builders
# --------------------------------------------------------------------------- #
def make_sample(
    elapsed: float,
    *,
    rss: dict | None = None,
    log_bytes: int = 1000,
    board_render_s: float | None = 0.01,
    board_error: str | None = None,
    newest_ts: str = "advance",
) -> Sample:
    rss = rss if rss is not None else {"run": 50000, "watchdog": 30000, "board": 40000}
    verdicts = int(elapsed)
    ts = offset_iso(BASE, elapsed) if newest_ts == "advance" else newest_ts
    return Sample(
        elapsed_s=float(elapsed),
        rss_kb=rss,
        log_bytes=log_bytes,
        wal_bytes=0,
        rows={"verdict": verdicts},
        board_render_s=board_render_s,
        board_error=board_error,
        verdicts=verdicts,
        newest_verdict_ts=ts,
    )


def _rising_rss(name: str, mb_per_h: float, base_kb: int = 50000) -> list[Sample]:
    kb_per_s = mb_per_h * 1024.0 / 3600.0
    out = []
    for elapsed in (0, 10, 20, 30):
        rss = {"run": base_kb, "watchdog": 30000, "board": 40000}
        rss[name] = int(round(base_kb + kb_per_s * elapsed))
        out.append(make_sample(elapsed, rss=rss))
    return out


# --------------------------------------------------------------------------- #
# AC1: RSS rising 50 MB/h fails a 10 MB/h threshold naming process + both numbers
# --------------------------------------------------------------------------- #
def test_rss_rising_50_fails_10_naming_process_and_numbers():
    result = soak_verdict(_rising_rss("run", 50.0), THRESHOLDS, false_flags=0)
    assert result["status"] == "fail"
    message = "; ".join(result["failures"])
    assert "run" in message and "50.0" in message and "10.0" in message
    assert abs(result["metrics"]["rss_growth_mb_per_h"]["run"] - 50.0) < 0.5


def test_flat_rss_series_passes():
    flat = [make_sample(e) for e in (0, 10, 20, 30)]
    result = soak_verdict(flat, THRESHOLDS, false_flags=0)
    assert result["status"] == "pass", result["failures"]


# --------------------------------------------------------------------------- #
# AC2: verdict gap, a failed Board render, and false flags each fail, named
# --------------------------------------------------------------------------- #
def test_verdict_gap_above_max_fails_named():
    stalled = [make_sample(e, newest_ts="t0") for e in (0, 10, 20, 40)]
    result = soak_verdict(stalled, THRESHOLDS, false_flags=0)
    assert result["status"] == "fail"
    assert any("verdict gap" in f for f in result["failures"])
    assert result["metrics"]["max_verdict_gap_s"] == 40.0


def test_failed_board_render_fails_named():
    samples = [
        make_sample(0),
        make_sample(10, board_render_s=None, board_error="ConnectionRefusedError"),
        make_sample(20),
    ]
    result = soak_verdict(samples, THRESHOLDS, false_flags=0)
    assert result["status"] == "fail"
    assert any("Board render failed" in f for f in result["failures"])


def test_false_flags_above_limit_fails_named():
    clean = [make_sample(e) for e in (0, 10, 20)]
    result = soak_verdict(clean, THRESHOLDS, false_flags=1)
    assert result["status"] == "fail"
    assert any("false flags" in f for f in result["failures"])


# --------------------------------------------------------------------------- #
# AC4: fewer than three post-warmup samples -> insufficient_samples
# --------------------------------------------------------------------------- #
def test_fewer_than_three_post_warmup_is_insufficient():
    two = [make_sample(0), make_sample(10)]
    result = soak_verdict(two, THRESHOLDS, false_flags=0)
    assert result["status"] == "insufficient_samples"


def test_warmup_excludes_early_samples_then_insufficient():
    # four samples, but three fall inside a 100 s warmup -> one post-warmup -> insufficient
    thresholds = {**THRESHOLDS, "warmup_s": 100.0}
    samples = [make_sample(e) for e in (0, 10, 20, 120)]
    result = soak_verdict(samples, thresholds, false_flags=0)
    assert result["status"] == "insufficient_samples"


# --------------------------------------------------------------------------- #
# AC4: a missing soak.* key raises StartupError naming the dotted key (K9)
# --------------------------------------------------------------------------- #
def test_missing_soak_board_key_raises_naming_it():
    config = {"soak": {k: 1.0 for k in THRESHOLDS if k != "max_board_render_s"}}
    with pytest.raises(StartupError) as exc:
        load_soak_thresholds(config)
    assert "soak.max_board_render_s" in str(exc.value)


def test_missing_soak_section_raises():
    with pytest.raises(StartupError) as exc:
        load_soak_thresholds({})
    assert "soak" in str(exc.value)


# --------------------------------------------------------------------------- #
# AC5: a child that exited early is named with its exit code
# --------------------------------------------------------------------------- #
class _FakeChild:
    def __init__(self, code):
        self._code = code

    def poll(self):
        return self._code


def test_scan_children_names_dead_watchdog_and_code():
    children = {"run": _FakeChild(None), "watchdog": _FakeChild(-9), "board": _FakeChild(None)}
    failure = scan_children(children)
    assert failure is not None
    assert "watchdog" in failure and "-9" in failure


def test_scan_children_all_alive_is_none():
    assert scan_children({"run": _FakeChild(None), "board": _FakeChild(None)}) is None


# --------------------------------------------------------------------------- #
# AC5: a deliberately tiny max_rss_growth_mb_per_h fails naming the process
# --------------------------------------------------------------------------- #
def test_tiny_rss_threshold_fails_naming_process():
    thresholds = {**THRESHOLDS, "max_rss_growth_mb_per_h": 0.001}
    result = soak_verdict(_rising_rss("board", 5.0), thresholds, false_flags=0)
    assert result["status"] == "fail"
    assert any("board" in f and "RSS growth" in f for f in result["failures"])


# --------------------------------------------------------------------------- #
# AC3: take_sample on a real child + temp Log; dead pid recorded as dead
# --------------------------------------------------------------------------- #
def _build_log(path):
    run_id = "run-soak-test"
    with Log(path) as log:
        for seq in range(2):
            log.append(
                Verdict(
                    station_id=HEALTH_CONFIG["station_id"],
                    ts=offset_iso(BASE, seq),
                    state=VerdictState.HEALTHY,
                    faults=(),
                    blind_reasons=(),
                    seq=seq,
                    run_id=run_id,
                )
            )
        log.append(CycleCompleted(ts=offset_iso(BASE, 2), cycle=0, stages=(), run_id=run_id))


def test_take_sample_real_child_positive_rss_and_row_counts(tmp_path):
    log = tmp_path / "station.db"
    _build_log(log)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        sample = take_sample(
            elapsed_s=1.0, pids={"child": child.pid}, log_path=log, board_port=None
        )
        assert sample.rss_kb["child"] is not None and sample.rss_kb["child"] > 0
        assert sample.rows.get("verdict") == 2
        assert sample.rows.get("cycle") == 1
        assert sample.verdicts == 2
        assert sample.newest_verdict_ts == offset_iso(BASE, 1)
        assert sample.log_bytes > 0
    finally:
        child.terminate()
        child.wait(timeout=10)

    dead = take_sample(elapsed_s=2.0, pids={"child": child.pid}, log_path=log, board_port=None)
    assert dead.rss_kb["child"] is None


# --------------------------------------------------------------------------- #
# CLI helpers
# --------------------------------------------------------------------------- #
def _entry_point() -> list[str]:
    script = Path(sys.executable).parent / "station-watch"
    return [str(script)] if script.exists() else [sys.executable, "-m", "station_watch"]


def _soak_config(tmp_path, *, soak=None, qa=None) -> Path:
    data = yaml.safe_load((ROOT / "config" / "station-example.yaml").read_text())
    data["soak"] = soak or {
        "max_rss_growth_mb_per_h": 5000.0,
        "max_log_mb_per_h": 100000.0,
        "max_board_render_s": 5.0,
        "max_verdict_gap_s": 30.0,
        "max_false_flags": 0,
        "warmup_s": 8.0,
    }
    data["qa"] = qa or {"max_unknown_fraction": 0.95}
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "config" / "station.yaml"
    cfg.write_text(yaml.safe_dump(data))
    return cfg


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# --------------------------------------------------------------------------- #
# AC6: --out outside the confined dir exits non-zero naming the rule
# --------------------------------------------------------------------------- #
def test_out_outside_confined_dir_exits_nonzero_naming_rule(tmp_path):
    log = tmp_path / "station.db"
    _build_log(log)
    cfg = _soak_config(tmp_path)
    result = subprocess.run(
        [
            *_entry_point(),
            "soak",
            "--config",
            str(cfg),
            "--log",
            str(log),
            "--out",
            "outside.json",
            "--dataset-kind",
            "synthetic",
            "--source",
            str(log),
            "--minutes",
            "0.1",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode != 0, result.stdout
    assert "must be written inside" in result.stderr


# --------------------------------------------------------------------------- #
# AC8: E2E conformance -- a real soak subprocess over a clean clip
# --------------------------------------------------------------------------- #
def test_e2e_real_soak_starts_children_samples_stops_and_passes(tmp_path):
    (tmp_path / "measurements" / "synthetic").mkdir(parents=True)
    cfg = _soak_config(tmp_path)
    # A clip longer than the 30 s soak so the run child never exits early (native fps).
    clip = write_synth_clip(tmp_path / "clip", frames=185, fps=5.0)
    port = _free_port()
    out_rel = "measurements/synthetic/soak.json"

    result = subprocess.run(
        [
            *_entry_point(),
            "soak",
            *("--config", str(cfg), "--log", str(tmp_path / "soak.db")),
            *("--out", out_rel, "--dataset-kind", "synthetic", "--source", str(clip)),
            *("--minutes", "0.5", "--sample-s", "3", "--board-port", str(port)),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    payload = json.loads((tmp_path / out_rel).read_text())
    metrics = payload["metrics"]
    assert metrics["status"] == "pass", metrics["failures"]
    assert metrics["post_warmup_samples"] >= 3
    for name in ("run", "watchdog", "board"):
        assert name in metrics["rss_growth_mb_per_h"]
    assert "log_growth_mb_per_h" in metrics
    assert "board_render_p95_s" in metrics
    assert "max_verdict_gap_s" in metrics
    assert metrics["false_flags"] == 0
    # The markdown table is written beside the JSON with the reproduce command.
    table = (tmp_path / "measurements" / "synthetic" / "soak.md").read_text()
    assert "Reproduce:" in table and "station-watch soak" in table

    # No orphan board child: its port is free again after the soak returned.
    with socket.socket() as sock:
        sock.settimeout(2.0)
        with pytest.raises((ConnectionRefusedError, OSError)):
            sock.connect(("127.0.0.1", port))
            sock.sendall(b"GET /view.json HTTP/1.0\r\n\r\n")
            if not sock.recv(1):
                raise ConnectionRefusedError
