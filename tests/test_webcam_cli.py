"""HF3.13 through the real CLI with a fake capture factory (no camera hardware needed).

``test_webcam_source.py`` covers the probe and the stability check in process; these
drive ``station-watch run --source 7`` and ``station-watch preflight --list-cameras``
through :func:`station_watch.cli.main` with the OpenCV capture replaced by a fake, so
the outcome does not depend on which devices this machine happens to have:

* an absent device index makes ``run`` exit non-zero with a message naming the index;
* ``--list-cameras`` where only index 1 opens prints index 1 with its resolution and
  fps, lists no other index, and exits 0;
* ``camera.gimbal: true`` still samples the fiducial, so the WARN carries the gimbal
  text and the measured drift together;
* a ``--drift-s`` window longer than the per-check timeout is not cut off as a FAIL.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import yaml

from station_watch import cli
from station_watch.capture import source as source_mod
from station_watch.capture.source import CaptureError, FrameSource
from station_watch.preflight import run_preflight
from station_watch.preflight import runner as preflight_runner
from station_watch.preflight.result import FAIL, PASS, WARN

sys.path.insert(0, str(Path(__file__).parent))
from helpers.synth_video import write_synth_clip  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _config(tmp_path: Path, **over) -> Path:
    data = yaml.safe_load((ROOT / "config" / "station-example.yaml").read_text())
    data.update(over)
    cfg = tmp_path / "station.yaml"
    cfg.write_text(yaml.safe_dump(data))
    return cfg


class _FakeVideoCapture:
    """Stands in for ``cv2.VideoCapture``: only the indexes in ``OPEN`` open."""

    OPEN: dict[int, tuple[int, int, float]] = {}

    def __init__(self, spec):
        self._mode = self.OPEN.get(spec) if isinstance(spec, int) else None

    def isOpened(self):  # noqa: N802 -- mirrors the OpenCV method name
        return self._mode is not None

    def get(self, prop):
        width, height, fps = self._mode or (0, 0, 0.0)
        return {
            cv2.CAP_PROP_FRAME_WIDTH: width,
            cv2.CAP_PROP_FRAME_HEIGHT: height,
            cv2.CAP_PROP_FPS: fps,
        }.get(prop, 0.0)

    def read(self):
        if self._mode is None:
            return False, None
        width, height, _ = self._mode
        return True, np.zeros((height, width, 3), np.uint8)

    def release(self):
        pass


def _fake_opencv(monkeypatch, open_modes: dict[int, tuple[int, int, float]]) -> None:
    monkeypatch.setattr(_FakeVideoCapture, "OPEN", open_modes)
    monkeypatch.setattr(source_mod.cv2, "VideoCapture", _FakeVideoCapture)


# --------------------------------------------------------------------------- #
# AC1: run --source 7 with no such device exits non-zero naming index 7
# --------------------------------------------------------------------------- #
def test_frame_source_error_names_the_device_index(monkeypatch):
    _fake_opencv(monkeypatch, {})
    try:
        FrameSource(7)
    except CaptureError as exc:
        assert "device index 7" in str(exc)
    else:  # pragma: no cover - the fake never opens index 7
        raise AssertionError("an absent index must raise CaptureError")


def test_run_source_7_with_fake_capture_exits_nonzero_naming_index_7(tmp_path, monkeypatch, capsys):
    _fake_opencv(monkeypatch, {0: (640, 480, 30.0)})
    cfg = _config(tmp_path)
    code = cli.main(
        ["run", "--config", str(cfg), "--source", "7", "--log", str(tmp_path / "station.db")]
    )
    assert code != 0
    err = capsys.readouterr().err
    assert "device index 7" in err, err


# --------------------------------------------------------------------------- #
# AC2: --list-cameras with a fake factory where only index 1 opens
# --------------------------------------------------------------------------- #
def _only_index_one_factory(index: int):
    if index != 1:
        raise CaptureError(f"could not open camera device index {index}")
    return _FakeSource()


class _FakeSource:
    resolution = (1280, 720)
    fps = 30.0

    def read(self):
        return np.zeros((720, 1280, 3), np.uint8)

    def release(self):
        pass


def test_list_cameras_cli_uses_the_injected_capture_factory(monkeypatch, capsys):
    from station_watch.preflight import cameras

    monkeypatch.setattr(cameras, "FrameSource", _only_index_one_factory)
    code = cli.main(["preflight", "--list-cameras", "--max-index", "5"])
    assert code == 0
    rows = [line.split() for line in capsys.readouterr().out.splitlines()[1:]]
    assert rows == [["1", "1280x720", "30.0", "read"]], rows


def test_list_cameras_cli_with_fake_opencv_lists_index_1_only(monkeypatch, capsys):
    _fake_opencv(monkeypatch, {1: (640, 480, 15.0)})
    code = cli.main(["preflight", "--list-cameras"])
    assert code == 0
    rows = [line.split() for line in capsys.readouterr().out.splitlines()[1:]]
    assert rows == [["1", "640x480", "15.0", "read"]], rows


# --------------------------------------------------------------------------- #
# AC4: the gimbal WARN still measures the drift; long --drift-s is not a timeout
# --------------------------------------------------------------------------- #
def test_gimbal_warn_also_reports_the_measured_drift(tmp_path):
    cfg = _config(tmp_path, camera={"gimbal": True})
    clip = write_synth_clip(tmp_path / "clip", frames=30, marker_move_from=10, marker_move_px=8)
    results = {r.name: r for r in run_preflight(str(cfg), str(clip), str(tmp_path / "s.db"))}
    stability = results["camera_stability"]
    assert stability.status == WARN
    assert "lock it before going live" in stability.detail
    assert "drifted" in stability.detail, stability.detail


def test_drift_window_longer_than_check_timeout_is_not_a_timeout(tmp_path, monkeypatch):
    # Shrink the base timeout below the drift window: the stability check must get
    # its full window on top of the base, not be cut off as a FAIL.
    monkeypatch.setattr(preflight_runner, "_CHECK_TIMEOUT_S", 0.3)
    cfg = _config(tmp_path)
    clip = write_synth_clip(tmp_path / "clip", frames=30)

    slow = preflight_runner.CHECKS["camera_stability"]

    def _slow_stability(ctx):
        import time

        time.sleep(0.5)
        return slow(ctx)

    checks = dict(preflight_runner.CHECKS, camera_stability=_slow_stability)
    results = {
        r.name: r
        for r in run_preflight(
            str(cfg), str(clip), str(tmp_path / "s.db"), drift_s=1.0, checks=checks
        )
    }
    assert results["camera_stability"].status == PASS, results["camera_stability"]
    assert results["fiducial"].status != FAIL


def test_gimbal_with_a_camera_that_never_opened_is_a_fail_not_a_warn(tmp_path):
    # A camera-dependent check never softens a camera that never opened (HF3.12 AC2):
    # the gimbal reminder rides along, but the status is FAIL naming the camera.
    cfg = _config(tmp_path, camera={"gimbal": True})
    missing = tmp_path / "no-such-clip.mkv"
    results = {r.name: r for r in run_preflight(str(cfg), str(missing), str(tmp_path / "s.db"))}
    stability = results["camera_stability"]
    assert stability.status == FAIL, stability
    assert "camera did not open" in stability.detail
    assert "lock it before going live" in stability.detail
