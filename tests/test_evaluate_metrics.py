"""Exact, hand-countable tests of the evaluation metric math and extraction.

Every number here is computed by hand against a tiny set, so a regression in
precision/recall, the rail confusion matrix, or latency is caught precisely --
this is the proving test's foundation (AC1): the harness's numbers are only
trustworthy if the functions under them are provably right on inputs we can count.
"""

from __future__ import annotations

from station_watch.evaluate.extract import ClipOutcome, ClipRun, extract_outcome
from station_watch.evaluate.manifest import ClipLabel
from station_watch.evaluate.metrics import (
    flag_metrics,
    latency_metrics,
    precision_recall,
    rail_confusion,
    rail_state_metrics,
)
from station_watch.records import (
    Fault,
    FaultKind,
    FrameRecord,
    Observation,
    ObservationKind,
    Verdict,
    VerdictState,
)

RUN = "run-eval-test"


def _outcome(**over):
    base = dict(
        session="s1",
        gt_flags=set(),
        pred_flags=set(),
        rail_intervals=[],
        latencies=[],
        time_to_alarm=[],
    )
    base.update(over)
    return ClipOutcome(**base)


# --- precision / recall ---------------------------------------------------------


def test_precision_recall_basic():
    assert precision_recall(3, 1, 2) == (0.75, 0.6)


def test_precision_recall_is_none_when_denominator_zero():
    # no positive predictions -> precision undefined; no positive labels -> recall undefined
    assert precision_recall(0, 0, 0) == (None, None)
    assert precision_recall(0, 0, 5) == (None, 0.0)


def test_flag_metrics_counts_clips_per_flag():
    # three clips: a true missing_part, a false alarm, a miss.
    outcomes = [
        _outcome(gt_flags={"missing_part"}, pred_flags={"missing_part"}),  # tp
        _outcome(gt_flags=set(), pred_flags={"missing_part"}),  # fp
        _outcome(gt_flags={"missing_part"}, pred_flags=set()),  # fn (a miss)
    ]
    m = flag_metrics(outcomes)["missing_part"]
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (1, 1, 1, 0)
    assert m["precision"] == 0.5 and m["recall"] == 0.5
    # a flag with no instances at all reports None, None (honest about no evidence)
    assert flag_metrics(outcomes)["cycle_time_creep"]["precision"] is None


# --- rail-position confusion matrix (misses included) ---------------------------


def test_rail_confusion_and_per_state_metrics():
    outcomes = [
        _outcome(rail_intervals=[("present", "present"), ("absent", "absent")]),
        _outcome(rail_intervals=[("present", "absent"), ("unknown", "none")]),
    ]
    conf = rail_confusion(outcomes)
    assert conf["present"]["present"] == 1
    assert conf["present"]["absent"] == 1  # one present read as absent
    assert conf["unknown"]["none"] == 1  # a miss: no reading in the interval
    states = rail_state_metrics(conf)
    # present: tp=1, fn=1 (read absent) -> recall 0.5; fp=0 -> precision 1.0
    assert states["present"]["precision"] == 1.0 and states["present"]["recall"] == 0.5
    assert states["present"]["support"] == 2
    # unknown: never predicted correctly -> recall 0.0, precision None (never predicted)
    assert states["unknown"]["recall"] == 0.0 and states["unknown"]["precision"] is None


# --- latency percentiles --------------------------------------------------------


def test_latency_metrics_median_and_p95():
    lat = latency_metrics([0.1, 0.2, 0.3, 0.4, 0.5])
    assert lat["count"] == 5
    assert lat["median_s"] == 0.3
    assert lat["p95_s"] == 0.48  # linear interp: 0.4 + 0.8*(0.5-0.4)


def test_latency_metrics_empty():
    assert latency_metrics([]) == {"count": 0, "median_s": 0.0, "p95_s": 0.0}


# --- extraction from records ----------------------------------------------------


def _obs(target, kind, frame_id, ts):
    return Observation(
        station_id="station-1",
        frame_id=frame_id,
        ts=ts,
        kind=kind,
        target=target,
        method="m",
        confidence_ceiling=1.0,
        detector_output={},
        run_id=RUN,
    )


def _frame(frame_id, ts):
    return FrameRecord(
        station_id="station-1",
        camera_id="cam-0",
        frame_id=frame_id,
        ts=ts,
        capture_mono=0.0,
        fingerprint="f",
        mean_luma=100.0,
        noise_score=0.0,
        run_id=RUN,
    )


def _verdict(state, faults, ts, seq):
    return Verdict(
        station_id="station-1",
        ts=ts,
        state=state,
        faults=tuple(faults),
        blind_reasons=(),
        seq=seq,
        run_id=RUN,
    )


def test_extract_outcome_scores_a_clip_exactly():
    label = ClipLabel(
        session="s1",
        rel_path="s1/clip.mkv",
        clip_path=None,
        positions=[
            {"target": "rail_pos_1", "state": "absent", "start_frame": 0, "end_frame": 10},
            {"target": "rail_pos_2", "state": "present", "start_frame": 0, "end_frame": 10},
        ],
        camera_faults=[{"reason": "dark", "start_frame": 5}],
    )
    t0 = "2026-09-28T12:00:00.000000+00:00"
    t_obs = "2026-09-28T12:00:01.000000+00:00"
    t_fault = "2026-09-28T12:00:02.500000+00:00"
    t_alarm = "2026-09-28T12:00:03.000000+00:00"
    run = ClipRun(
        session="s1",
        rel_path="s1/clip.mkv",
        verdicts=[
            _verdict(
                VerdictState.FAULT,
                [Fault(FaultKind.MISSING_PART, "rail_pos_1", (2,))],
                t_fault,
                1,
            )
        ],
        observations=[
            _obs("rail_pos_1", ObservationKind.PART_ABSENT, 4, t_obs),
            _obs("rail_pos_2", ObservationKind.PART_PRESENT, 4, t_obs),
        ],
        alarms=[
            {"event": "alarm", "cause": "missing_part:rail_pos_1", "started_ts": t_fault},
            {"event": "alarm", "cause": "unobservable:dark", "started_ts": t_alarm},
        ],
        frames=[_frame(2, t0), _frame(5, t_obs)],
    )
    outcome = extract_outcome(label, run)

    assert outcome.gt_flags == {"missing_part"}
    assert outcome.pred_flags == {"missing_part"}  # unobservable is not a flag type
    assert set(outcome.rail_intervals) == {("absent", "absent"), ("present", "present")}
    # latency: fault at 12:00:02.5 cited frame 2 captured at 12:00:00 -> 2.5 s
    assert outcome.latencies == [2.5]
    # time to alarm: dark injected at frame 5 (captured 12:00:01); blind alarm at 12:00:03 -> 2.0 s
    assert outcome.time_to_alarm[0]["seconds"] == 2.0
    assert outcome.time_to_alarm[0]["reason"] == "dark"


def test_extract_marks_a_missing_reading_as_a_miss():
    label = ClipLabel(
        session="s1",
        rel_path="c",
        clip_path=None,
        positions=[{"target": "rail_pos_1", "state": "present", "start_frame": 0, "end_frame": 3}],
    )
    # the only reading is at frame 9, outside the labeled [0, 3] window -> a miss
    run = ClipRun(
        session="s1",
        rel_path="c",
        observations=[_obs("rail_pos_1", ObservationKind.PART_PRESENT, 9, "t")],
    )
    outcome = extract_outcome(label, run)
    assert outcome.rail_intervals == [("present", "none")]
