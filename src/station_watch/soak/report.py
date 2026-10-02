"""Render a soak's result as the markdown table that sits beside the measurement.

The table's heading names the dataset kind (so a ``synthetic`` proving soak is never
read as a real bench soak) and its header carries the exact command that reproduces
it, built with :func:`shlex.join` so paths with spaces re-parse unchanged. Each row
is one judged dimension -- RSS per child, Log growth, Board render, verdict cadence,
false flags -- shown with its measured number, so the sheet stands alone.
"""

from __future__ import annotations

import shlex


def reproduce_command(args) -> str:
    """The exact ``station-watch soak`` command that reproduces this run."""
    parts = [
        "station-watch",
        "soak",
        "--config",
        args.config,
        "--log",
        args.log,
        "--out",
        args.out,
        "--dataset-kind",
        args.dataset_kind,
        "--source",
        str(args.source),
    ]
    if args.hours is not None:
        parts += ["--hours", str(args.hours)]
    else:
        parts += ["--minutes", str(args.minutes)]
    parts += ["--sample-s", str(args.sample_s), "--board-port", str(args.board_port)]
    return shlex.join(parts)


def _metric_rows(metrics: dict) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for key, value in sorted(metrics.items()):
        if key.startswith("rss_growth_mb_per_h."):
            name = key.split(".", 1)[1]
            rows.append((f"RSS growth {name} (MB/h)", f"{value:.2f}"))
    rows.append(("Log growth (MB/h)", f"{metrics.get('log_growth_mb_per_h', 0.0):.2f}"))
    rows.append(("Board render p95 (s)", f"{metrics.get('board_render_p95_s', 0.0):.3f}"))
    rows.append(("Board render failures", str(metrics.get("board_render_failures", 0))))
    rows.append(("Max verdict gap (s)", f"{metrics.get('max_verdict_gap_s', 0.0):.2f}"))
    rows.append(("Verdicts / minute", f"{metrics.get('verdicts_per_minute', 0.0):.2f}"))
    rows.append(("False flags", str(metrics.get("false_flags", 0))))
    rows.append(("Duration (s)", f"{metrics.get('duration_s', 0.0):.1f}"))
    return rows


def render_table(provenance: dict, metrics: dict, command: str) -> str:
    """The markdown table for a soak run: heading, reproduce command, one row per metric."""
    kind = provenance["dataset_kind"]
    lines = [
        f"# Station Watch soak — {kind}",
        "",
        f"- dataset_kind: **{kind}**",
        f"- status: **{metrics.get('status', 'unknown')}**",
        f"- git commit: `{provenance['git_commit']}`",
        f"- detector: `{provenance['detector']}`",
        "",
        "Reproduce:",
        "",
        f"```\n{command}\n```",
        "",
        "| Metric | Value |",
        "| --- | --- |",
    ]
    lines.extend(f"| {name} | {value} |" for name, value in _metric_rows(metrics))
    return "\n".join(lines) + "\n"


__all__ = ["reproduce_command", "render_table"]
