"""The not-evaluable dated-loosening reader and its audit-ledger write, for
`pr_merge_guard.py`.

Split out of the main hook module (architecture: file/function-count caps).
Stdlib-only, no `bpsai_pair` import, matching the main hook's own runtime
contract -- see `_read_merge_gate_loosening`'s own docstring for why this
cannot simply ask the CLI to resolve its own dial for this one case.
"""

from __future__ import annotations

import getpass
import json
import os
import re
from datetime import date, datetime, timezone

NOT_EVALUABLE_WARN_TEMPLATE = (
    "This `gh pr merge` could not be evaluated: {why}.\n"
    "ALLOWED: not-evaluable under a dated `merge_gate` warn loosening "
    "(until {until}; reason: {reason}).\n"
    "This is NOT a clean verdict -- it is a predicate that could not run, "
    "let through only because an operator dated a loosening. Re-check with "
    "`bpsai-pair pr merge-ready` once a `bpsai-pair` carrying `pr "
    "merge-guard-check` is reachable."
)

#: Used by `pr_merge_guard.main` instead of `pr_merge_guard_predicate.
#: FAIL_CLOSED_TEMPLATE` specifically for the not-evaluable failure shape
#: (a resolved `bpsai-pair` that predates `pr merge-guard-check`), whether
#: or not the dated-loosening carve-out above ended up honored. The generic
#: template names only one remedy (upgrade the CLI); a not-evaluable
#: predicate has a SECOND, equally real one -- the audited
#: `BPSAI_MERGE_GUARD_ALLOW_NOT_EVALUABLE=1` + `merge_gate: warn` carve-out
#: -- and a message that omits it reads as though that env var does
#: nothing, when in fact it simply also requires the dated config entry
#: `_read_merge_gate_loosening` looks for (fleet operator report: the env
#: var alone, with no matching `merge_gate: warn` entry, understandably
#: looked like dead code).
NOT_EVALUABLE_FAIL_CLOSED_TEMPLATE = (
    "This `gh pr merge` could not be checked: {why}.\n"
    "The merge guard fails CLOSED -- a predicate that could not run is not\n"
    "a clean bill of health.\n"
    "Remedy (either one):\n"
    "  1. Upgrade the CLI to a version carrying `pr merge-guard-check`\n"
    "     (`pip install --upgrade bpsai-pair`, then `bpsai-pair upgrade`).\n"
    "  2. Or set a dated, audited loosening: `bpsai-pair config set-gate\n"
    '     merge_gate warn --reason "..." --until <date>`, then set\n'
    "     `BPSAI_MERGE_GUARD_ALLOW_NOT_EVALUABLE=1` in the environment that\n"
    "     runs this hook -- allowed only while that loosening is live, and\n"
    "     every use is logged to `.paircoder/history/bypass_log.jsonl`.\n"
    "Run `{argv}` yourself to see the failure."
)


def not_evaluable_fail_closed_message(failure: str, argv: str) -> str:
    """`NOT_EVALUABLE_FAIL_CLOSED_TEMPLATE.format(...)`, as a helper rather
    than an inline call at the `pr_merge_guard.main` call site -- the
    inlined form's line length sits in the narrow band (>88, <=100 chars)
    where the payload's own dual ruff-format gate (default 88 / consumer
    100 line-length) disagrees about whether to collapse it."""
    return NOT_EVALUABLE_FAIL_CLOSED_TEMPLATE.format(why=failure, argv=argv)


