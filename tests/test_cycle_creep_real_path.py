"""Cycle-time creep (K12) through the real Capture -> Detect -> Judge path.

``tests/test_cycle_creep.py`` proves the slope rule on hand-built observations; this
file proves the rule is *reachable*: a rendered clip whose tool-motion runs lengthen
run after run goes through the real :class:`Capture` and :class:`Detector` (so the
``motion`` / ``no_motion`` rows come from :class:`MotionTracker`, not a fixture),
and the real :class:`Judge` raises ``cycle_time_creep`` on the station zone. A clip
of equal-length runs, through the same path, raises none.

The clock is tied to the frame count (one second per frame read) so step durations
are exact and the test never depends on wall-clock pacing.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

from station_watch.capture import BlindThresholds, Capture, FrameSource
from station_watch.clock import offset_iso
from station_watch.config import StationConfig
from station_watch.detect.detector import Detector
from station_watch.judge import Judge
from station_watch.log import Log
from station_watch.records import FaultKind, ObservationKind
from station_watch.steps import step_durations

sys.path.insert(0, str(Path(__file__).parent))
from helpers.records import HEALTH_CONFIG  # noqa: E402
from helpers.synth_station import write_synth_station_clip  # noqa: E402

EPOCH = "2026-09-28T00:00:00.000000+00:00"
RUN = "run-creep-real-path"
ZONE = {
    "id": "bench",
    "region": [[0.3, 1.5], [5.5, 1.5], [5.5, 3.5], [0.3, 3.5]],
    "track_motion": True,
}
STILL_FRAMES = 5


def _config() -> StationConfig:
    data = copy.deepcopy(HEALTH_CONFIG)
    data["liveness_window_s"] = 10_000.0
    data["fiducial"]["expected_center_px"] = [58, 58]
    data["detect"].update(
        persistence_frames=2,
        station_zone=ZONE,
        slope={"window_steps": 5, "min_s_per_step": 2.0},
    )
    return StationConfig.from_mapping(data)  # takt 30 + grace 5 -> 35 s stall window


class _FrameClock:
    """A FrameSource whose reads advance a clock by one second per frame."""

    def __init__(self, clip) -> None:
        self.source = FrameSource(str(clip))
        self.frames_read = 0

    def read(self):
        frame = self.source.read()
        if frame is not None:
            self.frames_read += 1
        return frame

    def now(self) -> str:
        return offset_iso(EPOCH, float(self.frames_read))

    def __getattr__(self, name):
        return getattr(self.source, name)


def _script(run_lengths):
    script = [{} for _ in range(STILL_FRAMES)]
    for length in run_lengths:
        script += [{"motion": True} for _ in range(length)]
        script += [{} for _ in range(STILL_FRAMES)]
    return script


def _capture_and_judge(tmp_path, run_lengths):
    config = _config()
    clip, _truth = write_synth_station_clip(
        tmp_path / "clip", _script(run_lengths), station_zone=ZONE, fps=20.0
    )
    source = _FrameClock(clip)
    log = Log(tmp_path / "creep.db")
    Capture(
        source,
        station_id=config.station_id,
        camera_id=config.camera_id,
        run_id=RUN,
        sleep=lambda _s: None,
        detector=Detector(config, RUN),
        clock=source.now,
    ).run(log, BlindThresholds.from_station_config(config))
    verdict = Judge(config, run_id=RUN).judge(log, source.now(), stream_ended=True)
    observations = log.since(EPOCH, ["obs"])
    return verdict, observations


def test_lengthening_motion_runs_raise_creep_through_the_real_detector(tmp_path):
    verdict, observations = _capture_and_judge(tmp_path, [4, 7, 10, 13, 16])

    kinds = {o.kind for o in observations}
    assert {ObservationKind.MOTION, ObservationKind.NO_MOTION} <= kinds, "MotionTracker emitted"
    durations = [s.duration_s for s in step_durations(observations)]
    assert len(durations) == 5, durations
    assert durations == sorted(durations), durations  # every step longer than the last

    creep = [f for f in verdict.faults if f.kind is FaultKind.CYCLE_TIME_CREEP]
    assert creep, verdict
    assert creep[0].target == "bench"
    assert set(creep[0].frame_ids) <= {o.frame_id for o in observations}
    assert not any(f.kind is FaultKind.STALLED for f in verdict.faults)


def test_equal_motion_runs_raise_no_creep_through_the_real_detector(tmp_path):
    verdict, observations = _capture_and_judge(tmp_path, [8, 8, 8, 8, 8])

    assert len(step_durations(observations)) == 5
    assert not any(f.kind is FaultKind.CYCLE_TIME_CREEP for f in verdict.faults), verdict
