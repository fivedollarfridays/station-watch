"""The Detector composes trackers; Capture runs it without ever faking a blind.

``Detector`` finds the fiducial once per frame and fans the corners out to every
tracker (the shared ``update`` / ``unknown_all`` protocol). ``build_detector``
builds one only when the config names Detect targets. The Capture test proves the
hook end to end on a real clip: a Detect exception on one frame becomes
``part_unknown`` (cause ``detect_error``) for every target, Capture keeps writing
frames, and no ``disconnected`` BlindRecord is ever written.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2

from station_watch.capture import BlindThresholds, Capture, FrameSource
from station_watch.config import StationConfig
from station_watch.detect.detector import (
    CAUSE_DETECT_ERROR,
    Detector,
    build_detector,
    detect_targets_configured,
)
from station_watch.log import Log
from station_watch.records import BlindReason, BlindState, Observation, ObservationKind

sys.path.insert(0, str(Path(__file__).parent))
from helpers.synth_station import write_synth_station_clip  # noqa: E402

RAIL = {
    "rail_pos_1": [[0.7, 1.9], [1.7, 1.9], [1.7, 2.5], [0.7, 2.5]],
    "rail_pos_2": [[2.5, 1.9], [3.5, 1.9], [3.5, 2.5], [2.5, 2.5]],
}
KEEPOUT = {
    "zone_press": {"region": [[4.0, 1.9], [5.3, 1.9], [5.3, 3.0], [4.0, 3.0]], "active": True}
}
STATION_ZONE = {"id": "bench", "region": [[0.3, 1.5], [5.5, 1.5], [5.5, 3.5], [0.3, 3.5]]}
# The synth default marker (xy 30,30, px 56) has its centre at (58, 58).
MARKER_CENTER = [58, 58]
EPOCH = "0001-01-01T00:00:00.000000+00:00"


def _config(*, rail_positions=None, **detect_over) -> StationConfig:
    rail_positions = RAIL if rail_positions is None else rail_positions
    detect = {
        "persistence_frames": 2,
        "emit_interval_s": 10_000.0,
        "rail_positions": rail_positions,
        "station_zone": STATION_ZONE,
        "keepout_rois": KEEPOUT,
        "blur_threshold": 100.0,
        "darkness_threshold": 40.0,
        "occlusion_threshold": 0.5,
    }
    detect.update(detect_over)
    return StationConfig.from_mapping(
        {
            "station_id": "station-1",
            "camera_id": "cam-0",
            "takt_s": 30.0,
            "grace_s": 5.0,
            "required_slots": list(rail_positions),
            "keepout_zones": [],
            "liveness_window_s": 5.0,
            "dark_luma_threshold": 15.0,
            "dark_window_s": 2.0,
            "frozen_frames": 10_000,
            "recover_good_frames": 3,
            "cycle_interval_s": 0.1,
            "recover_healthy_verdicts": 2,
            "fiducial": {
                "dictionary_id": "DICT_4X4_50",
                "marker_id": 0,
                "expected_center_px": MARKER_CENTER,
                "tolerance_px": 10,
                "window_s": 10_000.0,
            },
            "alarm": {"sinks": ["screen"]},
            "watchdog": {"cycle_window_s": 10.0, "alarm_eval_window_s": 10.0, "sinks": ["screen"]},
            "detect": detect,
        }
    )


def _one_synth_frame(tmp_path, spec):
    path, _truth = write_synth_station_clip(
        tmp_path / "frame",
        [spec],
        rail_positions=RAIL,
        keepout_rois=KEEPOUT,
        station_zone=STATION_ZONE,
    )
    cap = cv2.VideoCapture(str(path))
    ok, frame = cap.read()
    cap.release()
    assert ok
    return frame


def _clip_frames(path):
    cap = cv2.VideoCapture(str(path))
    frames = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()
    return frames


# --- Detector: one marker find per frame, fanned out to every tracker ---------


def test_process_finds_the_marker_once_and_shares_corners_with_every_tracker(tmp_path):
    seen = []

    class FakeTracker:
        def __init__(self, tag):
            self.tag = tag

        def update(self, frame, frame_id, ts, corners):
            seen.append((self.tag, frame_id, corners))
            return [_unknown_obs(frame_id, ts, self.tag, "none")]

        def unknown_all(self, frame_id, ts, cause, detail):
            return [_unknown_obs(frame_id, ts, self.tag, cause)]

    detector = Detector(_config(rail_positions={}), run_id="r")  # no auto tracker
    detector.add_tracker(FakeTracker("a"))
    detector.add_tracker(FakeTracker("b"))
    frame = _one_synth_frame(tmp_path, {"positions": {"rail_pos_1": "present"}})

    out = detector.process(frame, 7, "2026-01-01T00:00:00.000000+00:00")

    assert [tag for tag, *_ in seen] == ["a", "b"], "every tracker is updated"
    (_, _, corners_a), (_, _, corners_b) = seen
    assert corners_a is not None and corners_a is corners_b, "the marker is found once and shared"
    assert len(out) == 2, "each tracker's observations are concatenated"


def test_process_with_given_corners_does_not_search_again(tmp_path, monkeypatch):
    import station_watch.detect.detector as detector_mod
    from station_watch.detect.geometry import find_marker_corners, marker_center

    calls = {"n": 0}

    def counting(*args, **kwargs):
        calls["n"] += 1
        return find_marker_corners(*args, **kwargs)

    monkeypatch.setattr(detector_mod, "find_marker_corners", counting)

    config = _config()
    frame = _one_synth_frame(tmp_path, {"positions": {"rail_pos_1": "present"}})
    corners = find_marker_corners(frame, config.fiducial["dictionary_id"], config.fiducial["marker_id"])
    assert corners is not None

    detector = Detector(config, run_id="r")
    given = detector.process(frame, 0, EPOCH, corners=corners)
    assert calls["n"] == 0, "corners supplied -> the Detector must not search for the marker"
    assert marker_center(corners) is not None  # the center helper derives from corners

    detector_again = Detector(config, run_id="r")
    found = detector_again.process(frame, 1, EPOCH)
    assert calls["n"] == 1, "called without corners -> the Detector finds them itself"
    # Same frame, same reading whether corners were supplied or found.
    assert {o.target for o in given} == {o.target for o in found}


def _unknown_obs(frame_id, ts, target, cause) -> Observation:
    return Observation(
        station_id="station-1",
        frame_id=frame_id,
        ts=ts,
        kind=ObservationKind.PART_UNKNOWN,
        target=target,
        method="test",
        confidence_ceiling=0.0,
        detector_output={"cause": cause},
        run_id="r",
    )


def test_unknown_all_forces_part_unknown_for_every_target_with_the_cause(tmp_path):
    detector = Detector(_config(), run_id="r")
    out = detector.unknown_all(3, "2026-01-01T00:00:00.000000+00:00", CAUSE_DETECT_ERROR, "boom")

    by_target = {obs.target: obs for obs in out}
    assert set(by_target) == set(RAIL)
    for obs in by_target.values():
        assert obs.kind is ObservationKind.PART_UNKNOWN
        assert obs.detector_output["cause"] == CAUSE_DETECT_ERROR


# --- build_detector: only when the config names Detect targets -----------------


def test_detect_targets_configured_tracks_rail_positions():
    assert detect_targets_configured(_config()) is True
    assert detect_targets_configured(_config(rail_positions={})) is False


def test_build_detector_is_none_without_targets_and_reads_rails_with_them(tmp_path):
    assert build_detector(_config(rail_positions={}), run_id="r") is None

    detector = build_detector(_config(), run_id="r")
    assert detector is not None
    path, _truth = write_synth_station_clip(
        tmp_path / "clip",
        [{"positions": {"rail_pos_1": "present", "rail_pos_2": "present"}} for _ in range(3)],
        rail_positions=RAIL,
        keepout_rois=KEEPOUT,
        station_zone=STATION_ZONE,
    )
    out = []
    for frame_id, frame in enumerate(_clip_frames(path)):
        out.extend(detector.process(frame, frame_id, f"2026-01-01T00:00:0{frame_id}.000000+00:00"))
    assert out, "a configured detector reads real observations off the frames"
    assert all(obs.method != "fixture" for obs in out)
    assert any("rail_positions" in obs.method for obs in out)


# --- AC2: a Detect error on one frame is part_unknown, never a disconnect ------


class _FlakyDetector:
    """Wraps a real Detector but raises on one frame (a Detect bug mid-run)."""

    def __init__(self, inner, raise_on):
        self._inner = inner
        self._raise_on = raise_on

    def process(self, frame, frame_id, ts, corners=None):
        if frame_id == self._raise_on:
            raise RuntimeError("synthetic detect failure")
        return self._inner.process(frame, frame_id, ts, corners)

    def unknown_all(self, frame_id, ts, cause, detail):
        return self._inner.unknown_all(frame_id, ts, cause, detail)


def test_detect_exception_is_part_unknown_for_every_target_no_disconnect(tmp_path):
    config = _config()
    path, _truth = write_synth_station_clip(
        tmp_path / "clip",
        [{"positions": {"rail_pos_1": "present", "rail_pos_2": "present"}} for _ in range(6)],
        rail_positions=RAIL,
        keepout_rois=KEEPOUT,
        station_zone=STATION_ZONE,
        fps=20.0,
    )
    log = Log(tmp_path / "log.db")
    detector = _FlakyDetector(Detector(config, run_id="r"), raise_on=3)
    capture = Capture(
        FrameSource(str(path)),
        station_id=config.station_id,
        camera_id=config.camera_id,
        run_id="r",
        sleep=lambda _s: None,
        detector=detector,
    )
    capture.run(log, BlindThresholds.from_station_config(config))

    frames = log.since(EPOCH, ["frame"])
    assert len(frames) == 6, "Capture keeps writing frames through a Detect error"

    obs = log.since(EPOCH, ["obs"])
    failed = [o for o in obs if o.frame_id == 3 and o.kind is ObservationKind.PART_UNKNOWN]
    assert {o.target for o in failed} == set(RAIL), "every target reads part_unknown on the error"
    for o in failed:
        assert o.detector_output["cause"] == CAUSE_DETECT_ERROR
        assert "synthetic detect failure" in o.detector_output["scores"]["detail"]

    blind = log.since(EPOCH, ["blind"])
    disconnected = [
        b for b in blind if b.reason == BlindReason.DISCONNECTED and b.state == BlindState.OPENED
    ]
    assert disconnected == [], "a Detect error must not look like a disconnected camera"
