"""``station-watch preflight --cold-start``: launch-to-first-frame/verdict/healthy seconds.

Every test here drives the real CLI as a subprocess, and the cold-start command
itself launches a real ``station-watch run`` child (K14). A clip that turns healthy
writes the three launch-to-first timings in increasing order; a clip that never
turns healthy writes ``status: no_healthy_verdict`` with no ``metrics`` key (K13); an
``--out`` outside the dataset kind's measurement tree is refused before any child
starts; and rows an earlier run left in the same Log never count as this start's.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import yaml

from station_watch.synth.video import write_synth_clip

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "station-example.yaml"
OUT_REL = "measurements/synthetic/cold_start.json"
TIMINGS = ("launch_to_first_frame_s", "launch_to_first_verdict_s", "launch_to_first_healthy_s")


def _entry_point() -> list[str]:
    script = Path(sys.executable).parent / "station-watch"
    return [str(script)] if script.exists() else [sys.executable, "-m", "station_watch"]


def _never_healthy_config(tmp_path: Path) -> Path:
    """The example config with a fiducial window shorter than one cycle.

    With the shipped 1.0 s window the marker-missing blind opens at about the same
    instant as the first verdict, which can then still read healthy; at 0.2 s the
    blind is open before the first verdict, so a marker-less clip is never healthy.
    """
    data = yaml.safe_load(CONFIG.read_text())
    data["fiducial"]["window_s"] = 0.2
    cfg = tmp_path / "never-healthy.yaml"
    cfg.write_text(yaml.safe_dump(data))
    return cfg


def _cold_start(
    tmp_path: Path, clip: Path, log: Path, *extra: str, config: Path = CONFIG
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            *_entry_point(),
            "preflight",
            "--cold-start",
            "--config",
            str(config),
            "--source",
            str(clip),
            "--log",
            str(log),
            "--dataset-kind",
            "synthetic",
            *extra,
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_cold_start_on_a_healthy_clip_writes_increasing_timings(tmp_path):
    clip = write_synth_clip(tmp_path / "clip", frames=60, fps=10.0)
    result = _cold_start(tmp_path, clip, tmp_path / "station.db", "--out", OUT_REL)
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads((tmp_path / OUT_REL).read_text())
    assert data["provenance"]["dataset_kind"] == "synthetic"
    assert data["provenance"]["source"] == "clip.mkv"  # the file name only, never a path
    frame, verdict, healthy = (data["metrics"][key] for key in TIMINGS)
    assert 0 < frame <= verdict <= healthy, data["metrics"]
    assert "status" not in data or data["status"] == "ok"


def test_cold_start_on_a_clip_that_never_turns_healthy_writes_no_metrics(tmp_path):
    clip = write_synth_clip(tmp_path / "clip", frames=40, fps=10.0, hide_marker_from=0)
    result = _cold_start(
        tmp_path,
        clip,
        tmp_path / "station.db",
        "--out",
        OUT_REL,
        "--timeout-s",
        "30",
        config=_never_healthy_config(tmp_path),
    )
    assert result.returncode == 1, result.stdout + result.stderr
    data = json.loads((tmp_path / OUT_REL).read_text())
    assert data["status"] == "no_healthy_verdict"
    assert "metrics" not in data
    assert data["reason"]


def test_cold_start_refuses_an_out_outside_the_kind_tree_before_launching(tmp_path):
    clip = write_synth_clip(tmp_path / "clip", frames=20, fps=10.0)
    log = tmp_path / "station.db"
    result = _cold_start(tmp_path, clip, log, "--out", "elsewhere.json")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "measurements/synthetic/" in result.stderr
    assert not log.exists(), "no run child may start when --out is refused"


def test_cold_start_requires_out(tmp_path):
    clip = write_synth_clip(tmp_path / "clip", frames=20, fps=10.0)
    result = _cold_start(tmp_path, clip, tmp_path / "station.db")
    assert result.returncode == 2
    assert "--out" in result.stderr


def test_rows_from_an_earlier_run_in_the_same_log_do_not_count(tmp_path):
    log = tmp_path / "station.db"
    healthy = write_synth_clip(tmp_path / "healthy", frames=30, fps=10.0)
    subprocess.run(
        [
            *_entry_point(),
            "run",
            "--config",
            str(CONFIG),
            "--source",
            str(healthy),
            "--log",
            str(log),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    with sqlite3.connect(log) as conn:
        healthy_rows = conn.execute(
            "SELECT count(*) FROM records WHERE json_extract(body, '$.state') = 'healthy'"
        ).fetchone()[0]
    assert healthy_rows > 0  # the earlier run did leave healthy verdicts behind
    blind = write_synth_clip(tmp_path / "blind", frames=30, fps=10.0, hide_marker_from=0)
    result = _cold_start(
        tmp_path,
        blind,
        log,
        "--out",
        OUT_REL,
        "--timeout-s",
        "30",
        config=_never_healthy_config(tmp_path),
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert json.loads((tmp_path / OUT_REL).read_text())["status"] == "no_healthy_verdict"
