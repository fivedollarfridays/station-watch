"""The argparse builders for ``station-watch audit`` and its five actions.

argparse only, so building the CLI parser imports nothing heavy. The handlers are in
:mod:`station_watch.audit.commandline`, which re-exports :func:`add_parser`.
"""

from __future__ import annotations

from station_watch.audit.verdicts import VALID_VERDICTS

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
        "verdicts.jsonl. Bound to 127.0.0.1 only. Every request needs the session token: "
        "open the sign-in URL printed once on stderr at startup, which sets it as an "
        "HttpOnly cookie (without it the page is 401, with a wrong one 403). Every page "
        "carries a per-response CSP.",
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


__all__ = ["add_parser"]
