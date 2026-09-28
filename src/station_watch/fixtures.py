"""Load fixture observation sequences (JSONL) and rebase them onto a run clock.

Fixture rows carry ``t_offset_s`` (seconds from the start of a run) instead of an
absolute ``ts``, plus ``method: "fixture"``. The loader rebases each row to
``ts = run_start_ts + t_offset_s`` so fixture observations and live Capture
records share one clock. Lines beginning with ``#`` are README comments
describing the scenario and expected Judge verdict; they are skipped.
"""

from __future__ import annotations

import json
from pathlib import Path

from station_watch.clock import offset_iso
from station_watch.records import Observation, ObservationKind


def load_fixture_observations(
    path: str | Path, run_start_ts: str, run_id: str
) -> list[Observation]:
    """Parse a JSONL fixture file into :class:`Observation` records on the run clock."""
    observations: list[Observation] = []
    for line in Path(path).read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        row = json.loads(stripped)
        observations.append(_row_to_observation(row, run_start_ts, run_id))
    return observations


def _row_to_observation(row: dict, run_start_ts: str, run_id: str) -> Observation:
    return Observation(
        station_id=row["station_id"],
        frame_id=row["frame_id"],
        ts=offset_iso(run_start_ts, row["t_offset_s"]),
        kind=ObservationKind(row["kind"]),
        target=row["target"],
        method=row.get("method", "fixture"),
        confidence_ceiling=row["confidence_ceiling"],
        detector_output=row.get("detector_output", {}),
        run_id=run_id,
    )
