"""Rail-position detection proves itself on real synthetic clips, end to end.

Every test renders a clip with the synthetic station helper, reads it back through
``cv2.VideoCapture`` (the real file path), finds the marker, and drives
``PositionTracker`` exactly as HF2.3's ``Detector`` will. The clip's layout is the
tracker's config, so a drawn component and the region it is read from are the same
marker-unit place -- which is why a bumped (shifted) marker still maps correctly.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2

from station_watch.clock import offset_iso, utc_now_iso
from station_watch.config import StationConfig
from station_watch.detect.positions import PositionTracker, read_positions
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
STATION_ZONE = {"id": "bench", "region": [[0.3, 1.5], [5.5, 1.5], [5.5, 3.5], [0.3, 3.5]]}
PERSISTENCE = 3


def _config(**detect_overrides) -> StationConfig:
    detect = {
        "persistence_frames": PERSISTENCE,
        "emit_interval_s": 10_000.0,  # large: periodic re-emit never fires unless a test wants it
        "rail_positions": RAIL,
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
            "required_slots": list(RAIL),
            "keepout_zones": list(KEEPOUT),
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
                "expected_center_px": [40, 40],
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
    """Render, read back, and drive a tracker exactly as the Detector will."""
    from station_watch.detect.geometry import find_marker_corners

    config = config or _config()
    render_opts.setdefault("rail_positions", RAIL)
    render_opts.setdefault("keepout_rois", KEEPOUT)
    render_opts.setdefault("station_zone", STATION_ZONE)
    path, truth = write_synth_station_clip(tmp_path / "clip", script, **render_opts)
    frames = _read_frames(path)
    assert len(frames) == len(script)
    base = utc_now_iso()
    tracker = PositionTracker(config, run_id="run-test")
    fid = config.fiducial
    observations = []
    for frame_id, frame in enumerate(frames):
        corners = find_marker_corners(frame, fid["dictionary_id"], fid["marker_id"])
        ts = offset_iso(base, frame_id * 0.1)
        observations.extend(tracker.update(frame, frame_id, ts, corners))
    return observations, truth, frames


def _for(observations, target):
    return [o for o in observations if o.target == target]


# --- AC1: proving test -- present reads part_present only after N; empty part_absent


def test_proving_present_after_n_frames_and_empty_absent(tmp_path):
    script = [{"positions": {"rail_pos_1": "present", "rail_pos_2": "absent"}} for _ in range(5)]
    observations, _truth, frames = _run_clip(tmp_path, script)

    present = _for(observations, "rail_pos_1")
    assert len(present) == 1, "one emission on the single state change"
    assert present[0].kind is ObservationKind.PART_PRESENT
    # Only after N frames: the Nth frame (index N-1) is the first that confirms.
    assert present[0].frame_id == PERSISTENCE - 1
    streak = present[0].detector_output["streak_frame_ids"]
    assert streak == [0, 1, 2]
    assert all(0 <= f < len(frames) for f in streak), "cites frame ids that exist in the clip"
    assert present[0].method != "fixture" and "rail_positions" in present[0].method

    absent = _for(observations, "rail_pos_2")
    assert absent[0].kind is ObservationKind.PART_ABSENT
    assert absent[0].detector_output["cause"] == "empty"
    assert absent[0].frame_id == PERSISTENCE - 1


# --- AC2: a hand covering a filled position never yields part_present while covered


def test_hand_cover_yields_unknown_then_present_only_after_n_clean(tmp_path):
    script = (
        [{"positions": {"rail_pos_1": "present"}} for _ in range(PERSISTENCE)]  # 0,1,2 -> present
        + [{"positions": {"rail_pos_1": "occluded"}} for _ in range(2)]  # 3,4 cover (< N)
        + [{"positions": {"rail_pos_1": "present"}} for _ in range(PERSISTENCE)]  # 5,6,7 clean
    )
    observations, _truth, _frames = _run_clip(tmp_path, script)
    present = _for(observations, "rail_pos_1")

    kinds = [(o.frame_id, o.kind) for o in present]
    assert kinds == [
        (PERSISTENCE - 1, ObservationKind.PART_PRESENT),  # 2: first confirmation
        (PERSISTENCE, ObservationKind.PART_UNKNOWN),  # 3: cover resets immediately
        (len(script) - 1, ObservationKind.PART_PRESENT),  # 7: present only after N clean
    ]
    cover_frames = {3, 4}
    for obs in present:
        if obs.kind is ObservationKind.PART_PRESENT:
            assert obs.frame_id not in cover_frames, "never part_present while covered"
    unknown = next(o for o in present if o.kind is ObservationKind.PART_UNKNOWN)
    assert unknown.detector_output["cause"] == "occluded"


# --- AC3: marker hidden -> every position part_unknown with cause fiducial_missing


def test_marker_hidden_reads_fiducial_missing_for_every_position(tmp_path):
    script = [{"hide_marker": True} for _ in range(2)]
    observations, _truth, _frames = _run_clip(tmp_path, script)

    first = {o.target: o for o in observations if o.frame_id == 0}
    assert set(first) == set(RAIL)
    for obs in first.values():
        assert obs.kind is ObservationKind.PART_UNKNOWN
        assert obs.detector_output["cause"] == "fiducial_missing"


# --- AC4: off-seat beyond tolerance -> part_absent not_seated; within -> part_present


def test_not_seated_beyond_tolerance_is_absent_within_is_present(tmp_path):
    beyond = [{"positions": {"rail_pos_1": "not_seated"}} for _ in range(PERSISTENCE)]
    observations, _t, _f = _run_clip(tmp_path / "beyond", beyond)
    absent = _for(observations, "rail_pos_1")
    assert absent[0].kind is ObservationKind.PART_ABSENT
    assert absent[0].detector_output["cause"] == "not_seated"

    within = [{"positions": {"rail_pos_1": "present"}} for _ in range(PERSISTENCE)]
    observations2, _t2, _f2 = _run_clip(tmp_path / "within", within)
    present = _for(observations2, "rail_pos_1")
    assert present[0].kind is ObservationKind.PART_PRESENT


# --- AC5: blurred/dim frames lower confidence_ceiling vs sharp frames of same scene


def test_blur_and_dim_lower_confidence_ceiling(tmp_path):
    config = _config()

    def ceiling(spec):
        path, _truth = write_synth_station_clip(
            tmp_path / spec.get("tag", "c"),
            [spec],
            rail_positions=RAIL,
            keepout_rois=KEEPOUT,
            station_zone=STATION_ZONE,
        )
        frame = _read_frames(path)[0]
        reading = next(r for r in read_positions(frame, 0, "t", config) if r.target == "rail_pos_1")
        return reading.confidence_ceiling

    sharp = ceiling({"positions": {"rail_pos_1": "present"}, "tag": "sharp"})
    blurred = ceiling({"positions": {"rail_pos_1": "present"}, "blur": True, "tag": "blur"})
    dim = ceiling({"positions": {"rail_pos_1": "present"}, "dim": True, "tag": "dim"})

    assert blurred < sharp, (blurred, sharp)
    assert dim < sharp, (dim, sharp)


# --- AC6: camera bumped within fiducial.tolerance_px still maps regions correctly


def test_shifted_marker_within_tolerance_still_maps_regions(tmp_path):
    # The whole scene (marker + components) shifts 8px (< tolerance_px 10). Because
    # regions follow the marker, the components still land in their read regions.
    script = [
        {"positions": {"rail_pos_1": "present", "rail_pos_2": "present"}}
        for _ in range(PERSISTENCE)
    ]
    observations, _truth, _frames = _run_clip(tmp_path, script, marker_xy=(38, 30))

    for target in RAIL:
        emitted = _for(observations, target)
        assert emitted and emitted[0].kind is ObservationKind.PART_PRESENT, target


# --- re-emit: the current state is re-emitted every emit_interval_s


def test_current_state_re_emitted_every_interval(tmp_path):
    config = _config(emit_interval_s=0.25)  # frames are 0.1s apart -> re-emit roughly every 3rd
    script = [{"positions": {"rail_pos_1": "present"}} for _ in range(8)]
    observations, _truth, _frames = _run_clip(tmp_path, script, config=config)
    present = _for(observations, "rail_pos_1")

    assert present[0].frame_id == PERSISTENCE - 1
    assert len(present) >= 2, "state re-emitted after the interval, not only on change"
    assert all(o.kind is ObservationKind.PART_PRESENT for o in present)