def _yaml_scalar(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
        return raw[1:-1]
    return raw


#: The exact nesting `set_gate` ("core/gate_dial.py") writes the dial's
#: entry under -- required so a `merge_gate:` key ANYWHERE else in the file
#: (e.g. a stale `engage.gate_modes.merge_gate` a prior config layout could
#: have left behind, or in principle any other mapping) is never mistaken
#: for the one dial `resolve_merge_gate` actually reads (security-review
#: follow-up: matching the bare key name at ANY indentation/parent let a
#: block outside this exact path silently drive the allow decision here).
_MERGE_GATE_PATH = ("architecture", "gate_modes", "merge_gate")

#: Bounds the upward walk in `_find_config_path` -- a filesystem root is
#: reached long before this in practice; this is a hard stop against an
#: unbounded loop on a pathological `cwd` (e.g. a symlink cycle), not a
#: realistic repo depth.
_MAX_WALK_UP = 64


def _find_paircoder_dir(cwd: str):
    """The first `<ancestor>/.paircoder` directory found walking UP from
    `cwd` (`cwd` itself, then each parent in turn), or `None`.

    Mirrors `core/ops_project.find_project_root`'s own walk -- the CLI
    authority this reader stands in for locates its config (and its
    bypass ledger) the same way, via `find_paircoder_dir`. Two details
    matched deliberately, both cross-module review follow-ups:

    * Stops at the first `.git` sighting TOO, not just `.paircoder` --
      `find_project_root` resolves ITS project root there even absent a
      `.paircoder` (so the CLI would report "no config" rather than
      falling through further up). Without this, a nested checkout (its
      own `.git`, no `.paircoder` of its own) would let this reader walk
      PAST its own root into an unrelated ANCESTOR repo's `.paircoder`
      and honor a loosening the CLI itself would never see. Checked with
      `os.path.exists`, NOT `os.path.isdir` (cross-module review
      follow-up): in a git WORKTREE or submodule -- this project's own
      engage lanes run in worktrees -- `.git` is a FILE (a gitdir
      pointer), not a directory; `find_project_root` itself checks
      `.exists()` for exactly this reason, and an `isdir` check here
      would silently never fire in the shape this project uses routinely.
    * Walks the path AS GIVEN (`os.path.abspath`, not `os.path.realpath`)
      -- `find_project_root` never resolves symlinks either. Resolving
      them here would let this reader and the CLI walk into DIFFERENT
      directory trees on a symlinked checkout (common for engage
      worktrees), each reading a different `.paircoder/config.yaml`.
    """
    current = os.path.abspath(cwd)
    for _ in range(_MAX_WALK_UP):
        if os.path.isdir(os.path.join(current, ".paircoder")):
            return os.path.join(current, ".paircoder")
        if os.path.exists(os.path.join(current, ".git")):
            return None
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent
    return None


def _read_merge_gate_loosening(cwd: str, *, today=None):
    """``(reason, until)`` for a live, unexpired ``merge_gate: warn`` entry
    at ``architecture.gate_modes.merge_gate`` in
    ``<cwd>/.paircoder/config.yaml``, else ``None``.

    Used ONLY for the not-evaluable case in ``main``. This hook cannot
    import ``bpsai_pair.core.gate_dial.resolve_merge_gate`` (its
    stdlib-only contract) -- and, more fundamentally, the very CLI this
    reader stands in for is the one that turned out not to recognize this
    dial's own feature yet, so asking it to resolve its OWN dial is not an
    option here the way it is for every OTHER hook failure. This is
    therefore a deliberately narrow, stdlib-only, line-scanned reader for
    exactly the three scalars `set_gate` ever writes under
    `_MERGE_GATE_PATH` -- not a YAML parser, and not a second copy of the
    review-thread logic the main module docstring's "COUNTERPARTY"
    contract is actually protecting against re-deriving.

    Tracks the current mapping-key ancestry by indentation as it scans: a
    ``key:`` line with no inline value opens a nested block at that
    indentation, closed by the next line at the SAME OR SHALLOWER indent.
    Only ``mode``/``reason``/``until`` scalars encountered while the
    ancestry stack exactly equals `_MERGE_GATE_PATH` are captured.

    ACKNOWLEDGED DRIFT RISK (cross-module review finding): this duplicates
    just enough of `core/gate_dial._resolve_entry`'s semantics to agree
    with it for the ONE shape `set_gate` actually writes (the
    ``{mode, reason, until}`` dict), with no shared test pinning the two
    together. It already diverges on a shape `set_gate` never emits -- a
    legacy bare ``merge_gate: warn`` string with no ``until`` -- which
    `_resolve_entry` treats as an active, un-expiring loosening (allow)
    and this reader does not recognize as a mapping at all (fields stay
    empty, so it falls closed). That divergence is in the SAFE direction
    (this reader is stricter, never more permissive), so it is accepted
    rather than chased -- but any FUTURE change to how `set_gate`
    serializes the dial's entry is undetectable here except as a silently
    broken not-evaluable loosening.

    ACKNOWLEDGED SELF-SERVE RISK (cross-module review finding): reads
    config from the same working tree the hook is evaluating a merge for
    -- ordinarily the PR branch itself. This is NOT a new risk category:
    the main module's own docstring already accepts that an author with
    push access can add a `merge_gate: {mode: warn, ...}` entry to their
    own branch and self-serve past a REAL block, with the audited
    `bypass_log.jsonl` row as the compensating control, and declares
    moving the dial outside the checkout "a change to the shared gate-dial
    architecture... out of scope" for that path. This reader shares the
    exact same dial, the exact same compensating control (see
    `_write_not_evaluable_bypass_row`), and the exact same out-of-scope
    boundary -- it is the SAME accepted tradeoff reached via a second code
    path (a stale/PATH-shadowed predicate, observed as a real recurring
    condition in this project's own multi-repo tooling), not a new one.

    ACKNOWLEDGED DRIFT RISK, PART 2 (cross-module review follow-up): walks
    UP from `cwd` looking for `.paircoder/config.yaml`, mirroring
    `core/ops_project.find_project_root`'s own walk (checked here at
    every ancestor, not just `cwd` itself) -- the CLI authority this
    reader stands in for locates its config the same way, via
    `find_paircoder_dir`. Without this walk, a hook event firing from a
    subdirectory of the repo would silently disagree with the CLI about
    whether a loosening exists at all (fail closed either way, but for
    the wrong reason).
    """
    paircoder_dir = _find_paircoder_dir(cwd)
    if paircoder_dir is None:
        return None
    config_path = os.path.join(paircoder_dir, "config.yaml")
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return None

    fields = {}
    stack = []  # list of (indent, key)
    for raw_line in lines:
        line = raw_line.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        while stack and indent <= stack[-1][0]:
            stack.pop()
        path = tuple(key for _, key in stack)

        if path == _MERGE_GATE_PATH:
            field_match = re.match(r"^\s*(mode|reason|until):\s*(.*)$", line)
            if field_match:
                fields[field_match.group(1)] = _yaml_scalar(field_match.group(2))
            continue

        key_match = re.match(r"^(\S[^:]*):\s*$", line.strip())
        if key_match and len(path) < len(_MERGE_GATE_PATH):
            stack.append((indent, key_match.group(1)))

    if fields.get("mode") != "warn":
        return None
    until = fields.get("until")
    if not until:
        return None
    try:
        until_date = datetime.strptime(until, "%Y-%m-%d").date()
    except ValueError:
        return None
    if until_date < (today or date.today()):
        return None
    return fields.get("reason", ""), until


#: Same ledger, same relative path (from the `.paircoder` directory),
#: every other payload guard hook already appends to directly
#: (`base_branch_guard.py` / `git_commit_guard.py` /
#: `public_repo_push_guard.py`) -- see `core/bypass_log.py`'s own note on
#: those call sites (stdlib-only, no `bpsai_pair` import, each script owns
#: its own append).
BYPASS_LOG_HISTORY_REL = os.path.join("history", "bypass_log.jsonl")

NOT_EVALUABLE_BYPASS_TYPE = "merge_gate_not_evaluable_bypass"


def _resolve_user() -> str:
    """Mirrors `core/bypass_log.py`'s own `_resolve_user` -- kept as a
    literal duplicate rather than an import, per this hook's stdlib-only
    contract."""
    try:
        user = getpass.getuser()
        if user:
            return user
    except Exception:
        pass
    return os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"


def _write_not_evaluable_bypass_row(
    cwd: str,
    *,
    reason: str,
    until: str,
    why: str,
) -> str | None:
    """Append one row to the unified bypass ledger for a not-evaluable
    allow; returns an error string on failure, else ``None``.

    Security-review follow-up: `_read_merge_gate_loosening` allows this
    ONE outcome without ever invoking the CLI -- so, unlike an ordinary
    `merge_gate: warn` allow (audited by `pr_merge_guard_cmd.
    evaluate_merge_guard`'s own `log_bypass` call, on the other side of a
    real predicate invocation), nothing else in this path would otherwise
    write to the ledger the project's own docs describe as recording
    EVERY loosening. A write failure (e.g. a read-only checkout) is
    therefore NOT swallowed here -- the caller (`main`) treats it as a
    failure to obtain an audited allow and falls back to the unconditional
    deny, rather than letting an unauditable bypass through silently
    (security-review follow-up round 4: the ledger is this path's ONLY
    compensating control, so its own write failing must not read as "the
    allow is still fine").

    ACKNOWLEDGED LIFECYCLE GAP (security-review follow-up round 9): the
    ledger lives at `cwd`'s OWN `.paircoder/history/`, which for an engage
    lane is a per-worktree path -- `docs/merge-predicate.md` documents
    that the worktree is cleaned up once its run completes, taking this
    row with it. The write itself is correct (it succeeded, so the
    enforcement decision it records was genuinely audited at the instant
    it happened); what isn't guaranteed is that the row SURVIVES past the
    worktree's own lifecycle. Resolving that would mean this stdlib-only
    hook locating a durable, REPO-level ledger from inside a worktree
    (parsing the `.git` gitdir-pointer file to find the original clone) --
    out of scope for this pass; recorded here rather than silently assumed
    away.
    """
    entry = {
        "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
        "command": "pr_merge_guard",
        "target": "not-evaluable",
        "reason": reason,
        "bypass_type": NOT_EVALUABLE_BYPASS_TYPE,
        "user": _resolve_user(),
        "session_id": os.environ.get("CLAUDE_SESSION_ID", ""),
        "cwd": cwd,
        "metadata": {"gate": "merge_gate", "mode": "warn", "until": until, "why": why},
    }
    paircoder_dir = _find_paircoder_dir(cwd)
    if paircoder_dir is None:
        return f"no .paircoder directory found walking up from {cwd!r}"
    log_path = os.path.join(paircoder_dir, BYPASS_LOG_HISTORY_REL)
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    except OSError as exc:
        return str(exc)
    return None
