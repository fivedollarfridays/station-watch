"""Render a drill's measurement metrics as a markdown time-to-alarm table.

The table sits beside the measurement JSON. Its heading names the dataset kind (so a
``synthetic`` proving run can never be read as a bench run) and its header carries the
exact command that reproduces it. Each fault row shows the blind reason that opened,
the time from injection to the first alarm and from clear to recovery, and whether
exactly one alarm and one recovery occurred -- a fault that never alarmed reads
``none``, and a reason that is not the expected one is flagged, never hidden.
"""

from __future__ import annotations

_HEADERS = (
    "Fault",
    "Blind reason",
    "Time to alarm (s)",
    "Time to recovery (s)",
    "One alarm",
    "One recovery",
)


def _fmt_seconds(value: float | None) -> str:
    return "none" if value is None else f"{value:.3f}"


def _reason_cell(entry: dict) -> str:
    reason = entry["blind_reason"] or "none"
    if entry["blind_reason"] is not None and not entry["reason_matches"]:
        expected = " / ".join(entry["expected_reason"]) or "?"
        return f"{reason} (expected {expected})"
    return reason


def _yes_no(flag: bool) -> str:
    return "yes" if flag else "NO"


def _fault_row(name: str, entry: dict) -> str:
    cells = (
        name,
        _reason_cell(entry),
        _fmt_seconds(entry["time_to_alarm_s"]),
        _fmt_seconds(entry["time_to_recovery_s"]),
        _yes_no(entry["exactly_one_alarm"]),
        _yes_no(entry["exactly_one_recovery"]),
    )
    return "| " + " | ".join(cells) + " |"


def render_table(provenance: dict, metrics: dict, command: str) -> str:
    """The markdown table for a drill run: heading, reproduce command, one row per fault."""
    kind = provenance["dataset_kind"]
    lines = [
        f"# Station Watch fault drill — {kind}",
        "",
        f"- dataset_kind: **{kind}**",
        f"- git commit: `{provenance['git_commit']}`",
        f"- detector: `{provenance['detector']}`",
        "",
        "Reproduce:",
        "",
        f"```\n{command}\n```",
        "",
        "| " + " | ".join(_HEADERS) + " |",
        "|" + "|".join([" --- "] * len(_HEADERS)) + "|",
    ]
    lines.extend(_fault_row(name, entry) for name, entry in metrics["faults"].items())
    return "\n".join(lines) + "\n"


__all__ = ["render_table"]
