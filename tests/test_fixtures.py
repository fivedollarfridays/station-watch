"""Tests for loading fixture observation sequences and rebasing their timestamps."""

from pathlib import Path

import pytest

from station_watch.clock import parse_iso
from station_watch.fixtures import load_fixture_observations
from station_watch.records import Observation

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "observations"
RUN_START = "2026-09-28T18:00:00.000000+00:00"
RUN_ID = "run-fixture-test"

FIXTURE_FILES = [
    "normal_cycles.jsonl",
    "stall.jsonl",
    "missing_part.jsonl",
    "keepout_entry.jsonl",
]


def test_all_four_fixture_files_exist():
    for name in FIXTURE_FILES:
        assert (FIXTURES / name).is_file(), f"missing fixture {name}"


@pytest.mark.parametrize("name", FIXTURE_FILES)
def test_loader_produces_observations(name):
    obs = load_fixture_observations(FIXTURES / name, RUN_START, RUN_ID)
    assert obs, f"{name} yielded no observations"
    assert all(isinstance(o, Observation) for o in obs)
    assert all(o.run_id == RUN_ID for o in obs)
    assert all(o.method == "fixture" for o in obs)


@pytest.mark.parametrize("name", FIXTURE_FILES)
def test_ts_equals_run_start_plus_offset(name):
    path = FIXTURES / name
    obs = load_fixture_observations(path, RUN_START, RUN_ID)
    offsets = _raw_offsets(path)
    base = parse_iso(RUN_START)
    assert len(obs) == len(offsets)
    for o, offset in zip(obs, offsets, strict=True):
        delta = (parse_iso(o.ts) - base).total_seconds()
        assert delta == pytest.approx(offset)


def test_comment_lines_are_skipped():
    # Every fixture starts with a '#' README line describing the scenario.
    for name in FIXTURE_FILES:
        first = (FIXTURES / name).read_text().splitlines()[0]
        assert first.startswith("#")


def _raw_offsets(path):
    import json

    offsets = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        offsets.append(json.loads(line)["t_offset_s"])
    return offsets
