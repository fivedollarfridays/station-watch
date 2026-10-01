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
