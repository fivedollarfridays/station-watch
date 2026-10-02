"""The calibration provenance check: held-out runs refuse to score leaked clips (HF3.8).

A ``--split held_out`` run collects the calibration provenance for the config in
force (``detect.step_times_path`` plus ``calibration.provenance``) and refuses --
exit :data:`EXIT_CALIBRATION_LEAK`, writing nothing -- when a held-out clip, session
or date is traceable into any calibration file, or a calibration file predates the
hashes and dates that would prove separation. The leak checks here run before any
clip is scored, so they use cheap byte clips; the clean held-out run and the real
CLI conformance (which score real frames) are proven in the subprocess E2E below.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from station_watch.evaluate import (
    EXIT_CALIBRATION_LEAK,
    run_evaluation,
)
from station_watch.evaluate.calibration import held_out_calibration
from station_watch.evaluate.manifest import load_manifest, sha256_file
from station_watch.evaluate.synthetic import RAIL

sys.path.insert(0, str(Path(__file__).parent))
from helpers.synth_station import write_synth_station_clip  # noqa: E402

STATION_ZONE = {"id": "bench", "region": [[0.3, 1.5], [5.5, 1.5], [5.5, 3.5], [0.3, 3.5]]}


# --- helpers ---------------------------------------------------------------------


def _clip(clips_dir, rel, data=b"clip-bytes"):
    path = clips_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _cal_file(path, *, clip_sha256s=(), sessions=(), recorded_on=None, drop=()):
    """Write a measurement file whose provenance can serve as calibration provenance."""
    prov = {
        "dataset": "cal",
        "split": "calibration",
        "clip_sha256s": sorted(clip_sha256s),
        "sessions": list(sessions),
        "recorded_on": dict(recorded_on or {}),
    }
    for key in drop:
        prov.pop(key, None)
    path.write_text(json.dumps({"provenance": prov, "metrics": {}}))
    return path


def _manifest(path, sessions):
    path.write_text(yaml.safe_dump({"dataset": "held", "sessions": sessions}))
    return path


def _config(path, *, step_times_path=None, provenance=None):
    """A minimal config naming calibration provenance (the guard reads only these keys)."""
    cfg = {"detect": {}, "calibration": {}}
    if step_times_path is not None:
        cfg["detect"]["step_times_path"] = str(step_times_path)
    if provenance is not None:
        cfg["calibration"]["provenance"] = [str(p) for p in provenance]
    path.write_text(yaml.safe_dump(cfg))
    return path


def _held_out(tmp_path, *, config, manifest, clips_dir, out="out"):
    out_dir = tmp_path / out
    rc = run_evaluation(
        config_path=str(config),
        manifest_path=str(manifest),
        out_dir=str(out_dir),
        synthetic=False,
        clips_dir=str(clips_dir),
        split="held_out",
    )
    return rc, out_dir


# --- AC2 (byte-identical clip) / AC3 (session, missing date, stale cal file) ------


def test_held_out_clip_with_calibration_bytes_exits_leak_naming_the_clip(tmp_path, capsys):
    clips = tmp_path / "clips"
    leaked = _clip(clips, "held/reused.mkv", b"the-very-same-bytes")
    cal = _cal_file(tmp_path / "cal.json", clip_sha256s=[sha256_file(leaked)], recorded_on={})
    config = _config(tmp_path / "c.yaml", provenance=[cal])
    manifest = _manifest(
        tmp_path / "m.yaml",
        [
            {
                "id": "h",
                "split": "held_out",
                "recorded_on": "2026-10-20",
                "clips": [{"path": "held/reused.mkv"}],
            }
        ],
    )
    rc, out_dir = _held_out(tmp_path, config=config, manifest=manifest, clips_dir=clips)
    assert rc == EXIT_CALIBRATION_LEAK and rc != 0
    assert "held/reused.mkv" in capsys.readouterr().err
    assert not (out_dir / "detect.json").exists()


def test_shared_session_id_exits_leak_naming_the_session(tmp_path, capsys):
    clips = tmp_path / "clips"
    _clip(clips, "h/a.mkv")
    cal = _cal_file(tmp_path / "cal.json", sessions=["shared"], recorded_on={})
    config = _config(tmp_path / "c.yaml", provenance=[cal])
    manifest = _manifest(
        tmp_path / "m.yaml",
        [
            {
                "id": "shared",
                "split": "held_out",
                "recorded_on": "2026-10-20",
                "clips": [{"path": "h/a.mkv"}],
            }
        ],
    )
    rc, _ = _held_out(tmp_path, config=config, manifest=manifest, clips_dir=clips)
    assert rc == EXIT_CALIBRATION_LEAK
    assert "shared" in capsys.readouterr().err


def test_held_out_session_without_recorded_on_exits_leak(tmp_path, capsys):
    clips = tmp_path / "clips"
    _clip(clips, "h/a.mkv")
    cal = _cal_file(tmp_path / "cal.json", recorded_on={"c": "2026-01-01"})
    config = _config(tmp_path / "c.yaml", provenance=[cal])
    manifest = _manifest(
        tmp_path / "m.yaml",
        [{"id": "h", "split": "held_out", "clips": [{"path": "h/a.mkv"}]}],
    )
    rc, _ = _held_out(tmp_path, config=config, manifest=manifest, clips_dir=clips)
    assert rc == EXIT_CALIBRATION_LEAK
    err = capsys.readouterr().err
    assert "recorded_on" in err and "h" in err


@pytest.mark.parametrize("missing", ["clip_sha256s", "recorded_on"])
def test_calibration_file_missing_hashes_or_dates_exits_leak(tmp_path, capsys, missing):
    clips = tmp_path / "clips"
    _clip(clips, "h/a.mkv")
    cal = _cal_file(tmp_path / "stale.json", recorded_on={"c": "2026-01-01"}, drop=[missing])
    config = _config(tmp_path / "c.yaml", provenance=[cal])
    manifest = _manifest(
        tmp_path / "m.yaml",
        [
            {
                "id": "h",
                "split": "held_out",
                "recorded_on": "2026-10-20",
                "clips": [{"path": "h/a.mkv"}],
            }
        ],
    )
    rc, out_dir = _held_out(tmp_path, config=config, manifest=manifest, clips_dir=clips)
    assert rc == EXIT_CALIBRATION_LEAK
    err = capsys.readouterr().err
    assert "re-run calibration" in err and "stale.json" in err
    assert not (out_dir / "detect.json").exists()


# --- AC4: cross-manifest date leak (two files) -----------------------------------


def _cross_manifest_setup(tmp_path, held_date):
    clips = tmp_path / "clips"
    _clip(clips, "b/fresh.mkv", b"brand-new-bytes")
    # Calibration step_times.json computed from manifest A: session recorded 2026-10-10.
    cal = _cal_file(
        tmp_path / "step_times.json",
        clip_sha256s=[sha256_file(_clip(clips, "a/old.mkv", b"old-calibration-bytes"))],
        sessions=["sessA"],
        recorded_on={"sessA": "2026-10-10"},
    )
    config = _config(tmp_path / "c.yaml", step_times_path=cal)
    manifest = _manifest(
        tmp_path / "B.yaml",
        [
            {
                "id": "sessB",
                "split": "held_out",
                "recorded_on": held_date,
                "clips": [{"path": "b/fresh.mkv"}],
            }
        ],
    )
    return clips, config, manifest


def test_cross_manifest_same_date_exits_leak_naming_date_and_both_sessions(tmp_path, capsys):
    clips, config, manifest = _cross_manifest_setup(tmp_path, "2026-10-10")
    rc, out_dir = _held_out(tmp_path, config=config, manifest=manifest, clips_dir=clips)
    assert rc == EXIT_CALIBRATION_LEAK
    err = capsys.readouterr().err
    assert "2026-10-10" in err and "sessA" in err and "sessB" in err
    assert not (out_dir / "detect.json").exists()


def test_cross_manifest_different_date_passes_the_guard(tmp_path):
    clips, config, manifest = _cross_manifest_setup(tmp_path, "2026-10-12")
    loaded = load_manifest(str(manifest), clips)
    calibration = held_out_calibration(str(config), loaded)
    assert isinstance(calibration, list) and len(calibration) == 1
    assert len(calibration[0]["sha256"]) == 64


def test_no_calibration_provenance_configured_returns_none(tmp_path):
    clips = tmp_path / "clips"
    _clip(clips, "h/a.mkv")
    config = _config(tmp_path / "c.yaml")  # names no provenance at all
    manifest = _manifest(
        tmp_path / "m.yaml",
        [
            {
                "id": "h",
                "split": "held_out",
                "recorded_on": "2026-10-20",
                "clips": [{"path": "h/a.mkv"}],
            }
        ],
    )
    assert held_out_calibration(str(config), load_manifest(str(manifest), clips)) == "none"


# --- AC5 / AC6 / AC7: real CLI conformance over scored clips (subprocess) ---------


def _run_cli(*args, timeout=240):
    return subprocess.run(
        [sys.executable, "-m", "station_watch", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _render(clips_dir, session, name, frames):
    path, _truth = write_synth_station_clip(
        clips_dir / session / name,
        # Motion/still runs of four frames: closed steps, so the calibration run
        # measures a real step-time baseline (a zero-step file is refused).
        [
            {"positions": {"rail_pos_1": "present", "rail_pos_2": "present"}, "motion": i % 8 < 4}
            for i in range(frames)
        ],
        rail_positions=RAIL,
        station_zone=STATION_ZONE,
        fps=20.0,
    )
    return str(Path(path).relative_to(clips_dir))


def _real_config(path, *, step_times_path=None):
    cfg = {
        "station_id": "station-1",
        "camera_id": "cam-0",
        "takt_s": 30.0,
        "grace_s": 5.0,
        "required_slots": list(RAIL),
        "keepout_zones": [],
        "liveness_window_s": 2.0,
        "dark_luma_threshold": 15.0,
        "dark_window_s": 0.3,
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
        "detect": {
            "persistence_frames": 2,
            "emit_interval_s": 5.0,
            "rail_positions": RAIL,
            "station_zone": {**STATION_ZONE, "track_motion": True},
            "keepout_rois": {},
            "blur_threshold": 100.0,
            "darkness_threshold": 40.0,
            "occlusion_threshold": 0.5,
        },
    }
    if step_times_path is not None:
        cfg["detect"]["step_times_path"] = str(step_times_path)
    Path(path).write_text(yaml.safe_dump(cfg))
    return path


def _label(path):
    return {
        "path": path,
        "positions": [
            {"target": "rail_pos_1", "state": "present", "start_frame": 0, "end_frame": 15},
            {"target": "rail_pos_2", "state": "present", "start_frame": 0, "end_frame": 15},
        ],
    }


def _write_split_manifest(path, dataset, session, split, recorded_on, rel) -> None:
    """One-session manifest for the split conformance test."""
    session_entry = {
        "id": session,
        "split": split,
        "recorded_on": recorded_on,
        "clips": [_label(rel)],
    }
    path.write_text(yaml.safe_dump({"dataset": dataset, "sessions": [session_entry]}))


def _evaluate_split(split, cfg, manifest, clips, out):
    """``station-watch evaluate --split ...`` as a subprocess, at test speed."""
    args = ["--split", split, "--config", str(cfg), "--manifest", str(manifest)]
    args += ["--clips-dir", str(clips), "--out", str(out), "--force-out", "--speed", "50"]
    return _run_cli("evaluate", *args)


def test_evaluate_split_conformance_end_to_end(tmp_path):
    clips = tmp_path / "clips"
    cal_rel = _render(clips, "cal-sess", "clip", 20)
    cal_manifest = tmp_path / "cal.yaml"
    _write_split_manifest(cal_manifest, "cal", "cal-sess", "calibration", "2026-09-01", cal_rel)

    # AC6: a --split calibration run writes split/clip_sha256s/recorded_on everywhere.
    cal_cfg = _real_config(tmp_path / "cal-cfg.yaml")
    cal_out = tmp_path / "cal-out"
    rc = _evaluate_split("calibration", cal_cfg, cal_manifest, clips, cal_out)
    assert rc.returncode == 0, rc.stderr
    for name in ("detect.json", "step_times.json"):
        prov = json.loads((cal_out / name).read_text())["provenance"]
        assert prov["split"] == "calibration"
        assert prov["clip_sha256s"] == sorted(prov["clip_sha256s"]) and prov["clip_sha256s"]
        assert prov["recorded_on"] == {"cal-sess": "2026-09-01"}

    step_times = cal_out / "step_times.json"
    held_cfg = _real_config(tmp_path / "held-cfg.yaml", step_times_path=step_times)

    # AC2: a held-out clip byte-identical to a calibration clip exits 5 naming it.
    reuse_manifest = tmp_path / "reuse.yaml"
    _write_split_manifest(reuse_manifest, "held", "reuse-sess", "held_out", "2026-09-02", cal_rel)
    reuse_out = tmp_path / "reuse-out"
    reuse = _evaluate_split("held_out", held_cfg, reuse_manifest, clips, reuse_out)
    assert reuse.returncode == EXIT_CALIBRATION_LEAK, reuse.stderr
    assert cal_rel in reuse.stderr
    assert not (reuse_out / "detect.json").exists()

    # AC5 / AC7: a clean held-out run on fresh clips writes files stamped held_out.
    fresh_rel = _render(clips, "held-sess", "clip", 24)
    fresh_manifest = tmp_path / "fresh.yaml"
    _write_split_manifest(fresh_manifest, "held", "held-sess", "held_out", "2026-09-05", fresh_rel)
    held_out_dir = tmp_path / "held-out"
    held = _evaluate_split("held_out", held_cfg, fresh_manifest, clips, held_out_dir)
    assert held.returncode == 0, held.stderr
    prov = json.loads((held_out_dir / "detect.json").read_text())["provenance"]
    assert prov["split"] == "held_out"
    assert prov["clip_sha256s"] == sorted(prov["clip_sha256s"]) and prov["clip_sha256s"]
    assert prov["calibration"] == [{"path": str(step_times), "sha256": sha256_file(step_times)}]
