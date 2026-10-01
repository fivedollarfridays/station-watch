"""Keep-out zone logic proves itself with an injected fake backend, end to end.

The proving test renders a real synthetic station clip (so the fiducial is found
and the zone polygon is real pixels), then drives :class:`KeepoutTracker` exactly as
the ``Detector`` will -- but with a *fake* backend returning scripted person boxes,
so the zone logic (overlap, N-frame persistence, the three kinds) is tested without
any model. Separate tests feed the emitted observations to the real Judge (K1: a
zone reads healthy only on a latest ``zone_clear``; ``zone_unknown`` or no reading
never does).
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2

from station_watch.clock import offset_iso
from station_watch.config import StationConfig
from station_watch.detect.geometry import find_marker_corners, region_to_pixels
from station_watch.detect.keepout import KeepoutTracker
from station_watch.log import Log
from station_watch.records import (
    FaultKind,
    Observation,
    ObservationKind,
    VerdictState,
)

sys.path.insert(0, str(Path(__file__).parent))
from helpers.records import LiveCameraJudge  # noqa: E402
from helpers.synth_station import write_synth_station_clip  # noqa: E402

KEEPOUT = {
    "zone_press": {"region": [[4.0, 1.9], [5.3, 1.9], [5.3, 3.0], [4.0, 3.0]], "active": True}
}
STATION_ZONE = {"id": "bench", "region": [[0.3, 1.5], [5.5, 1.5], [5.5, 3.5], [0.3, 3.5]]}
MARKER_CENTER = [58, 58]
PERSISTENCE = 3
RUN = "run-keepout-test"
START = "2026-09-28T00:00:00.000000+00:00"


class FakeBackend:
    """Returns the scripted person boxes for each successive ``detect_people`` call."""

    def __init__(self, scripts):
        self._scripts = scripts
        self._i = 0

    def detect_people(self, frame):
        boxes = self._scripts[self._i]
        self._i += 1
        return boxes


def _config(*, keepout_rois=None, keepout_zones=("zone_press",), **detect_over) -> StationConfig:
    detect = {
        "persistence_frames": PERSISTENCE,
        "emit_interval_s": 10_000.0,  # large: periodic re-emit never fires unless wanted
        "rail_positions": {},
        "station_zone": STATION_ZONE,
        "keepout_rois": KEEPOUT if keepout_rois is None else keepout_rois,
        "blur_threshold": 100.0,
        "darkness_threshold": 40.0,
        "occlusion_threshold": 0.5,
        "keepout": {"min_overlap": 0.1},
    }
    detect.update(detect_over)
    return StationConfig.from_mapping(
        {
            "station_id": "station-1",
            "camera_id": "cam-0",
            "takt_s": 30.0,
            "grace_s": 5.0,
            "required_slots": [],
            "keepout_zones": list(keepout_zones),
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
                "expected_center_px": MARKER_CENTER,
                "tolerance_px": 10,
            },
            "alarm": {"sinks": ["screen"]},
            "watchdog": {"cycle_window_s": 10.0, "alarm_eval_window_s": 10.0, "sinks": ["screen"]},
            "detect": detect,
        }
    )


def _clip_frames(tmp_path, n):
    path, _truth = write_synth_station_clip(
        tmp_path / "clip",
        [{} for _ in range(n)],
        rail_positions={},
        keepout_rois=KEEPOUT,
        station_zone=STATION_ZONE,
    )
    cap = cv2.VideoCapture(str(path))
    frames = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()
    assert len(frames) == n
    return frames


def _zone_box(corners, score=0.9):
    """A person box covering the keep-out zone (so overlap is ~1.0 > min_overlap)."""
    poly = region_to_pixels(KEEPOUT["zone_press"]["region"], corners)
    x0, y0 = poly.min(axis=0)
    x1, y1 = poly.max(axis=0)
    return (float(x0), float(y0), float(x1), float(y1), score)


def _drive(tmp_path, occupancy, config=None):
    """Drive a KeepoutTracker over ``occupancy`` (per-frame bool: a person in-zone)."""
    config = config or _config()
    frames = _clip_frames(tmp_path, len(occupancy))
    fid = config.fiducial
    corners0 = find_marker_corners(frames[0], fid["dictionary_id"], fid["marker_id"])
    scripts = [[_zone_box(corners0)] if occ else [] for occ in occupancy]
    tracker = KeepoutTracker(config, run_id=RUN, backend=FakeBackend(scripts))
    observations = []
    for frame_id, frame in enumerate(frames):
        corners = find_marker_corners(frame, fid["dictionary_id"], fid["marker_id"])
        ts = offset_iso(START, frame_id * 0.1)
        observations.extend(tracker.update(frame, frame_id, ts, corners))
    return observations


def _for(observations, kind):
    return [o for o in observations if o.kind is kind]


# --- AC1: entry -> person_in_keepout after N + keepout_entry fault; leaving -> zone_clear


def test_entry_after_n_frames_then_leaving_is_zone_clear_and_faults(tmp_path):
    # Frames 0,1,2 occupied (person_in_keepout confirmed at index N-1=2),
    # then 3,4,5 clear (zone_clear confirmed at index 5).
    occupancy = [True, True, True, False, False, False]
    observations = _drive(tmp_path, occupancy)

    entered = _for(observations, ObservationKind.PERSON_IN_KEEPOUT)
    assert len(entered) == 1, "one emission on the single confirmed entry"
    assert entered[0].frame_id == PERSISTENCE - 1
    assert entered[0].detector_output["streak_frame_ids"] == [0, 1, 2]
    assert "keepout" in entered[0].method and entered[0].method != "fixture"

    cleared = _for(observations, ObservationKind.ZONE_CLEAR)
    assert cleared and cleared[0].frame_id == 5, "zone_clear only after N consecutive clear"

    # Feed the emitted rows to the real Judge: the entry is a keepout_entry fault
    # citing the frame it was confirmed on.
    log = Log(tmp_path / "entry.db")
    for obs in observations:
        if obs.kind is ObservationKind.PERSON_IN_KEEPOUT:
            log.append(obs)
    verdict = LiveCameraJudge(_config(), RUN).judge(log, offset_iso(START, 0.3))
    assert verdict.state == VerdictState.FAULT
    fault = next(f for f in verdict.faults if f.kind is FaultKind.KEEPOUT_ENTRY)
    assert fault.target == "zone_press"
    assert set(fault.frame_ids) <= {0, 1, 2} and fault.frame_ids


def test_inactive_zone_never_faults(tmp_path):
    # A person stands in an inactive zone for many frames: it reads clear, never
    # person_in_keepout, so the Judge raises no keepout_entry fault.
    inactive = {"zone_press": {"region": KEEPOUT["zone_press"]["region"], "active": False}}
    config = _config(keepout_rois=inactive)
    observations = _drive(tmp_path, [True] * 5, config=config)

    assert _for(observations, ObservationKind.PERSON_IN_KEEPOUT) == []
    assert _for(observations, ObservationKind.ZONE_CLEAR), "an inactive zone reads clear"

    log = Log(tmp_path / "inactive.db")
    for obs in observations:
        log.append(obs)
    verdict = LiveCameraJudge(config, RUN).judge(log, offset_iso(START, 0.4))
    assert not any(f.kind is FaultKind.KEEPOUT_ENTRY for f in verdict.faults)


def test_marker_missing_reads_zone_unknown(tmp_path):
    # corners None (marker not found) -> zone_unknown immediately, cause fiducial_missing.
    config = _config()
    frames = _clip_frames(tmp_path, 2)
    tracker = KeepoutTracker(config, run_id=RUN, backend=FakeBackend([[], []]))
    out = []
    for frame_id, frame in enumerate(frames):
        out.extend(tracker.update(frame, frame_id, offset_iso(START, frame_id * 0.1), None))

    unknown = _for(out, ObservationKind.ZONE_UNKNOWN)
    assert unknown and unknown[0].detector_output["cause"] == "fiducial_missing"


def test_backend_error_reads_zone_unknown(tmp_path):
    class Boom:
        def detect_people(self, frame):
            raise RuntimeError("model blew up")

    config = _config()
    frames = _clip_frames(tmp_path, 1)
    fid = config.fiducial
    corners = find_marker_corners(frames[0], fid["dictionary_id"], fid["marker_id"])
    tracker = KeepoutTracker(config, run_id=RUN, backend=Boom())

    out = tracker.update(frames[0], 0, offset_iso(START, 0.0), corners)
    unknown = _for(out, ObservationKind.ZONE_UNKNOWN)
    assert unknown and unknown[0].detector_output["cause"] == "detector_error"
    assert "model blew up" in unknown[0].detector_output["scores"]["detail"]


# --- AC2: zone_unknown, or no reading at all, never yields healthy -------------


def _healthy_baseline_log(tmp_path, name):
    """A log where the camera is live and no slot is required -- healthy but for the zone."""
    return Log(tmp_path / name)


def test_zone_unknown_is_never_healthy(tmp_path):
    config = _config()
    log = _healthy_baseline_log(tmp_path, "unknown.db")
    log.append(
        Observation(
            station_id="station-1",
            frame_id=3,
            ts=offset_iso(START, 0.3),
            kind=ObservationKind.ZONE_UNKNOWN,
            target="zone_press",
            method="keepout:test",
            confidence_ceiling=0.0,
            detector_output={"cause": "fiducial_missing"},
            run_id=RUN,
        )
    )
    verdict = LiveCameraJudge(config, RUN).judge(log, offset_iso(START, 0.5))
    assert verdict.state == VerdictState.UNOBSERVABLE
    assert verdict.faults == ()


def test_zone_with_no_reading_is_never_healthy(tmp_path):
    config = _config()
    log = _healthy_baseline_log(tmp_path, "noread.db")
    verdict = LiveCameraJudge(config, RUN).judge(log, offset_iso(START, 0.5))
    assert verdict.state != VerdictState.HEALTHY


def test_zone_clear_reads_healthy(tmp_path):
    config = _config()
    log = _healthy_baseline_log(tmp_path, "clear.db")
    log.append(
        Observation(
            station_id="station-1",
            frame_id=3,
            ts=offset_iso(START, 0.3),
            kind=ObservationKind.ZONE_CLEAR,
            target="zone_press",
            method="keepout:test",
            confidence_ceiling=0.9,
            detector_output={"overlap": 0.0},
            run_id=RUN,
        )
    )
    verdict = LiveCameraJudge(config, RUN).judge(log, offset_iso(START, 0.5))
    assert verdict.state == VerdictState.HEALTHY
