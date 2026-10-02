"""Argument wiring for ``station-watch audit`` (build / review / mark / score).

Kept light (argparse only) so building the parser never pulls in OpenCV, the Log walk
or the HTTP server; the heavy handlers are imported lazily inside :func:`handle`. A
missing or unreadable config/Log/artifact fails loud and non-zero (K9), naming the
piece. ``review`` serves the HF3.5 sheet with a verdict control on loopback; ``mark``
is its headless equivalent; ``score`` writes a reviewed-precision measurement file.
"""

from __future__ import annotations

import sys
from pathlib import Path

from station_watch.audit.verdicts import VALID_VERDICTS
from station_watch.board.reader import BoardLogError

_DEFAULT_EVIDENCE_DIR = "data/local/evidence"
_DEFAULT_REVIEW_PORT = 8766


def add_parser(sub) -> None:
    """Add the ``audit`` subparser (build / review / mark / score) to ``sub``."""
    audit = sub.add_parser(
        "audit",
        help="build, review and score a proof sheet of every flag a session raised",
        description="Read one session's Log read-only and work with its flags: build a "
        "self-contained proof sheet, review each flag correct/incorrect in the browser or "
        "headless, and score reviewed precision. The Log is never written.",
    )
    actions = audit.add_subparsers(dest="audit_action", required=True)
    _add_build(actions)
    _add_review(actions)
    _add_mark(actions)
    _add_score(actions)
    _add_rate(actions)


def _add_build(actions) -> None:
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
    build.add_argument("--out", help="output dir (default: data/local/audit/<log file stem>/)")


def _add_review(actions) -> None:
    review = actions.add_parser(
        "review",
        help="serve the sheet with a correct/incorrect control on 127.0.0.1",
        description="Serve <audit dir>/flags.json as a review page on loopback with a "
        "correct/incorrect control and an optional note per flag; marks append to "
        "verdicts.jsonl. Token-gated, loopback-only, with a per-response CSP.",
    )
    review.add_argument("--audit", required=True, help="the audit dir holding flags.json")
    review.add_argument(
        "--port", type=int, default=_DEFAULT_REVIEW_PORT, help="loopback port (0 picks a free one)"
    )
    review.add_argument("--reviewer", help="reviewer name recorded on each mark (default: $USER)")


def _add_mark(actions) -> None:
    mark = actions.add_parser(
        "mark",
        help="record a verdict for one flag without a browser",
        description="The headless equivalent of the review page's control: append one "
        "correct/incorrect verdict for a flag to verdicts.jsonl, idempotent per flag.",
    )
    mark.add_argument("--audit", required=True, help="the audit dir holding flags.json")
    mark.add_argument("flag_id", help="the flag to mark (must appear in flags.json)")
    mark.add_argument("verdict", choices=list(VALID_VERDICTS), help="correct or incorrect")
    mark.add_argument("--note", help="an optional note stored with the verdict")
    mark.add_argument("--reviewer", help="reviewer name recorded on the mark (default: $USER)")


def _add_score(actions) -> None:
    score = actions.add_parser(
        "score",
        help="write a reviewed-precision measurement file",
        description="From flags.json, verdicts.jsonl and the session's QA, write reviewed "
        "precision per flag kind and overall to a measurement file (confined to the dataset "
        "kind's own tree).",
    )
    score.add_argument("--audit", required=True, help="the audit dir holding flags.json")
    score.add_argument("--config", required=True, help="path to the station config YAML")
    score.add_argument("--log", required=True, help="path to the session's append-only Log")
    score.add_argument(
        "--dataset-kind",
        required=True,
        choices=["real", "synthetic"],
        help="which measurement tree --out must land in",
    )
    score.add_argument("--out", required=True, help="measurement file to write")
    score.add_argument(
        "--force-out", action="store_true", help="override --out confinement (one warning line)"
    )


