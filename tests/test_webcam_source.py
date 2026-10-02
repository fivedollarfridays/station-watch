"""`--source <device index>` as a documented, tested path, plus the webcam aids.

These cover the three pieces HF3.13 adds on top of HF3.12's preflight:

* ``runner.startup.parse_source`` maps a bare integer to a device index and
  anything else to a path, and ``station-watch run --source 7`` on a machine with
  no such device exits non-zero with a message that names index 7 (skipped with a
  reason if a device really does answer at index 7).
* ``preflight --list-cameras`` probes device indexes ``0..N`` through an injectable
  capture factory, printing the index, resolution and fps of each that opens and
  never failing on one that does not; the real CLI path exits 0.
* the ``camera_stability`` check: ``camera.gimbal: true`` is always a WARN naming the
  gimbal risk, a source whose marker drifts past half ``fiducial.tolerance_px`` is a
  WARN carrying the measured drift, and a steady source is a PASS.
* ``docs/CAMERAS.md`` exists, every ``station-watch`` command in it parses with the
  real argparse parser, and each Pocket 3 device step is marked ``unverified``.
"""

from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

import numpy as np
import pytest
import yaml

from station_watch import cli
from station_watch.capture.source import CaptureError
from station_watch.preflight import run_preflight
from station_watch.preflight.cameras import probe_cameras, render_camera_list
from station_watch.preflight.result import PASS, WARN
from station_watch.runner.startup import parse_source

sys.path.insert(0, str(Path(__file__).parent))
from helpers.synth_video import write_synth_clip  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _config(tmp_path: Path, **over) -> Path:
    data = yaml.safe_load((ROOT / "config" / "station-example.yaml").read_text())
    data.update(over)
    cfg = tmp_path / "station.yaml"
    cfg.write_text(yaml.safe_dump(data))
    return cfg


def _by_name(results):
    return {r.name: r for r in results}


# --------------------------------------------------------------------------- #
# AC1: parse_source int vs path; run --source 7 exits non-zero naming index 7
# --------------------------------------------------------------------------- #
def test_parse_source_maps_integer_to_index_and_text_to_path():
    assert parse_source("0") == 0
    assert isinstance(parse_source("0"), int)
    assert parse_source("clip.mkv") == "clip.mkv"
    assert isinstance(parse_source("clip.mkv"), str)


def test_run_with_absent_device_index_exits_nonzero_naming_it(tmp_path, capsys):
    from station_watch.capture.source import FrameSource

    try:
        probe = FrameSource(7)
    except CaptureError:
        probe = None
    if probe is not None:
        probe.release()
        pytest.skip("a capture device answers at index 7 on this machine")

    cfg = _config(tmp_path)
    code = cli.main(
        ["run", "--config", str(cfg), "--source", "7", "--log", str(tmp_path / "station.db")]
    )
    assert code != 0
    assert "7" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# AC2: --list-cameras with a fake factory where only index 1 opens
# --------------------------------------------------------------------------- #
class _FakeCamera:
    def __init__(self, width, height, fps, frame_ok):
        self.resolution = (width, height)
        self.fps = fps
        self._frame = (
            np.zeros((height, width, 3), np.uint8) if frame_ok else None
        )

    def read(self):
        return self._frame

    def release(self):
        pass


def _only_index_one(index: int):
    if index == 1:
        return _FakeCamera(640, 480, 30.0, frame_ok=True)
    raise CaptureError(f"could not open capture source: {index!r}")


def test_list_cameras_prints_only_the_index_that_opens():
    reports = probe_cameras(5, source_factory=_only_index_one)
    assert [r.index for r in reports] == [1]
    report = reports[0]
    assert report.width == 640 and report.height == 480
    assert report.fps == 30.0
    assert report.frame_read is True
    rendered = render_camera_list(reports)
    assert "1" in rendered
    assert "640" in rendered and "480" in rendered
    assert "30" in rendered


