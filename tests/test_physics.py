"""The physics measurement scaffold and the fps sweep (HF3A.3).

The physics scripts share one runner behind ``station-watch measure <name>``. The
honesty contract is stricter than ``evaluate``'s: a physics table that does not
exist yet must *say so in the repo*, so with no input the runner writes a
``no_input`` file -- with no ``metrics`` key at all, so the K13 claims test can
never cite it -- and exits zero. ``--synthetic`` is a test-only proving path that
writes only into the given ``--out`` and stamps ``dataset_kind: synthetic``.

The first script, ``fps_sweep``, subsamples each clip with a labeled keep-out
reach to 30/15/5 fps and reports, per fps, the frames that fell inside each event
beside the predicted minimum ``f * d`` (PLAN 3.4, frame rate vs event duration).
"""

from __future__ import annotations

import json
import numbers
from pathlib import Path

import yaml

from station_watch.cli import main
from station_watch.evaluate.manifest import load_manifest

ROOT = Path(__file__).resolve().parent.parent


def _has_number(node) -> bool:
    """True if any JSON value anywhere in ``node`` is an int or float."""
    if isinstance(node, bool):
        return False
    if isinstance(node, numbers.Number):
        return True
    if isinstance(node, dict):
        return any(_has_number(v) for v in node.values())
    if isinstance(node, list):
        return any(_has_number(v) for v in node)
    return False


# --- AC4: the optional tags load, HF2 manifests are unchanged -----------------


def test_manifest_loads_optional_tags(tmp_path):
    clips_dir = tmp_path / "clips"
    (clips_dir / "s1").mkdir(parents=True)
    (clips_dir / "s1" / "a.mkv").write_bytes(b"clip")
    manifest = tmp_path / "m.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "dataset": "d",
                "sessions": [
                    {
                        "id": "s1",
                        "clips": [{"path": "s1/a.mkv", "tags": {"fps": 30, "lamp": "led"}}],
                    }
                ],
            }
        )
    )
    loaded = load_manifest(str(manifest), clips_dir)
    assert loaded.clips[0].tags == {"fps": 30, "lamp": "led"}


def test_manifest_without_tags_defaults_to_empty(tmp_path):
    clips_dir = tmp_path / "clips"
    (clips_dir / "s1").mkdir(parents=True)
    (clips_dir / "s1" / "a.mkv").write_bytes(b"clip")
    manifest = tmp_path / "m.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {"dataset": "d", "sessions": [{"id": "s1", "clips": [{"path": "s1/a.mkv"}]}]}
        )
    )
    assert load_manifest(str(manifest), clips_dir).clips[0].tags == {}


# --- AC1: no input writes a no_input file with no numeric result fields --------


def test_no_clips_writes_no_input(tmp_path):
    out = tmp_path / "fps_sweep.json"
    rc = main(["measure", "fps_sweep", "--out", str(out)])
    assert rc == 0
    data = json.loads(out.read_text())
    assert data["status"] == "no_input"
    assert data["reason"]  # names why
    assert "clips" in data["reason"].lower()
    assert "metrics" not in data  # the claims test can never cite it
    assert set(data["provenance"]) == {"git_commit", "date_utc"}
    assert not _has_number(data)  # no numeric result fields anywhere


def test_manifest_with_no_tagged_clips_is_no_input(tmp_path):
    # A manifest whose clip carries keep-out labels but no fps tag is not an
    # fps_sweep input -- the sweep needs the clip's native capture rate to
    # subsample from, so it reports no_input rather than inventing one.
    clips_dir = tmp_path / "clips"
    (clips_dir / "s1").mkdir(parents=True)
    (clips_dir / "s1" / "a.mkv").write_bytes(b"clip")
    manifest = tmp_path / "m.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "dataset": "d",
                "sessions": [
                    {
                        "id": "s1",
                        "clips": [
                            {
                                "path": "s1/a.mkv",
                                "keepouts": [{"zone": "z", "start_frame": 0, "end_frame": 5}],
                            }
                        ],
                    }
                ],
            }
        )
    )
    out = tmp_path / "fps_sweep.json"
    rc = main(
        [
            "measure",
            "fps_sweep",
            "--clips",
            str(manifest),
            "--clips-dir",
            str(clips_dir),
            "--out",
            str(out),
        ]
    )
    assert rc == 0
    data = json.loads(out.read_text())
    assert data["status"] == "no_input"
    assert "tag" in data["reason"].lower() or "fps_sweep" in data["reason"]
    assert "metrics" not in data


# --- AC2: the synthetic sweep's measured frames match f*d within one frame -----


def test_synthetic_sweep_frames_match_prediction(tmp_path):
    out = tmp_path / "fps_sweep.json"
    rc = main(["measure", "fps_sweep", "--synthetic", "--out", str(out)])
    assert rc == 0
    data = json.loads(out.read_text())
    assert data["provenance"]["dataset_kind"] == "synthetic"
    metrics = data["metrics"]
    assert metrics["events"], "the synthetic set must contain at least one keep-out event"
    for event in metrics["events"]:
        for fps in ("30", "15", "5"):
            per = event["by_fps"][fps]
            assert abs(per["frames_in_event"] - per["predicted_frames"]) <= 1, (fps, per)
    # The sweep actually runs Detect, not just the subsampling arithmetic: the
    # scripted backend reaches into the zone, so every fps detects the event (a
    # measurement that never detects would be meaningless).
    for fps in ("30", "15", "5"):
        assert metrics["by_fps"][fps]["detection_rate"] == 1.0, (fps, metrics["by_fps"][fps])


def test_synthetic_writes_only_to_out(tmp_path, monkeypatch):
    # --synthetic must never touch the committed physics/v1 trees.
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "proof.json"
    assert main(["measure", "fps_sweep", "--synthetic", "--out", str(out)]) == 0
    assert out.exists()
    assert not (tmp_path / "measurements" / "physics").exists()
    assert not (tmp_path / "measurements" / "v1").exists()


# --- AC3: the committed physics files are no_input, no stray synthetic ---------


def test_committed_fps_sweep_file_is_no_input():
    path = ROOT / "measurements" / "physics" / "fps_sweep.json"
    assert path.exists(), "the fps sweep no_input file must be committed"
    data = json.loads(path.read_text())
    assert data["status"] == "no_input"
    assert "metrics" not in data


def test_every_committed_physics_file_is_no_input():
    physics = ROOT / "measurements" / "physics"
    files = list(physics.glob("*.json"))
    assert files, "expected at least the committed fps_sweep.json"
    for path in files:
        data = json.loads(path.read_text())
        assert data["status"] == "no_input", path
        assert "metrics" not in data, path


def test_no_committed_synthetic_result_outside_synthetic_dir():
    measurements = ROOT / "measurements"
    for path in measurements.rglob("*.json"):
        if "synthetic" in path.relative_to(measurements).parts:
            continue
        data = json.loads(path.read_text())
        kind = data.get("provenance", {}).get("dataset_kind")
        assert kind != "synthetic", f"{path} is dataset_kind synthetic outside synthetic/"
