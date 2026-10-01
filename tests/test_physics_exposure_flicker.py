"""HF3A.4 proving tests: motion blur vs exposure, and lighting flicker aliasing.

Each proving test drives the real ``station-watch measure`` CLI over a ``--synthetic``
input and reads the scored measurement file back, so what is asserted is the whole
render -> measure path (README K14), not a mocked estimator. The blur test blurs HF2.1
frames with a known kernel and checks the measured length; the flicker test runs a
120 Hz lamp through the *real* Capture and checks it aliases away and raises no dark
alarm. The no-input tests confirm both scripts stay honest without clips.
"""

from __future__ import annotations

import json
import numbers
from pathlib import Path

from station_watch.cli import main
from station_watch.physics.synthetic_blur import KERNEL_LEN

ROOT = Path(__file__).resolve().parent.parent


def _has_number(node) -> bool:
    if isinstance(node, bool):
        return False
    if isinstance(node, numbers.Number):
        return True
    if isinstance(node, dict):
        return any(_has_number(v) for v in node.values())
    if isinstance(node, list):
        return any(_has_number(v) for v in node)
    return False


# --- AC1: the synthetic blur measures the known kernel length within 15 percent -------


def test_synthetic_exposure_blur_measures_kernel_length(tmp_path):
    out = tmp_path / "exposure_blur.json"
    assert main(["measure", "exposure_blur", "--synthetic", "--out", str(out)]) == 0
    data = json.loads(out.read_text())
    assert data["provenance"]["dataset_kind"] == "synthetic"
    clips = data["metrics"]["clips"]
    assert clips, "the synthetic set must contain at least one blurred clip"
    for clip in clips:
        measured = clip["measured_blur_px"]
        assert measured is not None
        assert abs(measured - KERNEL_LEN) / KERNEL_LEN <= 0.15, (measured, KERNEL_LEN)
        # the predicted smear and the unknown share are reported beside the measurement
        assert clip["predicted_blur_mm"] > 0
        assert clip["measured_blur_mm"] > 0
        assert 0.0 <= clip["unknown_share"] <= 1.0
    assert data["metrics"]["unknown_share_by_exposure"]


# --- AC2: the 120 Hz lamp aliases to a constant and opens no dark record --------------


def test_synthetic_flicker_aliases_and_opens_no_dark_record(tmp_path):
    out = tmp_path / "flicker.json"
    assert main(["measure", "flicker", "--synthetic", "--out", str(out)]) == 0
    data = json.loads(out.read_text())
    assert data["provenance"]["dataset_kind"] == "synthetic"
    clips = data["metrics"]["clips"]
    assert clips, "the synthetic set must contain at least one flicker clip"
    for clip in clips:
        # 120 Hz sampled at 30 fps folds to 0 Hz: a constant, invisible as oscillation.
        assert clip["expected_alias_hz"] == 0.0
        assert clip["aliased_freq_hz"] == 0.0
        assert clip["peak_to_peak"] < 2.0, clip["peak_to_peak"]
        # flicker alone is not a blind condition: no dark record on a lit clip.
        assert clip["dark_record_opened"] is False
    assert data["metrics"]["any_dark_record_opened"] is False


def test_synthetic_writes_only_to_out(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("exposure_blur", "flicker"):
        out = tmp_path / f"{name}-proof.json"
        assert main(["measure", name, "--synthetic", "--out", str(out)]) == 0
        assert out.exists()
    assert not (tmp_path / "measurements" / "physics").exists()
    assert not (tmp_path / "measurements" / "v1").exists()


# --- AC3: without clips both scripts write no_input files with no numbers --------------


def test_no_clips_writes_no_input_for_both_scripts(tmp_path):
    for name in ("exposure_blur", "flicker"):
        out = tmp_path / f"{name}.json"
        assert main(["measure", name, "--out", str(out)]) == 0
        data = json.loads(out.read_text())
        assert data["status"] == "no_input"
        assert "clips" in data["reason"].lower()
        assert "metrics" not in data
        assert not _has_number(data)


def test_committed_files_are_no_input():
    for name in ("exposure_blur", "flicker"):
        path = ROOT / "measurements" / "physics" / f"{name}.json"
        assert path.exists(), f"the {name} no_input file must be committed"
        data = json.loads(path.read_text())
        assert data["status"] == "no_input"
        assert "metrics" not in data