def _add_rate(actions) -> None:
    rate = actions.add_parser(
        "rate",
        help="write a flags-per-hour measurement file for a normal-work session",
        description="From flags.json, verdicts.jsonl and the session's QA, write flags per "
        "hour of observable operation -- split true/false/unreviewed per kind and overall -- "
        "to a measurement file (confined to the dataset kind's own tree). Blind episodes are "
        "reported in their own block. Observable time under --min-hours writes a status, not a "
        "rate.",
    )
    rate.add_argument("--audit", required=True, help="the audit dir holding flags.json")
    rate.add_argument("--config", required=True, help="path to the station config YAML")
    rate.add_argument("--log", required=True, help="path to the session's append-only Log")
    rate.add_argument(
        "--dataset-kind",
        required=True,
        choices=["real", "synthetic"],
        help="which measurement tree --out must land in",
    )
    rate.add_argument("--out", required=True, help="measurement file to write")
    rate.add_argument(
        "--min-hours",
        type=float,
        default=1.0,
        help="minimum observable hours to measure a rate (default: 1.0)",
    )
    rate.add_argument(
        "--force-out", action="store_true", help="override --out confinement (one warning line)"
    )


def _default_out(log_path: str) -> str:
    return str(Path("data/local/audit") / Path(log_path).stem)


def _run_build(args) -> int:
    from station_watch.audit.build import build_audit

    out = args.out if args.out is not None else _default_out(args.log)
    return build_audit(
        config_path=args.config,
        log_path=args.log,
        evidence_dir=args.evidence_dir,
        clip=args.clip,
        out=out,
    )


def _run_review(args) -> int:
    import getpass

    from station_watch.audit.flags import load_flags_json
    from station_watch.audit.review_server import make_review_server

    flags = load_flags_json(args.audit)
    reviewer = args.reviewer or getpass.getuser()
    server = make_review_server(
        args.audit, flags, [f["flag_id"] for f in flags], reviewer, port=args.port
    )
    host, port = server.server_address[:2]
    print(
        f"station-watch audit review: serving {args.audit} on "
        f"http://{host}:{port}/ (Ctrl-C to stop)"
    )
    print(f"station-watch audit review: token {server.token}", file=sys.stderr)
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _run_mark(args) -> int:
    import getpass

    from station_watch.audit.verdicts import append_mark, flag_ids
    from station_watch.clock import utc_now_iso

    if args.flag_id not in flag_ids(args.audit):
        print(f"station-watch: unknown flag_id {args.flag_id!r}", file=sys.stderr)
        return 2
    reviewer = args.reviewer or getpass.getuser()
    appended = append_mark(
        args.audit, args.flag_id, args.verdict, args.note or "", reviewer, ts=utc_now_iso()
    )
    verb = "appended" if appended else "unchanged"
    print(f"station-watch audit mark: {verb} {args.flag_id} {args.verdict}")
    return 0


def _run_score(args) -> int:
    from station_watch.audit.score import score_audit

    return score_audit(
        audit_dir=args.audit,
        config_path=args.config,
        log_path=args.log,
        dataset_kind=args.dataset_kind,
        out=args.out,
        force_out=args.force_out,
    )


def _run_rate(args) -> int:
    from station_watch.audit.rate import rate_audit

    return rate_audit(
        audit_dir=args.audit,
        config_path=args.config,
        log_path=args.log,
        dataset_kind=args.dataset_kind,
        out=args.out,
        min_hours=args.min_hours,
        force_out=args.force_out,
    )


_HANDLERS = {
    "build": _run_build,
    "review": _run_review,
    "mark": _run_mark,
    "score": _run_score,
    "rate": _run_rate,
}


def handle(args) -> int:
    """Run the requested ``audit`` action; return an exit code (0 ok, 2 startup error)."""
    handler = _HANDLERS.get(args.audit_action)
    if handler is None:
        print(f"station-watch: unknown audit action: {args.audit_action}", file=sys.stderr)
        return 2
    try:
        return handler(args)
    except (OSError, KeyError, ValueError, BoardLogError) as exc:
        print(f"station-watch: {exc}", file=sys.stderr)
        return 2


__all__ = ["add_parser", "handle"]
