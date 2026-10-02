"""The Board renders UNKNOWN (never OK) when a Log row cannot be decoded.

A row an older or drifted writer stored -- an unknown record kind, a body that
fails ``from_dict``, or bad JSON -- is wrapped by the reader as ``BoardLogError``
naming the record id, so ``build_view`` turns it into an UNKNOWN view rather than
letting the exception escape the operator's screen (HF3.2 (1)).

The read-only Board also renders the same view off a Log written before the
blind-reason index existed: it cannot add the index, so its open-blind-reason
lookup falls back to the generic scan and still reads the right row.
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

from station_watch.board import build_view, render_html, render_text
from station_watch.board import reader as reader_mod
from station_watch.clock import offset_iso
from station_watch.log import Log
from station_watch.records import BlindReason, BlindState, Verdict, VerdictState

sys.path.insert(0, str(Path(__file__).parent))
from helpers.records import blind_at, frame_at, health_config  # noqa: E402

EPOCH = "2026-09-28T00:00:00.000000+00:00"
RUN = "run-decode"


def _t(offset: float) -> str:
    return offset_iso(EPOCH, offset)


def _healthy_log(path: Path) -> Path:
    with Log(path) as log:
        log.append(frame_at(_t(0), RUN, 0))
        log.append(
            Verdict(
                station_id="station-1",
                ts=_t(0),
                state=VerdictState.HEALTHY,
                faults=(),
                blind_reasons=(),
                seq=1,
                run_id=RUN,
            )
        )
    return path


def _raw_insert(path: Path, record_id: str, kind: str, ts: str, body: str) -> None:
    """Append a row straight through sqlite, bypassing the record dataclasses."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "INSERT INTO records (record_id, kind, ts, run_id, body) VALUES (?, ?, ?, ?, ?)",
            (record_id, kind, ts, RUN, body),
        )
        conn.commit()
    finally:
        conn.close()


UNKNOWN_KIND_ID = f"{RUN}:mystery:7"
BAD_BODY_ID = f"{RUN}:verdict:99"


def _append_unknown_kind_row(path: Path) -> str:
    # A newest "verdict" column whose body record_id infix is an unknown kind:
    # _rebuild raises KeyError on the kind map lookup.
    body = f'{{"record_id": "{UNKNOWN_KIND_ID}", "ts": "{_t(10)}", "run_id": "{RUN}"}}'
    _raw_insert(path, UNKNOWN_KIND_ID, "verdict", _t(10), body)
    return UNKNOWN_KIND_ID


def _append_undecodable_body_row(path: Path) -> str:
    # A newest "verdict" whose body is missing Verdict's required fields:
    # Verdict.from_dict raises TypeError building the dataclass.
    body = f'{{"record_id": "{BAD_BODY_ID}", "ts": "{_t(10)}", "run_id": "{RUN}"}}'
    _raw_insert(path, BAD_BODY_ID, "verdict", _t(10), body)
    return BAD_BODY_ID


@pytest.mark.parametrize(
    "append_row, record_id",
    [
        (_append_unknown_kind_row, UNKNOWN_KIND_ID),
        (_append_undecodable_body_row, BAD_BODY_ID),
    ],
)
def test_an_undecodable_row_renders_unknown_naming_the_record(tmp_path, append_row, record_id):
    log_path = _healthy_log(tmp_path / "log.db")
    append_row(log_path)

    view = build_view(health_config(), log_path, now=_t(10.1))  # must not raise

    assert view.state == "UNKNOWN"
    assert view.ok is False
    assert record_id in view.state_detail
    text = render_text(view)
    html = render_html(view)
    assert record_id in text and "NOT OK" in text
    assert record_id in html


def test_undecodable_blind_row_names_the_record_not_crashes(tmp_path):
    # A drifted "blind" row is surfaced by the open-blind-reason lookup.
    log_path = _healthy_log(tmp_path / "log.db")
    bad_id = f"{RUN}:blind:cam-0:dark:opened:5"
    body = f'{{"record_id": "{bad_id}", "reason": "dark", "state": "nonsense-state"}}'
    _raw_insert(log_path, bad_id, "blind", _t(10), body)

    view = build_view(health_config(), log_path, now=_t(10.1))

    assert view.state == "UNKNOWN"
    assert view.ok is False
    assert bad_id in view.state_detail


# --- AC7: a Log written before the blind-reason index renders the same view -----


def _dark_open_log(path: Path) -> Path:
    with Log(path) as log:
        log.append(frame_at(_t(0), RUN, 0))
        log.append(
            Verdict(
                station_id="station-1",
                ts=_t(0),
                state=VerdictState.HEALTHY,
                faults=(),
                blind_reasons=(),
                seq=1,
                run_id=RUN,
            )
        )
        log.append(blind_at(_t(1), RUN, BlindReason.DARK, BlindState.OPENED, 1))
        log.append(blind_at(_t(2), RUN, BlindReason.FROZEN, BlindState.CLEARED, 2))
    return path


def test_a_log_without_the_blind_reason_index_renders_the_same_view(tmp_path, capsys):
    with_index = _dark_open_log(tmp_path / "indexed.db")
    without_index = tmp_path / "legacy.db"
    shutil.copy2(with_index, without_index)

    # Simulate a Log written before the index existed: drop it.
    conn = sqlite3.connect(str(without_index))
    try:
        conn.execute("DROP INDEX IF EXISTS records_blind_reason")
        conn.commit()
        assert (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name='records_blind_reason'"
            ).fetchone()
            is None
        )
    finally:
        conn.close()

    reader_mod._warned_no_reason_index = False  # so the fallback warning can fire once
    indexed_view = build_view(health_config(), with_index, now=_t(5))
    legacy_view = build_view(health_config(), without_index, now=_t(5))

    assert legacy_view == indexed_view
    assert legacy_view.blind_reasons == ("dark",)
    # The read-only Board says once, on stderr, that it fell back.
    assert "records_blind_reason" in capsys.readouterr().err
