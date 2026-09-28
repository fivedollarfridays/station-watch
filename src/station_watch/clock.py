"""The one canonical clock: ISO 8601 UTC timestamps with microseconds.

``ts`` is the single wall-clock time the Log orders by and the Watchdog judges
against, so every producer (live Capture and rebased fixtures alike) formats it
the same way here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def utc_now_iso() -> str:
    """Current wall clock as ISO 8601 UTC with microseconds."""
    return to_iso(datetime.now(UTC))


def to_iso(moment: datetime) -> str:
    """Format a timezone-aware datetime as ISO 8601 UTC with microseconds."""
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def parse_iso(text: str) -> datetime:
    """Parse an ISO 8601 timestamp back into a timezone-aware datetime."""
    return datetime.fromisoformat(text)


def offset_iso(run_start_ts: str, offset_s: float) -> str:
    """Rebase ``run_start_ts`` by ``offset_s`` seconds, returning an ISO string."""
    return to_iso(parse_iso(run_start_ts) + timedelta(seconds=offset_s))