def test_list_cameras_cli_exits_zero(capsys):
    code = cli.main(["preflight", "--list-cameras", "--max-index", "0"])
    assert code == 0
    # Something is printed, whether a camera opened or not.
    assert capsys.readouterr().out.strip() != ""


# --------------------------------------------------------------------------- #
# AC4: camera_stability -- gimbal WARN, drift WARN w/ measured drift, steady PASS
# --------------------------------------------------------------------------- #
def test_camera_stability_warns_for_a_gimbal():
    # A gimbal is always a WARN, regardless of how steady the clip looks.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        cfg = _config(tmp_path, camera={"gimbal": True})
        clip = write_synth_clip(tmp_path / "clip", frames=30)
        results = _by_name(run_preflight(str(cfg), str(clip), str(tmp_path / "station.db")))
    stability = results["camera_stability"]
    assert stability.status == WARN
    assert "gimbal" in stability.detail.lower()
    assert "view_shifted" in stability.detail


def test_camera_stability_warns_when_the_marker_drifts(tmp_path):
    # tolerance_px is 10 -> half is 5px; an 8px shift mid-clip is a drift WARN.
    cfg = _config(tmp_path)
    clip = write_synth_clip(
        tmp_path / "clip", frames=30, marker_move_from=10, marker_move_px=8
    )
    results = _by_name(run_preflight(str(cfg), str(clip), str(tmp_path / "station.db")))
    stability = results["camera_stability"]
    assert stability.status == WARN
    assert re.search(r"\d", stability.detail)  # the measured drift is reported
    assert "8" in stability.detail or "7" in stability.detail


def test_camera_stability_passes_for_a_steady_source(tmp_path):
    cfg = _config(tmp_path)
    clip = write_synth_clip(tmp_path / "clip", frames=30)
    results = _by_name(run_preflight(str(cfg), str(clip), str(tmp_path / "station.db")))
    assert results["camera_stability"].status == PASS


# --------------------------------------------------------------------------- #
# AC5: docs/CAMERAS.md exists, its commands parse, and Pocket 3 steps are marked
# --------------------------------------------------------------------------- #
DOC = ROOT / "docs" / "CAMERAS.md"


def _station_watch_commands(text: str) -> list[str]:
    """Every ``station-watch ...`` command: whole lines in fenced blocks and inline spans."""
    fenced = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("station-watch ")]
    inline = re.findall(r"`(station-watch [^`]+)`", text)
    return fenced + inline


def test_doc_command_extraction_includes_inline_spans():
    text = "run `station-watch preflight --list-cameras` first\n```\nstation-watch qa\n```"
    assert _station_watch_commands(text) == [
        "station-watch qa",
        "station-watch preflight --list-cameras",
    ]


def test_pocket3_uvc_claim_is_marked_unverified():
    # The Pocket 3 working as a plain UVC webcam is a device claim not yet checked
    # on the hardware, so the sentence making it carries the "unverified" mark.
    claim = next(ln for ln in DOC.read_text().splitlines() if "plain UVC webcam" in ln)
    assert "unverified" in claim.lower(), claim


def test_cameras_doc_exists():
    assert DOC.exists()


def test_every_station_watch_command_in_the_doc_parses(capsys):
    commands = _station_watch_commands(DOC.read_text())
    assert commands, "the doc should contain station-watch commands"
    parser = cli.build_parser()
    for command in commands:
        args = shlex.split(command)
        assert args[0] == "station-watch"
        parser.parse_args(args[1:])  # raises SystemExit on a bad command


def test_pocket3_device_steps_are_marked_unverified():
    text = DOC.read_text()
    assert "unverified" in text
    lines = text.splitlines()
    # The Pocket 3 device-step section runs from its heading to the next "## ".
    start = next(i for i, ln in enumerate(lines) if ln.startswith("### On the Pocket 3"))
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    steps = [
        ln
        for ln in lines[start + 1 : end]
        if re.match(r"\s*(\d+\.|-)\s", ln)
    ]
    assert steps, "the Pocket 3 section should list device steps"
    for step in steps:
        assert "unverified" in step.lower(), step
