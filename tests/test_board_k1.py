"""K1 as a property table: the Board shows OK only on a healthy, fresh, unblinded
station -- every other combination reads UNKNOWN, STALE, FAULT or the blind reason.

The rule (K1) is: the page's status is ``OK`` *iff* the newest verdict is
``healthy`` and fresh, the newest frame and the newest alarm evaluation are both
fresh, and no blind reason is open. We enumerate the full cross-product of

* newest verdict state: healthy / fault / unobservable / none,
* verdict age: fresh / stale,
* frame age: fresh / stale,
* alarm-evaluation age: fresh / stale,
* open blind reasons: none / one,

build a real Log for each, render the real HTML page, parse its status the way a
browser would, and assert the status matches the oracle -- and that the specific
not-OK reason is actually surfaced. A final "test of the test" points the same
assertion at a deliberately broken renderer that shows OK for a stale verdict and
proves the check fails.
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

import pytest

from station_watch.board import build_view, render_html
from station_watch.clock import offset_iso
from station_watch.log import Log
from station_watch.records import (
    AlarmEvaluated,
    BlindReason,
    BlindState,
    Fault,
    FaultKind,
    Verdict,
    VerdictState,
)

sys.path.insert(0, str(Path(__file__).parent))
from helpers.board_page import parse_status  # noqa: E402
from helpers.records import blind_at, frame_at, health_config  # noqa: E402

EPOCH = "2026-09-28T00:00:00.000000+00:00"
RUN = "run-k1"
NOW = 100.0  # health_config windows: cycle/alarm 10.0s, liveness 2.0s

# Per-rail ages chosen against those windows: "fresh" is inside the rail's own
# window, "stale" is well outside it.
_VERDICT_TS = {"fresh": NOW - 2.0, "stale": NOW - 50.0}
_FRAME_TS = {"fresh": NOW - 1.0, "stale": NOW - 10.0}
_ALARM_TS = {"fresh": NOW - 2.0, "stale": NOW - 50.0}

_STATES = {
    "healthy": VerdictState.HEALTHY,
    "fault": VerdictState.FAULT,
    "unobservable": VerdictState.UNOBSERVABLE,
    "none": None,
}


def _t(offset: float) -> str:
    return offset_iso(EPOCH, offset)


def _verdict(state: VerdictState, verdict_age: str) -> Verdict:
    faults = (
        (Fault(FaultKind.STALLED, "zone_press", (11, 12)),) if state is VerdictState.FAULT else ()
    )
    blind_reasons = (BlindReason.DARK,) if state is VerdictState.UNOBSERVABLE else ()
    return Verdict(
        station_id="station-1",
        ts=_t(_VERDICT_TS[verdict_age]),
        state=state,
        faults=faults,
        blind_reasons=blind_reasons,
        seq=1,
        run_id=RUN,
    )


def _build_log(path: Path, combo: dict) -> Path:
    with Log(path) as log:
        log.append(frame_at(_t(_FRAME_TS[combo["frame_age"]]), RUN, 0))
        log.append(
            AlarmEvaluated(
                ts=_t(_ALARM_TS[combo["alarm_age"]]), seq=1, open_episodes=(), run_id=RUN
            )
        )
        state = _STATES[combo["state"]]
        if state is not None:
            log.append(_verdict(state, combo["verdict_age"]))
        if combo["blind"] == "one":
            log.append(
                blind_at(_t(_FRAME_TS["fresh"]), RUN, BlindReason.DARK, BlindState.OPENED, 1)
            )
    return path


def _oracle_ok(combo: dict) -> bool:
    """K1: OK iff healthy + fresh verdict, fresh frame, fresh alarm, no open blind."""
    return (
        combo["state"] == "healthy"
        and combo["verdict_age"] == "fresh"
        and combo["frame_age"] == "fresh"
        and combo["alarm_age"] == "fresh"
        and combo["blind"] == "none"
    )


def _expected_reason_tokens(combo: dict) -> list[str]:
    """At least one of these must appear on a not-OK page (the surfaced reason)."""
    tokens: list[str] = []
    state_unknown = combo["state"] == "none" or combo["verdict_age"] == "stale"
    if state_unknown:
        tokens.append("UNKNOWN")
    elif combo["state"] == "fault":
        tokens.append("FAULT")
    elif combo["state"] == "unobservable":
        tokens.append("UNOBSERVABLE")
    if combo["frame_age"] == "stale" or combo["alarm_age"] == "stale":
        tokens.append("STALE")
    if combo["blind"] == "one":
        tokens.append("dark")
    return tokens


def assert_k1(status_text: str, html: str, combo: dict) -> None:
    """The one K1 assertion, shared by the real table and the test-of-the-test."""
    oracle = _oracle_ok(combo)
    assert (status_text == "OK") == oracle, f"status {status_text!r} violates K1 for {combo}"
    if not oracle:
        reasons = _expected_reason_tokens(combo)
        assert any(tok in html for tok in reasons), f"no reason {reasons} surfaced for {combo}"


_COMBOS = [
    {
        "state": state,
        "verdict_age": verdict_age,
        "frame_age": frame_age,
        "alarm_age": alarm_age,
        "blind": blind,
    }
    for state, verdict_age, frame_age, alarm_age, blind in itertools.product(
        _STATES, ("fresh", "stale"), ("fresh", "stale"), ("fresh", "stale"), ("none", "one")
    )
]


def _combo_id(combo: dict) -> str:
    return "-".join(combo[k] for k in ("state", "verdict_age", "frame_age", "alarm_age", "blind"))


@pytest.mark.parametrize("combo", _COMBOS, ids=[_combo_id(c) for c in _COMBOS])
def test_k1_status_matches_the_oracle_for_every_combination(tmp_path, combo):
    log_path = _build_log(tmp_path / "log.db", combo)
    view = build_view(health_config(), log_path, now=_t(NOW))
    html = render_html(view)
    status, _ = parse_status(html)
    assert_k1(status, html, combo)


def test_the_table_covers_every_listed_combination():
    # 4 states x 2 verdict ages x 2 frame ages x 2 alarm ages x 2 blind = 64.
    assert len(_COMBOS) == 4 * 2 * 2 * 2 * 2
    assert sum(1 for c in _COMBOS if _oracle_ok(c)) == 1  # exactly one OK cell


def _broken_render_html(view) -> str:
    """A renderer that shows OK regardless of staleness -- the bug K1 forbids."""
    return (
        "<html><body>"
        "<h1>Station station-1 — HEALTHY</h1>"
        "<p class='status'>status: <b>OK</b></p>"
        "</body></html>"
    )


def test_of_the_test_a_broken_renderer_that_shows_ok_on_a_stale_verdict_fails(tmp_path):
    # A stale (so UNKNOWN, never OK) healthy verdict; the broken renderer lies OK.
    combo = {
        "state": "healthy",
        "verdict_age": "stale",
        "frame_age": "fresh",
        "alarm_age": "fresh",
        "blind": "none",
    }
    assert _oracle_ok(combo) is False
    log_path = _build_log(tmp_path / "log.db", combo)
    view = build_view(health_config(), log_path, now=_t(NOW))

    # The real renderer passes the check (UNKNOWN, NOT OK)...
    good_html = render_html(view)
    good_status, _ = parse_status(good_html)
    assert good_status == "NOT OK"
    assert_k1(good_status, good_html, combo)

    # ...the broken renderer, which shows OK for the stale verdict, must fail it.
    broken_html = _broken_render_html(view)
    broken_status, _ = parse_status(broken_html)
    assert broken_status == "OK"
    with pytest.raises(AssertionError):
        assert_k1(broken_status, broken_html, combo)
