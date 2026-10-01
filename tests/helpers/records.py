"""Small record and config builders shared by the Judge / Runner fix-round tests."""

from __future__ import annotations

from station_watch.config import StationConfig
from station_watch.records import BlindReason, BlindRecord, BlindState, FrameRecord

# A camera-health station: no slots or zones, so a live, fresh, unblinded camera
# judges healthy and the only things that can go wrong are the camera's own.
HEALTH_CONFIG = {
    "station_id": "station-1",
    "camera_id": "cam-0",
    "takt_s": 30.0,
    "grace_s": 5.0,
    "required_slots": [],
    "keepout_zones": [],
    "liveness_window_s": 2.0,
    "dark_luma_threshold": 15.0,
    "dark_window_s": 0.3,
    "frozen_frames": 10000,
    "recover_good_frames": 3,
    "cycle_interval_s": 0.1,
    "recover_healthy_verdicts": 2,
    "fiducial": {
        "dictionary_id": "DICT_4X4_50",
        "marker_id": 0,
        "expected_center_px": [40, 40],
        "tolerance_px": 10,
        "window_s": 10000.0,
    },
    "alarm": {"sinks": ["record"]},
    "watchdog": {"cycle_window_s": 10.0, "alarm_eval_window_s": 10.0, "sinks": ["record"]},
    "detect": {
        "persistence_frames": 3,
        "emit_interval_s": 5.0,
        "rail_positions": {},
        "station_zone": {"id": "bench", "region": [[-4, -3], [4, -3], [4, 3], [-4, 3]]},
        "keepout_rois": {},
        "blur_threshold": 100.0,
        "darkness_threshold": 40.0,
        "occlusion_threshold": 0.5,
    },
}


def health_config(**over) -> StationConfig:
    return StationConfig.from_mapping({**HEALTH_CONFIG, **over})


def frame_at(ts: str, run_id: str, frame_id: int = 0, config=None) -> FrameRecord:
    """A FrameRecord for the config's camera, stamped ``ts`` (a fresh frame)."""
    station = config.station_id if config is not None else HEALTH_CONFIG["station_id"]
    camera = config.camera_id if config is not None else HEALTH_CONFIG["camera_id"]
    return FrameRecord(
        station_id=station,
        camera_id=camera,
        frame_id=frame_id,
        ts=ts,
        capture_mono=float(frame_id),
        fingerprint=f"fp-{frame_id}",
        mean_luma=100.0,
        noise_score=1.0,
        run_id=run_id,
    )


def blind_at(ts: str, run_id: str, reason: BlindReason, state: BlindState, seq: int):
    return BlindRecord(
        station_id=HEALTH_CONFIG["station_id"],
        camera_id=HEALTH_CONFIG["camera_id"],
        ts=ts,
        reason=reason,
        evidence={},
        last_good_frame_id=None,
        state=state,
        seq=seq,
        run_id=run_id,
    )


class LiveCameraJudge:
    """A real Judge over a camera that is live at every judged instant.

    Fixture-observation tests exercise the Judge's fault and slot logic, not the
    camera: this appends one fresh frame stamped at the judged ``now`` before
    delegating, so K2 frame freshness is satisfied the way a live Capture would.
    """

    def __init__(self, config, run_id: str) -> None:
        from station_watch.judge import Judge

        self._config = config
        self._run_id = run_id
        self._judge = Judge(config, run_id=run_id)
        self._frames = 0

    def judge(self, log, now_ts: str):
        self._frames += 1
        log.append(frame_at(now_ts, self._run_id, 10_000 + self._frames, self._config))
        return self._judge.judge(log, now_ts)
