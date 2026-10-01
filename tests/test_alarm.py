"""Alarm tests: verdicts to episodes, on screen and (recorded) sound.

Every test drives the record sink, so CI never needs an audio device. The AC1
proving test composes the real Capture, Log, Judge and Alarm in-process: a fake
source is disconnected (the "pulled cable"), Capture writes the real
``BlindRecord``s, and the timeline is then judged tick by tick and fed to the
Alarm -- so what is asserted is the whole pipeline's shape, not a mocked verdict
stream (README K14). Fault episodes are driven from the committed fixtures
through the real Judge.
"""

import io
import json
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from station_watch.alarm import (
    Alarm,
    AlarmError,
    Episode,
    RecordSink,
    ScreenSink,
    SoundSink,
    build_sinks,
    default_tone_path,
    generate_tone_wav,
)
from station_watch.capture import BlindThresholds, Capture
from station_watch.clock import offset_iso, parse_iso, to_iso
from station_watch.config import load_station_config
from station_watch.fixtures import load_fixture_observations
from station_watch.judge import Judge
from station_watch.log import Log
from station_watch.records import BlindReason, BlindState

sys.path.insert(0, str(Path(__file__).parent))
from helpers.records import LiveCameraJudge  # noqa: E402
from test_capture_blind import _GapSource  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIG = Path(__file__).parent / "fixtures" / "station-slots.yaml"
FIXTURES = Path(__file__).parent / "fixtures" / "observations"
RUN = "run-alarm-test"
START = "2026-09-28T00:00:00.000000+00:00"
EPOCH = "0001-01-01T00:00:00.000000+00:00"


def _config(**over):
    return replace(load_station_config(CONFIG), **over)


def _record_alarm(config, path):
    return Alarm(config, run_id=RUN, sinks=[RecordSink(path)])


