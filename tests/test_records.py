"""Round-trip, validation and idempotency-key tests for the shared record formats."""

import json
from dataclasses import replace

import pytest

from station_watch.records import (
    AlarmEvaluated,
    BlindReason,
    BlindRecord,
    BlindState,
    CycleCompleted,
    Fault,
    FaultKind,
    FrameRecord,
    Observation,
    ObservationKind,
    Verdict,
    VerdictState,
)
from station_watch.run import new_run_id

RUN = "run-fixed-0000"


def sample_frame(run_id=RUN, frame_id=7):
    return FrameRecord(
        station_id="ST1",
        camera_id="CAM1",
        frame_id=frame_id,
        ts="2026-09-28T18:23:10.259389+00:00",
        capture_mono=1234.5,
        fingerprint="abc123",
        mean_luma=120.0,
        noise_score=0.02,
        run_id=run_id,
    )


def sample_blind(run_id=RUN, seq=0):
    return BlindRecord(
        station_id="ST1",
        camera_id="CAM1",
        ts="2026-09-28T18:23:10.259389+00:00",
        reason=BlindReason.FROZEN,
        evidence={"identical_frame_count": 30},
        last_good_frame_id=6,
        state=BlindState.OPENED,
        seq=seq,
        run_id=run_id,
    )


def sample_observation(run_id=RUN, frame_id=7):
    return Observation(
        station_id="ST1",
        frame_id=frame_id,
        ts="2026-09-28T18:23:10.259389+00:00",
        kind=ObservationKind.PART_PRESENT,
        target="slot_a",
        method="fixture",
        confidence_ceiling=0.9,
        detector_output={"score": 0.9},
        run_id=run_id,
    )


def sample_verdict(run_id=RUN, seq=0):
    return Verdict(
        station_id="ST1",
        ts="2026-09-28T18:23:10.259389+00:00",
        state=VerdictState.FAULT,
        faults=(Fault(kind=FaultKind.MISSING_PART, target="slot_a", frame_ids=(7, 8)),),
        blind_reasons=(),
        seq=seq,
        run_id=run_id,
    )


def sample_cycle(run_id=RUN, cycle=3):
    return CycleCompleted(
        ts="2026-09-28T18:23:10.259389+00:00",
        cycle=cycle,
        stages=("capture", "judge", "alarm"),
        run_id=run_id,
    )


def sample_alarm(run_id=RUN, seq=0):
    return AlarmEvaluated(
        ts="2026-09-28T18:23:10.259389+00:00",
        seq=seq,
        open_episodes=("ep-1", "ep-2"),
        run_id=run_id,
    )


ALL_SAMPLES = [
    sample_frame,
    sample_blind,
    sample_observation,
    sample_verdict,
    sample_cycle,
    sample_alarm,
]


@pytest.mark.parametrize("factory", ALL_SAMPLES)
def test_round_trip_dict_and_json(factory):
    obj = factory()
    cls = type(obj)
    # dict round trip
    assert cls.from_dict(obj.to_dict()) == obj
    # JSON round trip
    restored = cls.from_json(obj.to_json())
    assert restored == obj
    # to_dict is JSON serializable and stable
    assert json.loads(json.dumps(obj.to_dict())) == obj.to_dict()


def test_record_ids_match_per_type_rule():
    assert sample_frame().record_id == f"{RUN}:frame:CAM1:7"
    assert sample_blind().record_id == f"{RUN}:blind:CAM1:frozen:opened:0"
    assert sample_observation().record_id == f"{RUN}:obs:ST1:7:part_present:slot_a"
    assert sample_verdict().record_id == f"{RUN}:verdict:ST1:0"
    assert sample_cycle().record_id == f"{RUN}:cycle:3"
    assert sample_alarm().record_id == f"{RUN}:alarm_eval:0"


def test_cycle_time_creep_fault_kind_round_trips():
    # K12 added cycle_time_creep; a verdict carrying it must round-trip unchanged.
    verdict = replace(
        sample_verdict(),
        faults=(Fault(kind=FaultKind.CYCLE_TIME_CREEP, target="bench", frame_ids=(1, 9)),),
    )
    restored = Verdict.from_json(verdict.to_json())
    assert restored == verdict
    assert restored.faults[0].kind is FaultKind.CYCLE_TIME_CREEP


def test_record_id_is_deterministic():
    assert sample_frame().record_id == sample_frame().record_id


def test_record_id_recomputed_from_replayed_dict():
    d = sample_frame().to_dict()
    d["record_id"] = "tampered"
    assert FrameRecord.from_dict(d).record_id == f"{RUN}:frame:CAM1:7"


@pytest.mark.parametrize(
    "cls,field,bad",
    [
        (BlindRecord, "reason", "not_a_reason"),
        (BlindRecord, "state", "not_a_state"),
        (Observation, "kind", "not_a_kind"),
        (Verdict, "state", "not_a_state"),
    ],
)
def test_unknown_enum_values_rejected(cls, field, bad):
    factory = {
        BlindRecord: sample_blind,
        Observation: sample_observation,
        Verdict: sample_verdict,
    }[cls]
    d = factory().to_dict()
    d[field] = bad
    with pytest.raises(ValueError):
        cls.from_dict(d)


def test_records_are_frozen():
    frame = sample_frame()
    with pytest.raises(AttributeError):
        frame.frame_id = 99


def test_two_runs_from_zero_produce_disjoint_record_ids():
    run_a = new_run_id()
    run_b = new_run_id()
    assert run_a != run_b

    def ids_for(run_id):
        return {
            sample_frame(run_id=run_id, frame_id=0).record_id,
            sample_blind(run_id=run_id, seq=0).record_id,
            sample_observation(run_id=run_id, frame_id=0).record_id,
            sample_verdict(run_id=run_id, seq=0).record_id,
            sample_cycle(run_id=run_id, cycle=0).record_id,
            sample_alarm(run_id=run_id, seq=0).record_id,
        }

    assert ids_for(run_a).isdisjoint(ids_for(run_b))


def test_new_run_id_is_uuid4_unique():
    ids = {new_run_id() for _ in range(1000)}
    assert len(ids) == 1000
