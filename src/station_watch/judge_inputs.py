"""The Judge's incremental, run-scoped view of the Log.

Each cycle the Judge needs the current run's observations and the latest state of
each blind reason. Re-reading the whole Log every cycle would grow without bound
over a shift, so :class:`JudgeInputs` keeps a rowid cursor and folds in only the
rows appended since the last call (``Log.read_new``), scoped to one ``run_id`` --
a restarted process mints a new run, so a previous run that ended blind never
keeps the new one blind. Rows stamped later than the instant being judged are
held back until that instant arrives, so judging "as of" a past ts stays exact.
"""

from __future__ import annotations

from collections import defaultdict

from station_watch.records import BlindRecord, BlindState, Observation

_READ_KINDS = ("obs", "blind")


class JudgeInputs:
    """Observations by target and the newest record per blind reason, for one run."""

    def __init__(self, *, camera_id: str, run_id: str) -> None:
        self._camera_id = camera_id
        self._run_id = run_id
        self._cursor = 0
        self._pending: list = []
        self._latest_blind: dict = {}
        self.by_target: dict[str, list[Observation]] = defaultdict(list)

    def refresh(self, log, now: str) -> None:
        """Fold in rows appended since the last call whose ts is at or before ``now``."""
        self._cursor, fresh = log.read_new(self._cursor, _READ_KINDS, run_id=self._run_id)
        self._pending.extend(fresh)
        ready = [record for record in self._pending if record.ts <= now]
        self._pending = [record for record in self._pending if record.ts > now]
        for record in ready:
            if isinstance(record, Observation):
                self.by_target[record.target].append(record)
            elif isinstance(record, BlindRecord):
                self._fold_blind(record)

    def _fold_blind(self, record: BlindRecord) -> None:
        if record.camera_id != self._camera_id:
            return
        current = self._latest_blind.get(record.reason)
        if current is None or (record.ts, record.seq) >= (current.ts, current.seq):
            self._latest_blind[record.reason] = record

    def active_blind_reasons(self) -> set:
        """Reasons whose newest record (this run, this camera) is still opened."""
        return {r for r, rec in self._latest_blind.items() if rec.state == BlindState.OPENED}


__all__ = ["JudgeInputs"]
