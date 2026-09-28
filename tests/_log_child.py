"""A killable writer used by the SIGKILL proving test in test_log.py.

Run as a script: ``python _log_child.py <db_path> <run_id> <base_ts> <count>``.
Appends ``count`` ``CycleCompleted`` rows with deterministic ids and timestamps
(``ts = base_ts + cycle`` seconds), committing each, so a parent can SIGKILL it
mid-loop and reopen the store to inspect the committed prefix. Named with a
leading underscore so pytest does not collect it.
"""

from __future__ import annotations

import sys

from station_watch.clock import offset_iso
from station_watch.log import Log
from station_watch.records import CycleCompleted

STAGES = ("capture", "judge", "alarm")


def cycle_record(base_ts: str, run_id: str, cycle: int) -> CycleCompleted:
    """The exact record the child writes for a given cycle number."""
    return CycleCompleted(
        ts=offset_iso(base_ts, cycle),
        cycle=cycle,
        stages=STAGES,
        run_id=run_id,
    )


def main() -> None:
    db_path, run_id, base_ts, count = (
        sys.argv[1],
        sys.argv[2],
        sys.argv[3],
        int(sys.argv[4]),
    )
    log = Log(db_path)
    for cycle in range(count):
        log.append(cycle_record(base_ts, run_id, cycle))


if __name__ == "__main__":
    main()
