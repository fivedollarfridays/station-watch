"""Station-zone motion proves itself on real synthetic clips, end to end.

Every test renders a clip with the synthetic station helper, reads it back through
``cv2.VideoCapture`` (the real file path), finds the marker, and drives
``MotionTracker`` exactly as HF2.3's ``Detector`` does -- the same tracker protocol
``update(frame, frame_id, ts, corners)`` / ``unknown_all(...)``. The point the
backlog calibrates against: a still-but-live zone (sensor noise only) must read
``no_motion``, never ``motion``, while a moving tool in the zone reads ``motion``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2

from station_watch.clock import offset_iso, utc_now_iso
from station_watch.config import StationConfig
from station_watch.detect.motion import MotionTracker
from station_watch.records import ObservationKind

sys.path.insert(0, str(Path(__file__).parent))
from helpers.synth_station import write_synth_station_clip  # noqa: E402

RAIL = {
    "rail_pos_1": [[0.7, 1.9], [1.7, 1.9], [1.7, 2.5], [0.7, 2.5]],
    "rail_pos_2": [[2.5, 1.9], [3.5, 1.9], [3.5, 2.5], [2.5, 2.5]],
}
KEEPOUT = {
    "zone_press": {"region": [[4.0, 1.9], [5.3, 1.9], [5.3, 3.0], [4.0, 3.0]], "active": True}
}
STATION_ZONE = {
    "id": "bench",
    "region": [[0.3, 1.5], [5.5, 1.5], [5.5, 3.5], [0.3, 3.5]],
    "track_motion": True,
}
PERSISTENCE = 2


def _config(*, rail_positions=None, **detect_overrides) -> StationConfig:
    rail_positions = RAIL if rail_positions is None else rail_positions
    detect = {
        "persistence_frames": PERSISTENCE,
        "emit_interval_s": 10_000.0,
        "rail_positions": rail_positions,
        "station_zone": STATION_ZONE,
        "keepout_rois": KEEPOUT,
        "blur_threshold": 100.0,
        "darkness_threshold": 40.0,
        "occlusion_threshold": 0.5,
    }
    detect.update(detect_overrides)
    return StationConfig.from_mapping(
        {
            "station_id": "station-1",
            "camera_id": "cam-0",
            "takt_s": 30.0,
            "grace_s": 5.0,
            "required_slots": list(rail_positions),
            "keepout_zones": [],
            "liveness_window_s": 3.0,
            "dark_luma_threshold": 15.0,
            "dark_window_s": 2.0,
            "frozen_frames": 30,
            "recover_good_frames": 5,
            "cycle_interval_s": 1.0,
            "recover_healthy_verdicts": 3,
            "fiducial": {
                "dictionary_id": "DICT_4X4_50",
                "marker_id": 0,
                "expected_center_px": [58, 58],
                "tolerance_px": 10,
            },
            "alarm": {"sinks": ["screen"]},
            "watchdog": {"cycle_window_s": 10.0, "alarm_eval_window_s": 10.0, "sinks": ["screen"]},
            "detect": detect,
        }
    )


def _read_frames(path: Path):
    cap = cv2.VideoCapture(str(path))
    assert cap.isOpened(), f"cv2.VideoCapture could not open {path}"
    frames = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()
    return frames


def _run_clip(tmp_path, script, config=None, **render_opts):
    from station_watch.detect.geometry import find_marker_corners

    config = config or _config()
    render_opts.setdefault("rail_positions", RAIL)
    render_opts.setdefault("keepout_rois", KEEPOUT)
    render_opts.setdefault("station_zone", STATION_ZONE)
    path, truth = write_synth_station_clip(tmp_path / "clip", script, **render_opts)
    frames = _read_frames(path)
    assert len(frames) == len(script)
    base = utc_now_iso()
    tracker = MotionTracker(config, run_id="run-test")
    fid = config.fiducial
    observations = []
    for frame_id, frame in enumerate(frames):
        corners = find_marker_corners(frame, fid["dictionary_id"], fid["marker_id"])
        ts = offset_iso(base, frame_id * 0.1)
        observations.extend(tracker.update(frame, frame_id, ts, corners))
    return observations, truth, frames


# --- the calibration point: still-but-live is no_motion, never motion --------


def test_still_live_zone_reads_no_motion_never_motion(tmp_path):
    script = [{} for _ in range(8)]  # no motion flag: sensor noise only
    observations, _truth, _frames = _run_clip(tmp_path, script)

    kinds = {o.kind for o in observations}
    assert observations, "a readable still zone still emits a confirmed no_motion"
    assert ObservationKind.MOTION not in kinds, "sensor noise must never read as motion"
    no_motion = [o for o in observations if o.kind is ObservationKind.NO_MOTION]
    assert no_motion
    assert no_motion[0].target == "bench"
    assert no_motion[0].method != "fixture" and "frame_diff" in no_motion[0].method


# --- marker jitter must never read as motion ---------------------------------


def test_marker_jitter_in_a_still_scene_reads_no_motion(tmp_path):
    # A still scene nudged 2 px every other frame (a marker/camera knock jitters the
    # whole image, marker and all): nothing on the bench actually moved. Comparing
    # the zone crop cut under each frame's own corners re-registers the content, so
    # the jitter reads no_motion -- a fixed-coordinate frame diff would read motion.
    import numpy as np

    from station_watch.detect.geometry import find_marker_corners

    config = _config()
    path, _truth = write_synth_station_clip(
        tmp_path / "still",
        [{}],
        rail_positions=RAIL,
        keepout_rois=KEEPOUT,
        station_zone=STATION_ZONE,
        noise_sigma=0.0,  # isolate the jitter: the only change between frames is the nudge
    )
    base_frame = _read_frames(path)[0]

    base = utc_now_iso()
    fid = config.fiducial
    tracker = MotionTracker(config, run_id="run-test")
    observations = []
    for frame_id in range(8):
        frame = base_frame if frame_id % 2 == 0 else np.roll(base_frame, 2, axis=1)
        corners = find_marker_corners(frame, fid["dictionary_id"], fid["marker_id"])
        ts = offset_iso(base, frame_id * 0.1)
        observations.extend(tracker.update(frame, frame_id, ts, corners))

    kinds = {o.kind for o in observations}
    assert ObservationKind.MOTION not in kinds, "marker jitter must never read as motion"
    assert ObservationKind.NO_MOTION in kinds, "a still jittering scene still reads no_motion"


# --- a moving tool in the zone reads motion ----------------------------------


def test_moving_tool_in_zone_reads_motion(tmp_path):
    script = [{"motion": True} for _ in range(8)]
    observations, _truth, _frames = _run_clip(tmp_path, script)

    motion = [o for o in observations if o.kind is ObservationKind.MOTION]
    assert motion, "a moving tool in the zone must read motion"
    assert motion[0].target == "bench"
    assert motion[0].detector_output["motion_fraction"] > 0.0


def test_motion_then_still_transitions_to_no_motion(tmp_path):
    script = [{"motion": True} for _ in range(5)] + [{} for _ in range(5)]
    observations, _truth, _frames = _run_clip(tmp_path, script)

    kinds = [o.kind for o in observations]
    assert ObservationKind.MOTION in kinds
    assert ObservationKind.NO_MOTION in kinds
    # motion is confirmed before no_motion in the stream
    first_motion = next(i for i, k in enumerate(kinds) if k is ObservationKind.MOTION)
    last_no_motion = max(i for i, k in enumerate(kinds) if k is ObservationKind.NO_MOTION)
    assert first_motion < last_no_motion


# --- unknown frames emit nothing for the zone (Judge's blind handling governs) -


def test_marker_hidden_emits_nothing_for_the_zone(tmp_path):
    script = [{"hide_marker": True} for _ in range(4)]
    observations, _truth, _frames = _run_clip(tmp_path, script)
    assert observations == [], "no marker -> no motion reading at all"


def test_dark_frames_emit_nothing_for_the_zone(tmp_path):
    script = [{"dim": True} for _ in range(4)]
    observations, _truth, _frames = _run_clip(tmp_path, script)
    assert observations == [], "a dark zone -> no motion reading at all"


def test_unknown_all_emits_nothing(tmp_path):
    tracker = MotionTracker(_config(), run_id="run-test")
    assert tracker.unknown_all(3, offset_iso(utc_now_iso(), 0.0), "detect_error", "boom") == []


def test_blind_gap_does_not_diff_across_it(tmp_path):
    # Still, then marker hidden, then still again: the pair spanning the gap must
    # not be scored (it would be a large spurious diff) -- only no_motion, ever.
    script = [{} for _ in range(3)] + [{"hide_marker": True}] + [{} for _ in range(3)]
    observations, _truth, _frames = _run_clip(tmp_path, script)
    assert all(o.kind is ObservationKind.NO_MOTION for o in observations)


# --- the Detector composes a MotionTracker when the zone tracks motion --------


def test_detect_targets_configured_counts_zone_motion():
    from station_watch.detect.detector import detect_targets_configured

    # No rail positions, but the zone tracks motion -> still a Detect target.
    zone_only = _config(rail_positions={}, station_zone=STATION_ZONE)
    assert detect_targets_configured(zone_only) is True

    off = _config(
        rail_positions={},
        station_zone={"id": "bench", "region": STATION_ZONE["region"]},  # no track_motion
    )
    assert detect_targets_configured(off) is False


def test_build_detector_composes_motion_tracker(tmp_path):
    from station_watch.detect.detector import build_detector

    config = _config(rail_positions={}, station_zone=STATION_ZONE)
    detector = build_detector(config, run_id="r")
    assert detector is not None

    path, _truth = write_synth_station_clip(
        tmp_path / "clip",
        [{"motion": True} for _ in range(6)],
        rail_positions=RAIL,
        keepout_rois=KEEPOUT,
        station_zone=STATION_ZONE,
    )
    out = []
    for frame_id, frame in enumerate(_read_frames(path)):
        out.extend(detector.process(frame, frame_id, offset_iso(utc_now_iso(), frame_id * 0.1)))
    assert any(o.kind is ObservationKind.MOTION and o.target == "bench" for o in out)
