"""The manifest honesty rules: never report numbers over an absent or partial set.

These cover AC2 (absent / missing manifest) and AC3 (a manifest naming a missing
clip) at the loader level; the CLI-level exit codes and "no file written" are
proven end to end in ``test_evaluate.py``.
"""

from __future__ import annotations

import pytest
import yaml

from station_watch.evaluate.manifest import (
    ManifestError,
    NoLabeledSetError,
    load_manifest,
    sha256_file,
)


def _write_clip(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not really a video, but a real file")


def test_absent_manifest_raises_no_labeled_set(tmp_path):
    with pytest.raises(NoLabeledSetError):
        load_manifest(None, tmp_path)


def test_missing_manifest_file_raises_no_labeled_set(tmp_path):
    with pytest.raises(NoLabeledSetError) as exc:
        load_manifest(str(tmp_path / "nope.yaml"), tmp_path)
    assert "no labeled set present" in str(exc.value)


def test_manifest_naming_a_missing_clip_fails_naming_that_clip(tmp_path):
    clips_dir = tmp_path / "clips"
    manifest = tmp_path / "m.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "dataset": "d",
                "sessions": [{"id": "s1", "clips": [{"path": "s1/ghost.mkv"}]}],
            }
        )
    )
    with pytest.raises(ManifestError) as exc:
        load_manifest(str(manifest), clips_dir)
    assert "ghost.mkv" in str(exc.value)


def test_valid_manifest_loads_sessions_and_hash(tmp_path):
    clips_dir = tmp_path / "clips"
    _write_clip(clips_dir / "s1/a.mkv")
    _write_clip(clips_dir / "s2/b.mkv")
    manifest = tmp_path / "m.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "dataset": "demo",
                "sessions": [
                    {
                        "id": "s1",
                        "clips": [
                            {
                                "path": "s1/a.mkv",
                                "positions": [
                                    {
                                        "target": "rail_pos_1",
                                        "state": "present",
                                        "start_frame": 0,
                                        "end_frame": 5,
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "id": "s2",
                        "clips": [
                            {"path": "s2/b.mkv", "stalls": [{"start_frame": 1, "end_frame": 9}]}
                        ],
                    },
                ],
            }
        )
    )
    loaded = load_manifest(str(manifest), clips_dir)
    assert loaded.dataset == "demo"
    assert loaded.sessions == ["s1", "s2"]
    assert len(loaded.clips) == 2
    assert loaded.sha256 == sha256_file(manifest)
    assert loaded.clips[0].positions[0]["state"] == "present"


def _manifest_naming(tmp_path, rel):
    manifest = tmp_path / "m.yaml"
    manifest.write_text(
        yaml.safe_dump({"dataset": "d", "sessions": [{"id": "s1", "clips": [{"path": rel}]}]})
    )
    return manifest


@pytest.mark.parametrize("rel", ["../outside.mkv", "s1/../../outside.mkv"])
def test_clip_path_escaping_clips_dir_is_refused(tmp_path, rel):
    clips_dir = tmp_path / "clips"
    _write_clip(clips_dir / "s1/a.mkv")
    _write_clip(tmp_path / "outside.mkv")  # exists, so only the containment check stops it
    with pytest.raises(ManifestError, match="escapes the clips directory"):
        load_manifest(str(_manifest_naming(tmp_path, rel)), clips_dir)


def test_absolute_clip_path_is_refused(tmp_path):
    clips_dir = tmp_path / "clips"
    outside = tmp_path / "outside.mkv"
    _write_clip(outside)
    with pytest.raises(ManifestError, match="escapes the clips directory"):
        load_manifest(str(_manifest_naming(tmp_path, str(outside))), clips_dir)


def test_dotdot_that_stays_inside_clips_dir_is_allowed(tmp_path):
    clips_dir = tmp_path / "clips"
    _write_clip(clips_dir / "s1/a.mkv")
    loaded = load_manifest(str(_manifest_naming(tmp_path, "s2/../s1/a.mkv")), clips_dir)
    assert loaded.clips[0].clip_path.exists()


def test_relative_clips_dir_still_resolves_contained_clips(tmp_path, monkeypatch):
    _write_clip(tmp_path / "s1/a.mkv")
    monkeypatch.chdir(tmp_path)
    loaded = load_manifest(str(_manifest_naming(tmp_path, "s1/a.mkv")), ".")
    assert loaded.clips[0].clip_path.exists()


# --- HF3.8: split / recorded_on / clip_sha256 (backward compatible) --------------


def test_hf2_manifest_with_no_split_loads_every_clip_as_calibration(tmp_path):
    """An HF2-format manifest (no split, no recorded_on) loads unchanged: every clip
    is calibration, has no recorded_on, and carries the SHA-256 of its bytes."""
    clips_dir = tmp_path / "clips"
    _write_clip(clips_dir / "s1/a.mkv")
    loaded = load_manifest(str(_manifest_naming(tmp_path, "s1/a.mkv")), clips_dir)
    clip = loaded.clips[0]
    assert clip.split == "calibration"
    assert clip.recorded_on is None
    assert clip.clip_sha256 == sha256_file(clips_dir / "s1/a.mkv")
    assert len(clip.clip_sha256) == 64


def test_session_split_and_recorded_on_propagate_to_each_clip(tmp_path):
    clips_dir = tmp_path / "clips"
    _write_clip(clips_dir / "held/a.mkv")
    manifest = tmp_path / "m.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "dataset": "d",
                "sessions": [
                    {
                        "id": "held",
                        "split": "held_out",
                        "recorded_on": "2026-10-12",
                        "clips": [{"path": "held/a.mkv"}],
                    }
                ],
            }
        )
    )
    clip = load_manifest(str(manifest), clips_dir).clips[0]
    assert clip.split == "held_out"
    assert clip.recorded_on == "2026-10-12"


def test_unknown_split_value_is_refused(tmp_path):
    clips_dir = tmp_path / "clips"
    _write_clip(clips_dir / "s/a.mkv")
    manifest = tmp_path / "m.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {"sessions": [{"id": "s", "split": "train", "clips": [{"path": "s/a.mkv"}]}]}
        )
    )
    with pytest.raises(ManifestError, match="split"):
        load_manifest(str(manifest), clips_dir)
