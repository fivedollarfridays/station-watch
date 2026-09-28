"""Durability, dedup and ordering tests for the append-only Log (component 4)."""

import subprocess
import sys
import threading
from pathlib import Path

import pytest

from station_watch.clock import offset_iso
from station_watch.log import Log, LogError
from station_watch.records import (
    BlindReason,
    BlindRecord,
    BlindState,
    CycleCompleted,
)

# Import the killable child's record factory so the proving test can rebuild the
# exact records it produced (same run_id + record_id).
sys.path.insert(0, str(Path(__file__).parent))
from _log_child import cycle_record  # noqa: E402

BASE = "2026-09-28T00:00:00.000000+00:00"
STAGES = ("capture", "judge", "alarm")


def cycle(run_id, n, ts=None):
    return CycleCompleted(
        ts=ts if ts is not None else offset_iso(BASE, n),
        cycle=n,
        stages=STAGES,
        run_id=run_id,
    )


def blind(run_id, seq, camera_id="CAM1", reason=BlindReason.FROZEN):
    return BlindRecord(
        station_id="ST1",
        camera_id=camera_id,
        ts=offset_iso(BASE, seq),
        reason=reason,
        evidence={"n": seq},
        last_good_frame_id=None,
        state=BlindState.OPENED,
        seq=seq,
        run_id=run_id,
    )


# --- K9: missing directory ---------------------------------------------------


def test_opening_log_in_missing_dir_raises_naming_the_path(tmp_path):
    missing = tmp_path / "does_not_exist" / "log.db"
    with pytest.raises(LogError) as excinfo:
        Log(str(missing))
    assert str(missing.parent) in str(excinfo.value)


# --- append / newest / dedup -------------------------------------------------


def test_append_then_newest_returns_latest_by_ts(tmp_path):
    log = Log(str(tmp_path / "log.db"))
    for n in range(3):
        log.append(cycle("run-A", n))
    assert log.newest("cycle") == cycle("run-A", 2)
    log.close()


def test_replaying_identical_record_is_ignored_not_duplicated(tmp_path):
    log = Log(str(tmp_path / "log.db"))
    rec = cycle("run-A", 0)
    log.append(rec)
    log.append(rec)  # exact replay, same record_id
    assert len(log.since(BASE, ["cycle"])) == 1
    log.close()


def test_newest_matches_on_fields(tmp_path):
    log = Log(str(tmp_path / "log.db"))
    log.append(blind("run-A", 0, camera_id="CAM1", reason=BlindReason.FROZEN))
    log.append(blind("run-A", 1, camera_id="CAM2", reason=BlindReason.DARK))
    log.append(blind("run-A", 2, camera_id="CAM1", reason=BlindReason.DARK))
    got = log.newest("blind", camera_id="CAM1", reason=BlindReason.DARK)
    assert got == blind("run-A", 2, camera_id="CAM1", reason=BlindReason.DARK)
    # a match with no such row is None
    assert log.newest("blind", camera_id="CAM9") is None
    log.close()


def test_newest_returns_none_for_absent_kind(tmp_path):
    log = Log(str(tmp_path / "log.db"))
    assert log.newest("cycle") is None
    log.close()


# --- AC3: ordering by recorded ts, not insert order --------------------------


def test_newest_and_since_order_by_ts_not_insert_order(tmp_path):
    log = Log(str(tmp_path / "log.db"))
    # insert out of chronological order: ts for 3, then 1, then 2
    log.append(cycle("run-A", 3))
    log.append(cycle("run-A", 1))
    log.append(cycle("run-A", 2))
    # AC uses the friendly alias "cycle_completed" for the same rows
    assert log.newest("cycle_completed") == cycle("run-A", 3)
    rows = log.since(BASE, ["cycle_completed"])
    assert [r.cycle for r in rows] == [1, 2, 3]
    log.close()


def test_since_is_inclusive_and_filters_by_kind(tmp_path):
    log = Log(str(tmp_path / "log.db"))
    log.append(cycle("run-A", 0))
    log.append(cycle("run-A", 1))
    log.append(blind("run-A", 5))
    from_one = log.since(offset_iso(BASE, 1), ["cycle"])
    assert [r.cycle for r in from_one] == [1]
    assert log.since(BASE, []) == []
    log.close()


# --- AC2: a restarted writer's rows are all kept -----------------------------


def test_restarted_writer_from_zero_keeps_all_rows(tmp_path):
    log = Log(str(tmp_path / "log.db"))
    for n in range(3):
        log.append(cycle("run-A", n))
    # process restart: new run_id, counters back at zero
    for n in range(3):
        log.append(cycle("run-B", n))
    rows = log.since(BASE, ["cycle"])
    assert len(rows) == 6
    assert {r.run_id for r in rows} == {"run-A", "run-B"}
    log.close()


# --- single-writer lock: concurrent appends are serialized -------------------


def test_concurrent_appends_are_serialized(tmp_path):
    log = Log(str(tmp_path / "log.db"))

    def writer(run_id):
        for n in range(50):
            log.append(cycle(run_id, n))

    threads = [threading.Thread(target=writer, args=(f"run-{i}",)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(log.since(BASE, ["cycle"])) == 150
    log.close()


# --- AC1: SIGKILL mid-write, reopen, replay ----------------------------------


def test_sigkill_midwrite_reopen_then_replay_has_no_duplicates(tmp_path):
    db = tmp_path / "log.db"
    run_id = "run-child-A"
    child = Path(__file__).parent / "_log_child.py"
    proc = subprocess.Popen(
        [sys.executable, str(child), str(db), run_id, BASE, "5000000"]
    )
    try:
        # let it commit a batch, then hard-kill mid-loop
        proc.wait(timeout=0.6)
    except subprocess.TimeoutExpired:
        pass
    proc.kill()  # SIGKILL on POSIX
    proc.wait()

    log = Log(str(db))
    rows = log.since(BASE, ["cycle"])
    assert rows, "expected a committed prefix to survive the kill"
    cycles = [r.cycle for r in rows]
    last = cycles[-1]
    # committed prefix is contiguous 0..last and the newest row is intact
    assert cycles == list(range(last + 1))
    expected_newest = cycle_record(BASE, run_id, last)
    assert log.newest("cycle") == expected_newest
    committed = len(rows)

    # replay the exact records the child produced -> deduped, zero new rows
    for n in range(last + 1):
        log.append(cycle_record(BASE, run_id, n))
    after = log.since(BASE, ["cycle"])
    assert len(after) == committed
    assert log.newest("cycle") == expected_newest
    log.close()
