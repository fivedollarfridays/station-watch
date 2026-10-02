"""Argument wiring for ``station-watch audit`` (later ``audit`` actions add here).

Kept light (argparse only) so building the parser never pulls in OpenCV or the
Log walk; the heavy :func:`~station_watch.audit.build.build_audit` is imported
lazily inside :func:`handle`. A missing or unreadable config/Log fails loud and
non-zero (K9), naming the piece.
"""

from __future__ import annotations

import sys
from pathlib import Path

from station_watch.board.reader import BoardLogError

_DEFAULT_EVIDENCE_DIR = "data/local/evidence"


def add_parser(sub) -> None:
    """Add the ``audit`` subparser (with its ``build`` action) to ``sub``."""
    audit = sub.add_parser(
        "audit",
        help="build a proof sheet of every flag a session raised",
        description="Read one session's Log read-only and write a self-contained proof sheet: "
        "flags.json plus an index.html showing every fault and blind episode with each cited "
        "frame as an evidence thumbnail. The Log is never written.",
    )
    actions = audit.add_subparsers(dest="audit_action", required=True)
    build = actions.add_parser(
        "build",
        help="write flags.json and index.html for one session's Log",
        description="Collect every flag of the session and write <out>/flags.json and a "
        "self-contained <out>/index.html proof sheet.",
    )
    build.add_argument("--config", required=True, help="path to the station config YAML")
    build.add_argument("--log", required=True, help="path to the session's append-only Log")
    build.add_argument(
        "--evidence-dir",
        default=_DEFAULT_EVIDENCE_DIR,
        help="where the run kept evidence thumbnails (default: data/local/evidence)",
    )
    build.add_argument(
        "--clip",
        help="recorded clip to fall back to for a cited frame with no stored evidence",
    )
    build.add_argument(
        "--out",
        help="output dir (default: data/local/audit/<log file stem>/)",
    )


def _default_out(log_path: str) -> str:
    return str(Path("data/local/audit") / Path(log_path).stem)


def handle(args) -> int:
    """Run the requested ``audit`` action; return an exit code (0 ok, 2 startup error)."""
    from station_watch.audit.build import build_audit

    if args.audit_action != "build":
        print(f"station-watch: unknown audit action: {args.audit_action}", file=sys.stderr)
        return 2
    out = args.out if args.out is not None else _default_out(args.log)
    try:
        return build_audit(
            config_path=args.config,
            log_path=args.log,
            evidence_dir=args.evidence_dir,
            clip=args.clip,
            out=out,
        )
    except (OSError, KeyError, ValueError, BoardLogError) as exc:
        print(f"station-watch: {exc}", file=sys.stderr)
        return 2


__all__ = ["add_parser", "handle"]