def _events(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def _load(log, name):
    for obs in load_fixture_observations(FIXTURES / name, START, RUN):
        log.append(obs)


def _at(offset_s):
    return offset_iso(START, offset_s)


# --- AC1: proving test, real Capture -> Log -> Judge -> Alarm ----------------


def _cable_thresholds():
    return BlindThresholds(
        liveness_window_s=0.2,
        dark_luma_threshold=15.0,
        dark_window_s=0.06,
        frozen_frames=1000,  # never trip frozen on the synthetic bright frames
        recover_good_frames=3,
        fiducial={
            "dictionary_id": "DICT_4X4_50",
            "marker_id": 0,
            "expected_center_px": [40, 40],
            "tolerance_px": 10,
            "window_s": 1000.0,  # never trip fiducial: no marker in these frames
        },
    )


def test_pulled_cable_alarms_once_and_recovers_once(tmp_path):
    # Empty slots/zones: a live camera judges healthy, a disconnected one is the
    # only thing that can go wrong -- isolating the "pulled cable" round trip.
    config = _config(required_slots=[], keepout_zones=[], recover_healthy_verdicts=2)
    log = Log(tmp_path / "cable.db")
    thresholds = _cable_thresholds()
    source = _GapSource(before=3, gap_s=0.35, after=6)
    capture = Capture(
        source,
        station_id=config.station_id,
        camera_id=config.camera_id,
        run_id=RUN,
    )
    source.on_end = capture.stop
    capture.run(log, thresholds, poll_interval=0.02)

    # Sanity: Capture really opened and cleared exactly one disconnected episode.
    blind = log.since(EPOCH, ["blind"])
    opened = [
        b for b in blind if b.reason == BlindReason.DISCONNECTED and b.state == BlindState.OPENED
    ]
    cleared = [
        b for b in blind if b.reason == BlindReason.DISCONNECTED and b.state == BlindState.CLEARED
    ]
    assert len(opened) == 1 and len(cleared) == 1

    # Replay the whole recorded timeline through the real Judge and Alarm.
    record_path = tmp_path / "alarm.jsonl"
    judge = Judge(config, run_id=RUN)
    alarm = _record_alarm(config, record_path)
    stamps = sorted(parse_iso(r.ts) for r in log.since(EPOCH, ["frame", "blind"]))
    tick = stamps[0]
    end = stamps[-1] + timedelta(seconds=1.0)
    while tick <= end:
        verdict = judge.judge(log, to_iso(tick))
        alarm.evaluate(verdict, log)
        tick += timedelta(seconds=0.05)

    events = _events(record_path)
    alarms = [e for e in events if e["event"] == "alarm"]
    recoveries = [e for e in events if e["event"] == "recovery"]
    assert len(alarms) == 1
    assert alarms[0]["cause"] == "unobservable:disconnected"
    assert len(recoveries) == 1
    assert recoveries[0]["cause"] == "unobservable:disconnected"


# --- AC2: fixture fault verdicts each open one citing episode, no duplicates -


@pytest.mark.parametrize(
    "fixture,offset,cause",
    [
        ("stall.jsonl", 45.0, "stalled:zone_press"),
        ("missing_part.jsonl", 30.0, "missing_part:rail_pos_1"),
        ("keepout_entry.jsonl", 30.0, "keepout_entry:zone_press"),
    ],
)
def test_each_fault_opens_one_citing_episode(tmp_path, fixture, offset, cause):
    config = _config()
    log = Log(tmp_path / "fault.db")
    _load(log, fixture)
    record_path = tmp_path / "alarm.jsonl"
    judge = LiveCameraJudge(config, RUN)
    alarm = _record_alarm(config, record_path)

    # Two identical evaluations at the same instant: the second must not duplicate.
    for _ in range(2):
        alarm.evaluate(judge.judge(log, _at(offset)), log)

    alarms = [e for e in _events(record_path) if e["event"] == "alarm"]
    assert len(alarms) == 1
    assert alarms[0]["cause"] == cause
    assert alarms[0]["frame_ids"], "the episode must cite the frames its fault came from"


# --- AC3: startup without a sink refuses to start, naming the sink -----------


def test_build_sinks_without_a_sink_names_the_missing_sink():
    for cfg in ({"sinks": []}, {}, {"sinks": None}):
        with pytest.raises(AlarmError) as excinfo:
            build_sinks(cfg)
        assert "sink" in str(excinfo.value).lower()


def test_alarm_refuses_an_empty_sink_list():
    with pytest.raises(AlarmError) as excinfo:
        Alarm(_config(), run_id=RUN, sinks=[])
    assert "sink" in str(excinfo.value).lower()


# --- AC4: an alarm_evaluated row after each evaluation -----------------------


def test_alarm_evaluated_row_after_each_evaluation(tmp_path):
    config = _config()
    log = Log(tmp_path / "eval.db")
    _load(log, "missing_part.jsonl")
    alarm = _record_alarm(config, tmp_path / "alarm.jsonl")
    judge = LiveCameraJudge(config, RUN)

    for offset in (10.0, 20.0, 30.0):
        alarm.evaluate(judge.judge(log, _at(offset)), log)

    rows = log.since(EPOCH, ["alarm_eval"])
    assert len(rows) == 3
    assert [r.seq for r in rows] == [1, 2, 3]
    # By 30s the missing part has opened an episode, named in open_episodes.
    assert rows[-1].open_episodes == ("missing_part:rail_pos_1",)


# --- sinks and tone ----------------------------------------------------------


def test_screen_sink_line_names_station_cause_start_and_frames():
    stream = io.StringIO()
    sink = ScreenSink(stream)
    episode = Episode(
        "stalled:zone_press", "fault", "station-1", "stalled zone_press", START, (30, 60)
    )
    sink.on_alarm(episode)
    sink.on_recovery(episode, _at(90.0))
    text = stream.getvalue()
    assert "station-1" in text
    assert "stalled zone_press" in text
    assert START in text
    assert "[30, 60]" in text
    assert "CLEAR" in text


def test_sound_sink_uses_afplay_on_macos_without_playing(tmp_path):
    calls = []
    tone = tmp_path / "tone.wav"
    sink = SoundSink(tone_path=tone, runner=calls.append, platform="darwin")
    episode = Episode("unobservable:frozen", "unobservable", "station-1", "frozen", START, ())
    sink.on_alarm(episode)
    assert calls and calls[0][0] == "afplay"
    assert tone.exists()  # the tone is generated on demand if absent


def test_sound_sink_rings_the_bell_when_no_player():
    bell = io.StringIO()
    sink = SoundSink(platform="sunos", bell=bell)
    sink.on_alarm(Episode("x", "fault", "s", "x", START, ()))
    assert "\a" in bell.getvalue()


def test_bundled_tone_is_a_readable_wav():
    import wave

    with wave.open(str(default_tone_path())) as wav:
        assert wav.getnframes() > 0
        assert wav.getnchannels() == 1


def test_generate_tone_writes_a_fresh_wav(tmp_path):
    import wave

    out = generate_tone_wav(tmp_path / "nested" / "t.wav")
    assert out.exists()
    with wave.open(str(out)) as wav:
        assert wav.getframerate() == 8000


def test_build_sinks_builds_the_named_pluggable_sinks(tmp_path):
    sinks = build_sinks(
        {"sinks": ["record", "screen", "sound"]},
        record_path=tmp_path / "r.jsonl",
        screen_stream=io.StringIO(),
    )
    assert [type(s).__name__ for s in sinks] == ["RecordSink", "ScreenSink", "SoundSink"]


def test_unobservable_without_a_named_reason_still_alarms(tmp_path):
    # A part_unknown slot judges unobservable with no blind reason -- it must
    # still alarm (K1: unobservable never folds into "no problems found").
    config = _config()
    log = Log(tmp_path / "unknown.db")
    _load(log, "normal_cycles.jsonl")
    from station_watch.records import Observation, ObservationKind

    log.append(
        Observation(
            station_id=config.station_id,
            frame_id=90,
            ts=_at(45.0),
            kind=ObservationKind.PART_UNKNOWN,
            target="rail_pos_1",
            method="fixture",
            confidence_ceiling=0.3,
            detector_output={},
            run_id=RUN,
        )
    )
    record_path = tmp_path / "alarm.jsonl"
    alarm = _record_alarm(config, record_path)
    alarm.evaluate(LiveCameraJudge(config, RUN).judge(log, _at(45.0)), log)

    alarms = [e for e in _events(record_path) if e["event"] == "alarm"]
    assert len(alarms) == 1
    assert alarms[0]["cause"] == "unobservable:unknown"
