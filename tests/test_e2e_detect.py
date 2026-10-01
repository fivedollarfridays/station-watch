"""HF2.7: HF1's fixture scenarios, re-run on real Detect output, end to end (K14).

HF1 proved the Judge and Alarm on *fixture* observation rows. This re-proves the
same four scenarios on the *real* Detect path: a synthetic rail clip depicting each
scenario is driven through the real ``station-watch run`` (Capture + Detect + Judge
+ Alarm), and the verdict and the Alarm episodes are read back out of the Log and
the ``record`` sink -- reaching the same verdict state and the same fault kind, with
exactly one episode per root cause, that the matching fixture test reaches.

Two architectural facts shape the "same episode" claim:

* the ``stall`` scenario's no-motion is a *station-zone* reading in real Detect
  (:mod:`station_watch.detect.motion`), so its episode cites the station zone's id
  rather than the fixture's ``zone_press`` -- the same ``stalled`` fault kind, one
  episode, cited to the still frames;
* the ``keepout_entry`` scenario needs a person detector. The real backend is the
  Apache-2.0 YOLOX model, which never recognises a synthetic blob, so -- exactly as
  the keep-out model lifecycle tests do -- this runs the real Runner in-process with
  the injectable *fake* backend returning a scripted person box. Capture, Detect,
  the Judge and the Alarm are all real; only the person detector is faked.

The hand-pass grace rule itself (a brief ``part_unknown`` waits out
``detect.unknown_grace_s``) is proven deterministically against the real Judge and
Alarm in :mod:`tests.test_alarm`; here the four scenarios use a comfortably large
grace so a slot's start-up ``part_unknown`` never cries wolf before it is confirmed.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import yaml

from station_watch.alarm.sink import build_sinks
from station_watch.capture.blind import BlindThresholds
from station_watch.capture.source import FrameSource
from station_watch.config import load_station_config
from station_watch.detect.geometry import region_to_pixels
from station_watch.log import Log
from station_watch.records import BlindReason, FaultKind, VerdictState
from station_watch.run import new_run_id
from station_watch.runner.pipeline import Runner
from station_watch.runner.startup import RunContext, resolve_stall_window_or_fail

sys.path.insert(0, str(Path(__file__).parent))
from helpers.synth_station import write_synth_station_clip  # noqa: E402
from helpers.synth_video import _write_frames  # noqa: E402

EPOCH = "0001-01-01T00:00:00.000000+00:00"

RAIL = {
    "rail_pos_1": [[0.7, 1.9], [1.7, 1.9], [1.7, 2.5], [0.7, 2.5]],
    "rail_pos_2": [[2.5, 1.9], [3.5, 1.9], [3.5, 2.5], [2.5, 2.5]],
}
KEEPOUT = {
    "zone_press": {"region": [[4.0, 1.9], [5.3, 1.9], [5.3, 3.0], [4.0, 3.0]], "active": True}
}
STATION_ZONE = {"id": "bench", "region": [[0.3, 1.5], [5.5, 1.5], [5.5, 3.5], [0.3, 3.5]]}
# The synthetic bench marker: drawn at (30, 30), 56 px on a side -> centre (58, 58).
MARKER_CORNERS = np.array([[30, 30], [86, 30], [86, 86], [30, 86]], dtype=np.float32)

BASE = {
    "station_id": "station-1",
    "camera_id": "cam-0",
    "takt_s": 30.0,
    "grace_s": 5.0,
    "required_slots": [],
    "keepout_zones": [],
    "liveness_window_s": 2.0,
    "dark_luma_threshold": 15.0,
    "dark_window_s": 0.3,
    "frozen_frames": 10_000,
    "recover_good_frames": 3,
    "cycle_interval_s": 0.05,
    "recover_healthy_verdicts": 2,
    "fiducial": {
        "dictionary_id": "DICT_4X4_50",
        "marker_id": 0,
        "expected_center_px": [58, 58],
        "tolerance_px": 10,
        "window_s": 10_000.0,
    },
    "alarm": {"sinks": ["record"]},
    "watchdog": {"cycle_window_s": 1.0, "alarm_eval_window_s": 1.0, "sinks": ["record"]},
    "detect": {
        "persistence_frames": 2,
        "emit_interval_s": 5.0,
        "rail_positions": {},
        "station_zone": STATION_ZONE,
        "keepout_rois": {},
        "blur_threshold": 100.0,
        "darkness_threshold": 40.0,
        "occlusion_threshold": 0.5,
        "unknown_grace_s": 10.0,  # start-up part_unknown never cries wolf in a short clip
    },
}


def _write_config(path, **over):
    detect_over = over.pop("detect", {})
    cfg = {**BASE, **over, "detect": {**BASE["detect"], **detect_over}}
    Path(path).write_text(yaml.safe_dump(cfg))
    return path


def _run_cli(*args, timeout=120):
    return subprocess.run(
        [sys.executable, "-m", "station_watch", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _clip(tmp_path, name, script, **over):
    path, _truth = write_synth_station_clip(
        tmp_path / name,
        script,
        rail_positions=over.get("rail_positions", RAIL),
        keepout_rois=over.get("keepout_rois", KEEPOUT),
        station_zone=over.get("station_zone", STATION_ZONE),
        fps=over.get("fps", 20.0),
    )
    return path


def _rail_script(n, pos1="present", pos2="present"):
    return [{"positions": {"rail_pos_1": pos1, "rail_pos_2": pos2}} for _ in range(n)]


def _events(path):
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _alarms(record_path):
    return [e for e in _events(record_path) if e["event"] == "alarm"]


def _verdicts(logdb):
    with Log(logdb) as log:
        return log.since(EPOCH, ["verdict"])


def _run_scenario(tmp_path, clip, config):
    record = tmp_path / "alarm.jsonl"
    result = _run_cli(
        "run",
        "--config",
        str(config),
        "--source",
        str(clip),
        "--log",
        str(tmp_path / "log.db"),
        "--alarm-record",
        str(record),
    )
    assert result.returncode == 0, result.stderr
    return _verdicts(tmp_path / "log.db"), _alarms(record)


# --- AC1: the four HF1 scenarios, each on real Detect through the real run -------


def test_normal_cycles_run_is_healthy_and_opens_no_episode(tmp_path):
    # normal_cycles fixture -> HEALTHY, no episodes. Both rail positions filled.
    config = _write_config(
        tmp_path / "station.yaml", required_slots=list(RAIL), detect={"rail_positions": RAIL}
    )
    clip = _clip(tmp_path, "normal", _rail_script(60))

    verdicts, alarms = _run_scenario(tmp_path, clip, config)

    assert any(v.state is VerdictState.HEALTHY for v in verdicts), "the filled rail reads healthy"
    assert not any(v.state is VerdictState.FAULT for v in verdicts), "nothing is wrong"
    assert alarms == [], "a healthy run opens no episode"


def test_missing_part_run_faults_and_opens_one_episode(tmp_path):
    # missing_part fixture -> FAULT missing_part on the emptied slot, one episode.
    config = _write_config(
        tmp_path / "station.yaml", required_slots=list(RAIL), detect={"rail_positions": RAIL}
    )
    clip = _clip(tmp_path, "missing", _rail_script(60, pos1="absent"))

    verdicts, alarms = _run_scenario(tmp_path, clip, config)

    missing = [
        f
        for v in verdicts
        if v.state is VerdictState.FAULT
        for f in v.faults
        if f.kind is FaultKind.MISSING_PART and f.target == "rail_pos_1"
    ]
    assert missing, "the Judge raises missing_part from Detect's rows (read back from the Log)"
    assert any(f.frame_ids for f in missing), "the fault cites the frames it came from"
    assert [a["cause"] for a in alarms] == ["missing_part:rail_pos_1"], "exactly one episode"


def test_stall_run_faults_stalled_and_opens_one_episode(tmp_path):
    # stall fixture -> FAULT stalled, one episode. Real Detect reads no_motion on the
    # station zone, so the episode cites the zone id -- the same stalled fault kind.
    zone = {**STATION_ZONE, "track_motion": True}
    config = _write_config(
        tmp_path / "station.yaml",
        takt_s=0.5,
        grace_s=0.3,  # 0.8 s stall window, reached well inside the still tail
        detect={"rail_positions": {}, "station_zone": zone},
    )
    script = [{"motion": True} for _ in range(6)] + [{} for _ in range(50)]
    clip = _clip(tmp_path, "stall", script, rail_positions={}, station_zone=zone)

    verdicts, alarms = _run_scenario(tmp_path, clip, config)

    stalled = [
        f
        for v in verdicts
        if v.state is VerdictState.FAULT
        for f in v.faults
        if f.kind is FaultKind.STALLED
    ]
    assert stalled, "the no-motion run past takt+grace stalls"
    assert any(f.frame_ids for f in stalled), "the stall cites the still frames"
    stall_alarms = [a for a in alarms if a["cause"].startswith("stalled:")]
    assert len(stall_alarms) == 1, f"exactly one stall episode; got {[a['cause'] for a in alarms]}"
    assert stall_alarms[0]["cause"] == f"stalled:{zone['id']}"


class _EnteringBackend:
    """A fake keep-out backend: no one for the first N calls, then a person in the zone."""

    def __init__(self, person_box, clear_calls):
        self._box = person_box
        self._clear_calls = clear_calls
        self._calls = 0

    def detect_people(self, frame):
        self._calls += 1
        return [] if self._calls <= self._clear_calls else [self._box]


def _zone_person_box():
    poly = region_to_pixels(KEEPOUT["zone_press"]["region"], MARKER_CORNERS)
    x0, y0 = poly.min(axis=0)
    x1, y1 = poly.max(axis=0)
    return (float(x0), float(y0), float(x1), float(y1), 0.9)


def test_keepout_entry_run_faults_and_opens_one_episode(tmp_path):
    # keepout_entry fixture -> FAULT keepout_entry:zone_press, one episode. The real
    # Runner runs in-process with the injectable fake backend (the YOLOX model never
    # sees a synthetic person); Capture, Detect, the Judge and the Alarm are real.
    config = load_station_config(
        _write_config(
            tmp_path / "station.yaml",
            keepout_zones=["zone_press"],
            detect={"rail_positions": {}, "keepout_rois": KEEPOUT, "keepout": {"min_overlap": 0.1}},
        )
    )
    clip = _clip(tmp_path, "keepout", [{} for _ in range(16)], rail_positions={}, fps=30.0)
    record = tmp_path / "alarm.jsonl"
    context = RunContext(
        config=config,
        source=FrameSource(str(clip)),
        log=Log(tmp_path / "log.db"),
        sinks=build_sinks(config.alarm, record_path=record),
        thresholds=BlindThresholds.from_station_config(config),
        keepout_backend=_EnteringBackend(_zone_person_box(), clear_calls=5),
        stall_window_s=resolve_stall_window_or_fail(config)[0],
        stall_window_source="test",
    )
    runner = Runner(context, run_id=new_run_id(), speed=1000.0)
    try:
        runner.run()
    finally:
        context.log.close()
        context.source.release()

    keepout = [
        f
        for v in _verdicts(tmp_path / "log.db")
        if v.state is VerdictState.FAULT
        for f in v.faults
        if f.kind is FaultKind.KEEPOUT_ENTRY and f.target == "zone_press"
    ]
    assert keepout, "a person in the active zone raises keepout_entry (read back from the Log)"
    assert [a["cause"] for a in _alarms(record)] == ["keepout_entry:zone_press"], "one episode"


# --- AC2: a hand pass -- the grace rule, end to end on real Detect output -------
# The grace *threshold* is proven deterministically in tests/test_alarm.py
# (test_brief_* / test_long_*). This shows the real Detect path produces the
# part_unknown a hand pass is made of, and that it is never read as healthy.


def test_hand_over_a_filled_position_reads_unobservable_never_healthy(tmp_path):
    config = _write_config(
        tmp_path / "station.yaml", required_slots=list(RAIL), detect={"rail_positions": RAIL}
    )
    # rail_pos_1 filled, then a hand covers it (occluded) to the end of the clip.
    script = _rail_script(8) + _rail_script(20, pos1="occluded")
    clip = _clip(tmp_path, "handpass", script, fps=20.0)

    verdicts, _alarms_ = _run_scenario(tmp_path, clip, config)

    with Log(tmp_path / "log.db") as log:
        obs = log.since(EPOCH, ["obs"])
    unknown = [o for o in obs if o.kind.value == "part_unknown" and o.target == "rail_pos_1"]
    assert unknown, "the covered position reads part_unknown through real Detect"
    assert unknown[-1].detector_output.get("cause") == "occluded"
    # While covered the station is unobservable -- never folded into healthy (K1).
    assert any(v.state is VerdictState.UNOBSERVABLE for v in verdicts)


# --- AC3: a frozen clip opens its blind episode at once, no grace ---------------


def _read_frames(path):
    cap = cv2.VideoCapture(str(path))
    frames = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()
    return frames


def test_frozen_clip_opens_its_blind_episode_at_once_despite_grace(tmp_path):
    # unknown_grace_s is large, but a frozen sensor is a blind *reason*, not a bare
    # part_unknown: its episode opens immediately, exactly as in HF1.
    config = _write_config(
        tmp_path / "station.yaml",
        frozen_frames=3,
        required_slots=list(RAIL),
        detect={"rail_positions": RAIL, "unknown_grace_s": 10_000.0},
    )
    base = _clip(tmp_path, "src", _rail_script(6))
    frames = _read_frames(base)
    frozen = frames + [frames[-1].copy() for _ in range(20)]  # identical bytes -> frozen sensor
    frozen_dir = tmp_path / "frozen"
    frozen_dir.mkdir()
    clip = _write_frames(frozen_dir, frozen, 20.0)

    _verdicts_, alarms = _run_scenario(tmp_path, clip, config)

    with Log(tmp_path / "log.db") as log:
        blind = log.since(EPOCH, ["blind"])
    assert any(b.reason is BlindReason.FROZEN for b in blind), "Capture catches the frozen sensor"
    assert any(a["cause"] == "unobservable:frozen" for a in alarms), "the blind episode opened"


# --- AC4: the watchdog beside a Detect-enabled run, --stop-stage alarm ----------


def _run_popen(*args):
    return subprocess.Popen(
        [sys.executable, "-m", "station_watch", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _cycle_count(logdb):
    with Log(logdb) as log:
        return len(log.since(EPOCH, ["cycle"]))


def _wait_until_cycles(logdb, at_least, deadline_s=15.0):
    start = time.monotonic()
    while time.monotonic() - start < deadline_s:
        if logdb.exists() and _cycle_count(logdb) >= at_least:
            return True
        time.sleep(0.05)
    return False


def test_watchdog_fires_the_alarm_stage_beside_a_detect_enabled_run(tmp_path):
    config = _write_config(
        tmp_path / "station.yaml", required_slots=list(RAIL), detect={"rail_positions": RAIL}
    )
    clip = _clip(tmp_path, "clip", _rail_script(600), fps=50.0)
    logdb = tmp_path / "log.db"
    wd_record = tmp_path / "wd.jsonl"

    run = _run_popen(
        "run",
        "--config",
        str(config),
        "--source",
        str(clip),
        "--log",
        str(logdb),
        "--alarm-record",
        str(tmp_path / "run-alarm.jsonl"),
        "--stop-stage",
        "alarm",
        "--stop-stage-after",
        "5",
        "--max-cycles",
        "120",
    )
    try:
        assert _wait_until_cycles(logdb, 6), "the Detect-enabled run must be writing cycle rows"
        watchdog = _run_cli(
            "watchdog",
            "--config",
            str(config),
            "--log",
            str(logdb),
            "--alarm-record",
            str(wd_record),
            "--interval",
            "0.2",
            "--max-checks",
            "15",
        )
        assert watchdog.returncode == 0, watchdog.stderr
        causes = {a["cause"] for a in _alarms(wd_record)}
        assert "watchdog:alarm" in causes, f"the watchdog must name the alarm stage; got {causes}"
        assert run.poll() is None, "the run must still be alive (Capture + cycle loop keep going)"
    finally:
        run.terminate()
        run.wait(timeout=10)
