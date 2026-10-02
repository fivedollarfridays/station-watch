"""Argument wiring for ``station-watch audit`` (build / review / mark / score).

Kept light (argparse only) so building the parser never pulls in OpenCV, the Log walk
or the HTTP server; the heavy handlers are imported lazily inside :func:`handle`. A
missing or unreadable config/Log/artifact fails loud and non-zero (K9), naming the
piece. ``review`` serves the HF3.5 sheet with a verdict control on loopback; ``mark``
is its headless equivalent; ``score`` writes a reviewed-precision measurement file.
The argparse builders live in :mod:`station_watch.audit.args`; the handlers live here.
"""

from __future__ import annotations

import sys
from pathlib import Path

from station_watch.audit.args import add_parser
from station_watch.board.reader import BoardLogError


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
    # The sign-in URL is the operator's only way in (the page is token-gated), so it
    # is printed once, here, on stderr, and nowhere else: never in stdout, a page or
    # a rejected-request log line.
    print(
        f"station-watch audit review: sign in at http://{host}:{port}/?token={server.token}",
        file=sys.stderr,
    )
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
