"""`station-watch preflight`: one PASS/FAIL/WARN/SKIP line per check before going live.

The checks run in a fixed order (``CHECKS`` / ``EXPECTED_CHECKS``), each under a
timeout and never raising (K10: an exception or a timeout is a FAIL that names the
reason). These tests prove the happy path end to end as a real subprocess (marker in
place -> every runnable check PASS, model_weights/board SKIP, exit 0), the fault paths
(marker hidden -> fiducial FAIL; a source that cannot open -> camera FAIL and the
camera-dependent checks FAIL, never PASS; a frozen clip -> frames_live FAIL; an
unwritable Log dir -> log_writable FAIL; a raising/slow check -> FAIL), the real-Board
counterparty (the `board` check answers a live `station-watch board` child and FAILs
when nothing listens), that ``--json`` matches the text run and the Log is untouched,
and that the command is wired into the CLI (orphan test) and its module is reachable.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import yaml

from station_watch.clock import offset_iso
from station_watch.log import Log
from station_watch.preflight import CHECKS, CheckResult, run_preflight
from station_watch.preflight.result import FAIL, PASS, SKIP
from station_watch.preflight.runner import render_json, render_text

sys.path.insert(0, str(Path(__file__).parent))
from helpers.records import HEALTH_CONFIG  # noqa: E402
from helpers.synth_video import write_synth_clip  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PAST = "2026-09-01T00:00:00.000000+00:00"

# The one list of checks, in order: a task that adds a check updates only this.
EXPECTED_CHECKS = [
    "config",
    "camera",
    "frames_live",
    "fiducial",
    "camera_stability",
    "model_weights",
    "log_writable",
    "disk_space",
    "clock",
    "alarm_sinks",
    "board",
]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _entry_point() -> list[str]:
    script = Path(sys.executable).parent / "station-watch"
    return [str(script)] if script.exists() else [sys.executable, "-m", "station_watch"]


def _config(tmp_path: Path, **over) -> Path:
    """A camera-health config (screen+sound sinks, marker at (40, 40), no zones)."""
    data = yaml.safe_load((ROOT / "config" / "station-example.yaml").read_text())
    data.update(over)
    cfg = tmp_path / "station.yaml"
    cfg.write_text(yaml.safe_dump(data))
    return cfg


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _by_name(results: list[CheckResult]) -> dict[str, CheckResult]:
    return {r.name: r for r in results}


# --------------------------------------------------------------------------- #
# The ordered registry is the single source of truth
# --------------------------------------------------------------------------- #
def test_checks_registry_is_the_expected_ordered_list():
    assert list(CHECKS) == EXPECTED_CHECKS


# --------------------------------------------------------------------------- #
# AC1: E2E conformance -- a clean clip passes every runnable check, exits 0
# --------------------------------------------------------------------------- #
def test_e2e_clean_clip_passes_and_exits_zero(tmp_path):
    cfg = _config(tmp_path)
    clip = write_synth_clip(tmp_path / "clip", frames=30)
    result = subprocess.run(
        [
            *_entry_point(),
            "preflight",
            "--config",
            str(cfg),
            "--source",
            str(clip),
            "--log",
            str(tmp_path / "station.db"),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.strip().splitlines()
    # One aligned line per check, in order, then the verdict.
    assert [line.split()[0] for line in lines[:-1]] == EXPECTED_CHECKS
    assert lines[-1] == "PREFLIGHT PASS"
    for name in (
        "config",
        "camera",
        "frames_live",
        "fiducial",
        "camera_stability",
        "log_writable",
        "disk_space",
        "clock",
        "alarm_sinks",
    ):
        assert f"{name} " in result.stdout
        line = next(line for line in lines if line.startswith(name))
        assert "PASS" in line, line
    assert "SKIP" in next(line for line in lines if line.startswith("model_weights"))
    assert "SKIP" in next(line for line in lines if line.startswith("board"))


# --------------------------------------------------------------------------- #
# AC2: marker hidden -> fiducial FAIL and exit 1
# --------------------------------------------------------------------------- #
def test_hidden_marker_fails_fiducial_and_exits_one(tmp_path):
    cfg = _config(tmp_path)
    clip = write_synth_clip(tmp_path / "clip", frames=30, hide_marker_from=0)
    result = subprocess.run(
        [
            *_entry_point(),
            "preflight",
            "--config",
            str(cfg),
            "--source",
            str(clip),
            "--log",
            str(tmp_path / "station.db"),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 1, result.stdout
    fiducial = next(line for line in result.stdout.splitlines() if line.startswith("fiducial"))
    assert "FAIL" in fiducial
    assert "PREFLIGHT FAIL" in result.stdout


# --------------------------------------------------------------------------- #
# AC2: a source that cannot open -> camera FAIL naming it; dependents never PASS
# --------------------------------------------------------------------------- #
def test_unopenable_source_fails_camera_and_dependents_never_pass(tmp_path):
    cfg = _config(tmp_path)
    missing = tmp_path / "no-such-clip.mkv"
    results = _by_name(run_preflight(str(cfg), str(missing), str(tmp_path / "station.db")))
    assert results["camera"].status == FAIL
    assert str(missing) in results["camera"].detail
    for dependent in ("frames_live", "fiducial"):
        assert results[dependent].status != PASS
        assert "camera" in results[dependent].detail.lower()
    # model_weights is not camera-dependent: no zones -> SKIP, never PASS/FAIL here.
    assert results["model_weights"].status == SKIP


# --------------------------------------------------------------------------- #
# AC3: a frozen clip (identical frames) -> frames_live FAIL
# --------------------------------------------------------------------------- #
def test_frozen_clip_fails_frames_live(tmp_path):
    cfg = _config(tmp_path)
    clip = write_synth_clip(tmp_path / "clip", frames=30, freeze_from=0)
    results = _by_name(run_preflight(str(cfg), str(clip), str(tmp_path / "station.db")))
    assert results["camera"].status == PASS
    assert results["frames_live"].status == FAIL


# --------------------------------------------------------------------------- #
# AC4: an unwritable Log directory -> log_writable FAIL
# --------------------------------------------------------------------------- #
def test_unwritable_log_dir_fails_log_writable(tmp_path):
    cfg = _config(tmp_path)
    clip = write_synth_clip(tmp_path / "clip", frames=30)
    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, 0o500)
    try:
        results = _by_name(run_preflight(str(cfg), str(clip), str(locked / "station.db")))
        assert results["log_writable"].status == FAIL
    finally:
        os.chmod(locked, 0o700)


def test_missing_log_dir_fails_log_writable(tmp_path):
    cfg = _config(tmp_path)
    clip = write_synth_clip(tmp_path / "clip", frames=30)
    results = _by_name(run_preflight(str(cfg), str(clip), str(tmp_path / "gone" / "station.db")))
    assert results["log_writable"].status == FAIL
    assert "directory" in results["log_writable"].detail.lower()


# --------------------------------------------------------------------------- #
# AC4: a check that raises or exceeds its timeout -> FAIL with the reason (K10)
# --------------------------------------------------------------------------- #
def test_a_raising_check_is_a_fail_not_a_crash(tmp_path):
    def boom(_ctx):
        raise RuntimeError("kaboom")

    cfg = _config(tmp_path)
    results = run_preflight(str(cfg), "0", str(tmp_path / "station.db"), checks={"boom": boom})
    assert results[0].status == FAIL
    assert "kaboom" in results[0].detail


def test_a_slow_check_times_out_as_fail(tmp_path, monkeypatch):
    import station_watch.preflight.runner as runner

    monkeypatch.setattr(runner, "_CHECK_TIMEOUT_S", 0.2)

    def slow(_ctx):
        time.sleep(5.0)
        return CheckResult("slow", PASS, "never")

    cfg = _config(tmp_path)
    results = run_preflight(str(cfg), "0", str(tmp_path / "station.db"), checks={"slow": slow})
    assert results[0].status == FAIL
    assert "timed out" in results[0].detail


# --------------------------------------------------------------------------- #
# AC5 (counterparty): the real Board answers the `board` check
# --------------------------------------------------------------------------- #
def test_board_check_answers_a_real_board_subprocess(tmp_path):
    cfg = _config(tmp_path)
    log = tmp_path / "station.db"
    with Log(log):  # a real, openable Log for the Board child to read
        pass
    port = _free_port()
    board = subprocess.Popen(
        [*_entry_point(), "board", "--config", str(cfg), "--log", str(log), "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_port(port)
        results = _by_name(run_preflight(str(cfg), "0", str(log), board_port=port))
        assert results["board"].status == PASS, results["board"].detail
    finally:
        board.terminate()
        board.wait(timeout=10)

    # Nothing listening now -> board FAIL.
    results = _by_name(run_preflight(str(cfg), "0", str(log), board_port=port))
    assert results["board"].status == FAIL


def _wait_for_port(port: int, timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.5)
            try:
                sock.connect(("127.0.0.1", port))
                return
            except OSError:
                time.sleep(0.1)
    raise AssertionError(f"board did not come up on port {port}")


# --------------------------------------------------------------------------- #
# AC6: --json parses, matches the text run, and the Log's bytes are unchanged
# --------------------------------------------------------------------------- #
def test_json_output_matches_text_results(tmp_path):
    cfg = _config(tmp_path)
    clip = write_synth_clip(tmp_path / "clip", frames=30)
    results = run_preflight(str(cfg), str(clip), str(tmp_path / "station.db"))
    payload = json.loads(render_json(results))
    assert [c["name"] for c in payload["checks"]] == [r.name for r in results]
    assert [c["status"] for c in payload["checks"]] == [r.status for r in results]
    assert payload["ok"] is True
    text = render_text(results)
    for check in payload["checks"]:
        assert any(
            line.startswith(check["name"]) and check["status"] in line for line in text.splitlines()
        )


def test_preflight_leaves_the_log_bytes_unchanged(tmp_path):
    cfg = _config(tmp_path)
    clip = write_synth_clip(tmp_path / "clip", frames=30)
    log = tmp_path / "station.db"
    run_id = "run-pf"
    from station_watch.records import CycleCompleted, Verdict, VerdictState

    with Log(log) as handle:
        handle.append(
            Verdict(
                station_id=HEALTH_CONFIG["station_id"],
                ts=offset_iso(PAST, 0),
                state=VerdictState.HEALTHY,
                faults=(),
                blind_reasons=(),
                seq=0,
                run_id=run_id,
            )
        )
        handle.append(CycleCompleted(ts=offset_iso(PAST, 1), cycle=0, stages=(), run_id=run_id))
    before = hashlib.sha256(log.read_bytes()).hexdigest()

    results = _by_name(run_preflight(str(cfg), str(clip), str(log)))
    assert results["log_writable"].status == PASS
    assert results["clock"].status == PASS
    after = hashlib.sha256(log.read_bytes()).hexdigest()
    assert after == before


def test_clock_fails_when_wall_clock_predates_newest_log_row(tmp_path):
    cfg = _config(tmp_path)
    clip = write_synth_clip(tmp_path / "clip", frames=30)
    log = tmp_path / "station.db"
    future = offset_iso("2099-01-01T00:00:00.000000+00:00", 0)
    from station_watch.records import Verdict, VerdictState

    with Log(log) as handle:
        handle.append(
            Verdict(
                station_id=HEALTH_CONFIG["station_id"],
                ts=future,
                state=VerdictState.HEALTHY,
                faults=(),
                blind_reasons=(),
                seq=0,
                run_id="run-f",
            )
        )
    results = _by_name(run_preflight(str(cfg), str(clip), str(log)))
    assert results["clock"].status == FAIL


# --------------------------------------------------------------------------- #
# model_weights: SKIP names why, FAIL when zones need absent weights
# --------------------------------------------------------------------------- #
def test_model_weights_fails_when_zones_need_absent_weights(tmp_path):
    detect = yaml.safe_load((ROOT / "config" / "station-example.yaml").read_text())["detect"]
    detect["rail_positions"] = {}
    detect["keepout_rois"] = {"zoneA": {"region": [[0, 0], [1, 0], [1, 1], [0, 1]], "active": True}}
    cfg = _config(tmp_path, keepout_zones=["zoneA"], detect=detect)
    clip = write_synth_clip(tmp_path / "clip", frames=30)
    results = _by_name(run_preflight(str(cfg), str(clip), str(tmp_path / "station.db")))
    assert results["model_weights"].status == FAIL


# --------------------------------------------------------------------------- #
# config FAIL carries the K9 dotted key
# --------------------------------------------------------------------------- #
def test_config_fail_names_the_missing_key(tmp_path):
    data = yaml.safe_load((ROOT / "config" / "station-example.yaml").read_text())
    del data["takt_s"]
    cfg = tmp_path / "bad.yaml"
    cfg.write_text(yaml.safe_dump(data))
    clip = write_synth_clip(tmp_path / "clip", frames=30)
    results = _by_name(run_preflight(str(cfg), str(clip), str(tmp_path / "station.db")))
    assert results["config"].status == FAIL
    assert "takt_s" in results["config"].detail
