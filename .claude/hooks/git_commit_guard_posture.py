"""Posture reading for ``git_commit_guard.py`` -- the LOWER layer.

What ``guards.enforcement.on_error`` says, where it may say it, what that
resolves to, what gets said out loud about it, and what gets recorded.
Split out of ``git_commit_guard.py`` so the enforcement hook keeps only
the deciding and the enforcing; the reading lives here.

Imports NOTHING from ``git_commit_guard.py``: the dependency runs one way
only, so this module can be read and tested without the gate, and the
gate cannot create an import cycle by reaching back.

Runtime contract: stdlib only, plain ``python3``, no ``bpsai_pair``
import -- same as every other script in this payload; it runs on operator
machines with no PairCoder venv.
"""

from __future__ import annotations

import os
from pathlib import Path

# The text-level rules this module rests on: what may be read, and
# whether anything is being declared at all. A sibling, shipped and
# ordered alongside this file; if it is missing this module fails to
# import, which the enforcement hook's own guarded import turns into a
# named defect and a fail-closed posture.
from git_commit_guard_scan import (
    PROOF_ESCAPED,
    PROOF_UNDECODABLE,
    _absent_proof,
    _declares_the_key,
    _unmodellable,
)

# The DECLARED on-error posture, read from `.paircoder/config.yaml`
# (`guards.enforcement.on_error`). `fail-closed` is the default and the
# doctrine (gates fail closed; every bypass explicit and logged), but the
# opposite is a legitimate operator choice -- hook bugs do fire in the
# field, and a repo that would rather absorb a missed protected-branch
# commit than a bricked one may say so OUT LOUD, in a committed file,
# instead of relying on a silent fail-open nobody declared.
#
# Under EITHER posture the degraded path writes an audit row naming the
# failure class; only the exit code follows the declaration. Under
# fail-closed, a hook bug blocking a legitimate commit is recoverable
# without editing this script: the persistent opt-out marker
# (`.paircoder/hooks/base_branch_guard.off`) and the worktree-scoped
# engage exemption are both honored while degraded, and both audit.
FAIL_CLOSED = "fail-closed"
FAIL_OPEN = "fail-open"
POSTURE_KEY_PATH = ("guards", "enforcement", "on_error")

# TIGHTEN-ONLY FROM REPO SCOPE (ratified 2026-08-26).
# `.paircoder/config.yaml` is TRACKED, so the artifact this gate
# protects used to author the gate's own posture: one commit -- a web-UI
# commit no local hook ever observes -- could set `fail-open`, and every
# later exception here passed silently. The audit row was never the
# backstop it looked like either: a row that fails to append is not a
# control. So the two directions are no longer symmetric: a repo may
# TIGHTEN (declare `fail-closed`), and the LOOSENING direction is
# honoured only from the user/machine-level settings file below, which
# lives outside every checkout and which a pull request cannot touch.
# A repo-level `fail-open` is refused, out loud, with the remedy named.
#
# Reused, not invented: `~/.paircoder/preferences.yaml` is already the
# CLI's user-level settings file (`bpsai_pair.core.preferences`).
USER_POSTURE_REL = Path(".paircoder") / "preferences.yaml"

# States a declaration can be in, beyond the two postures themselves.
POSTURE_UNSET = "unset"  # absent, or a file that cannot be read
POSTURE_MALFORMED = "malformed"  # the key is there, the value is not a posture
POSTURE_TAMPERED = "tampered"  # the config path is a symlink
POSTURE_INSIDE_REPO = "inside-repo"  # the user-level file lands in the repo
POSTURE_UNREADABLE = "unreadable"  # `on_error:` present in a shape this cannot parse

# The ledger action naming a refused repo-level loosening. Distinct from
# the refusal it causes: a fleet needs to find repos whose tracked config
# is ASKING for something this gate will not grant.
POSTURE_REFUSED_ACTION = "repo_posture_fail_open_refused"

# The other half of the same idea: a posture that was IN FORCE for a
# commit. The announcement is the primary control, but a discarded
# stderr (`2>/dev/null`, or a closed fd 2 -- git reopens it on
# /dev/null) makes that write succeed into nothing, undetectably from
# inside the process. A record that outlives the terminal is the answer.
POSTURE_ACTIVE_ACTION = "posture_fail_open_active"

# The third posture FACT: a repo-level declaration this gate could not
# READ. Not an absence -- it pins fail-closed like every other
# repo-level declaration, and the row is how a fleet finds repos whose
# tracked config is declaring into the void.
POSTURE_REPO_IGNORED_ACTION = "repo_posture_unreadable"

CONFIG_SYMLINK_WARNING = (
    "git_commit_guard: .paircoder/config.yaml is a symlink -- treating as "
    "tampering, not a declaration. Posture stays fail-closed.\n"
)

POSTURE_REPO_SCOPE_REFUSED = (
    "git_commit_guard: REFUSED the repo-level "
    "`guards.enforcement.on_error: fail-open` in .paircoder/config.yaml -- "
    "a repo may only TIGHTEN this gate. fail-open is honoured only from "
    "{user}, which a commit to this repo cannot write. Posture stays "
    "fail-closed.\n"
)

# Said on EVERY invocation while it is active, not once in a ledger row
# while it is active. A weakened guard that says nothing is
# indistinguishable from a healthy one -- the absent-instrument shape,
# in config rather than in code.
POSTURE_ACTIVE_ANNOUNCEMENT = (
    "git_commit_guard: enforcement posture `fail-open` is ACTIVE "
    "(declared in {source}) -- a protected-branch commit is ALLOWED when "
    "this gate cannot evaluate. Default is fail-closed.\n"
)

POSTURE_USER_IGNORED = (
    "git_commit_guard: ignoring the user-level enforcement posture in "
    "{path} ({why}) -- treated as absent; posture stays fail-closed.\n"
)

# The repo side of POSTURE_USER_IGNORED, and the opposite verdict: an
# unreadable USER declaration is treated as absent (it could only have
# loosened), an unreadable REPO one is not (it could only have
# tightened, and tightening is what repo scope is allowed to do).
POSTURE_REPO_IGNORED = (
    "git_commit_guard: could not read the repo-level enforcement posture "
    "in .paircoder/config.yaml ({why}) -- a declaration this gate cannot "
    "read is still a declaration, not an absence, so it does NOT let a "
    "user-level `fail-open` through. Effective posture: fail-closed.\n"
)

POSTURE_MALFORMED_WHY = "not one of fail-closed/fail-open"

POSTURE_UNREADABLE_WHY = (
    "`on_error:` is present but not at `guards.enforcement.on_error` in a "
    "shape this gate can read -- a flow mapping, a YAML anchor and tab "
    "indentation all parse elsewhere and cannot be read here"
)

# A refusal states what was FOUND. The original wording asserted the
# key was present, which was false for a config that never mentions it
# -- reported from the field against a file whose only oddity was a
# multi-line scalar written by `upgrade`.
POSTURE_ESCAPED_TOKEN_WHY = (
    "a double-quoted scalar decodes, through escape sequences, to text "
    "containing the key name, and this gate cannot tell a decoded key "
    "from a decoded value"
)

POSTURE_UNDECODABLE_WHY = (
    "a double-quoted scalar here could not be decoded -- an unknown "
    "escape, a short hex escape, or no closing quote -- so the key "
    "cannot be shown to be absent"
)

POSTURE_UNREADABLE_BYTES_WHY = (
    "the file is there but its bytes could not be read at all -- "
    "undecodable, unopenable, or not a file"
)

POSTURE_INSIDE_REPO_WHY = "it resolves inside the repo this gate guards"


def _read_declaration(path: Path) -> tuple:
    """``(text, readable)`` for *path*.

    The two answers a reader can give that must NOT be conflated:
    "there is nothing here" and "there is something here I could not
    read". A single `""` for both meant a tracked config VISIBLY
    carrying `on_error: fail-closed` -- but holding one invalid UTF-8
    byte, or encoded UTF-16, or unopenable, or replaced by a directory
    -- was classed ABSENT, which is the one classification that lets a
    user-level `fail-open` through. Bytes this gate cannot read are the
    same failure class as grammar it cannot read, one layer down.

    An ABSENT file is readable and empty: that is the ordinary case and
    the one that must keep the user-level scope reachable at all. So is
    a genuinely zero-byte file -- emptiness is content, not a failure.
    """
    try:
        return path.read_text(encoding="utf-8"), True
    except FileNotFoundError:
        return "", True
    except (OSError, UnicodeDecodeError):
        return "", False


def _declared_posture(path: Path) -> str:
    """The declaration at ``POSTURE_KEY_PATH`` in *path*.

    One of ``FAIL_CLOSED`` / ``FAIL_OPEN`` (the EXACT bare literal, and
    nothing else) / ``POSTURE_UNSET`` (no ``on_error`` KEY anywhere --
    a mention in a comment, in another key's name or in another key's
    value is not a declaration) / ``POSTURE_MALFORMED`` (a bare value
    that is not a
    posture) / ``POSTURE_UNREADABLE`` (``on_error`` appears, but this
    scanner cannot say what it means -- or the file is there and its
    BYTES could not be read at all).

    ONLY THE BARE LITERAL. This runs on a bare ``python3`` with no PyYAML,
    so it is a line scanner reading a file that is really YAML. It used to
    strip quote characters off the value, which meant a quoted
    ``"fail-open"`` -- writable in one command through the sanctioned CLI
    verb -- was HONOURED here while ``yaml.safe_load``, the config
    validator and the grant audit all saw a different string. An enforcer
    and its auditor that normalise differently are not one control. So the
    value is now compared as written, and every shape that would need
    normalising to reach a posture -- quoted scalars, block and folded
    scalars, anchors, flow mappings, tabs, multiple documents, a duplicated
    key -- reads as unreadable instead: fail-closed, announced on every
    invocation, and warned about by config validation.
    """
    text, readable = _read_declaration(path)
    if not readable:
        return POSTURE_UNREADABLE
    if not _declares_the_key(text):
        return POSTURE_UNSET
    if _unmodellable(text):
        return POSTURE_UNREADABLE
    matches = []
    stack: list = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip() or ":" not in line:
            continue
        indent = len(line) - len(line.lstrip(" "))
        key, _, value = line.strip().partition(":")
        key, value = key.strip(), value.strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if tuple(entry[1] for entry in stack) + (key,) == POSTURE_KEY_PATH:
            matches.append(value)
            continue
        if not value:
            stack.append((indent, key))
    # Not exactly one reachable declaration -- none (the key is somewhere
    # this scanner does not look) or several (a duplicate key, where YAML's
    # last-one-wins is a rule this scanner does not implement).
    if len(matches) != 1:
        return POSTURE_UNREADABLE
    if matches[0] in (FAIL_CLOSED, FAIL_OPEN):
        return matches[0]
    return POSTURE_MALFORMED


def user_posture_path() -> Path | None:
    """The user/machine-level settings file, or None if HOME is unknowable.

    Not a new file: this is the CLI's existing user-level settings path
    (``bpsai_pair.core.preferences``). The point is only that it sits
    outside every checkout, so no commit to a guarded repo can reach it.
    """
    try:
        return Path.home() / USER_POSTURE_REL
    except (RuntimeError, OSError):
        return None


def _resolves_inside(path: Path, repo_root: Path) -> bool:
    """True when *path* actually lands inside *repo_root*.

    The user-level scope means something only while the guarded artifact
    cannot write it, so a symlink at ``~/.paircoder/preferences.yaml``
    pointing back into the checkout would hand the loosening switch
    straight back to the repo. Resolved rather than compared literally,
    and ONLY the inside-the-repo case is refused -- a dotfile manager
    symlinking that file elsewhere in ``$HOME`` is ordinary, not an
    escalation.
    """
    try:
        target = os.path.realpath(str(path))
        root = os.path.realpath(str(repo_root))
    except OSError:
        return True  # cannot establish the file is out of reach -> ignore it
    return target == root or target.startswith(root + os.sep)


def _repo_declaration(repo_root: Path) -> str:
    """What the repo-tracked config declares, as a declaration STATE.

    Silent by design: the notice belongs to ``posture_notices``, which
    runs once per invocation, so a degraded commit does not print the
    same warning twice from two different frames.
    """
    config = repo_root / ".paircoder" / "config.yaml"
    if config.is_symlink():
        # Parity with both marker paths in this module: a symlink AT a
        # path that can disarm a gate is tampering, never honored. This
        # one can point outside the repo entirely, so the declaration
        # would not even be visible in the checkout it governs.
        return POSTURE_TAMPERED
    return _declared_posture(config)


def _user_declaration(repo_root: Path) -> tuple:
    """``(declaration state, path)`` for the user/machine-level file."""
    path = user_posture_path()
    if path is None:
        return POSTURE_UNSET, None
    if _resolves_inside(path, repo_root):
        return POSTURE_INSIDE_REPO, path
    return _declared_posture(path), path


def resolve_posture(repo_root: Path) -> tuple:
    """``(effective posture, honoured source, repo state, user state)``.

    TIGHTEN-ONLY: repo scope contributes a FLOOR, never a
    ceiling. ANY repo-level declaration -- an honoured ``fail-closed``, a
    refused ``fail-open``, a typo, a shape this gate cannot READ, a
    symlinked config -- pins ``fail-closed``, so tightening always wins
    and a repo asking to loosen gets the safe direction instead of a merge
    of two scopes. Unreadable counts because the only thing repo scope may
    declare is the tightening, so dropping it is a drop in the loosening
    direction. Only an ABSENT repo declaration lets a user-level
    ``fail-open`` through.
    """
    repo_state = _repo_declaration(repo_root)
    user_state, user_path = _user_declaration(repo_root)
    if repo_state == POSTURE_UNSET and user_state == FAIL_OPEN:
        return FAIL_OPEN, str(user_path), repo_state, user_state
    return FAIL_CLOSED, None, repo_state, user_state


def _unreadable_why(path: Path) -> str:
    """Why this gate could not read *path*, in its own words.

    Re-reads the file rather than threading a reason through the
    resolution tuple: this is a MESSAGE, and the decision it explains
    (fail-closed) has already been taken by the time it is written. A
    notice that asserts more than was found is worse than a vague one
    -- an operator sent looking for a declaration that does not exist
    cannot act on it.
    """
    text, readable = _read_declaration(path)
    if not readable:
        return POSTURE_UNREADABLE_BYTES_WHY
    proof = _absent_proof(text)
    if proof == PROOF_ESCAPED:
        return POSTURE_ESCAPED_TOKEN_WHY
    if proof == PROOF_UNDECODABLE:
        return POSTURE_UNDECODABLE_WHY
    return POSTURE_UNREADABLE_WHY


def posture_notices(repo_root: Path) -> list:
    """Every stderr line this checkout's posture configuration earns.

    Said on EVERY invocation, healthy or degraded, rather than once in a
    ledger row: the deployments most exposed to a
    weakened gate are the unattended ones, where nobody reads the ledger,
    and a silent non-default posture is indistinguishable from a healthy
    default one.
    """
    posture, source, repo_state, user_state = resolve_posture(repo_root)
    user = user_posture_path()
    lines = []
    if repo_state == POSTURE_TAMPERED:
        lines.append(CONFIG_SYMLINK_WARNING)
    if repo_state == FAIL_OPEN:
        lines.append(POSTURE_REPO_SCOPE_REFUSED.format(user=user))
    if repo_state in (POSTURE_UNREADABLE, POSTURE_MALFORMED):
        # Statements, not a ternary -- same payload formatting rule as
        # the user-side block below.
        why = POSTURE_MALFORMED_WHY
        if repo_state == POSTURE_UNREADABLE:
            why = _unreadable_why(repo_root / ".paircoder" / "config.yaml")
        lines.append(POSTURE_REPO_IGNORED.format(why=why))
    if user_state in (POSTURE_MALFORMED, POSTURE_INSIDE_REPO, POSTURE_UNREADABLE):
        # Statements, not a ternary: the payload must be `ruff format`
        # clean at line-length 88 AND at a consumer's 100, and a ternary
        # collapses past the latter.
        why = POSTURE_MALFORMED_WHY
        if user_state == POSTURE_INSIDE_REPO:
            why = POSTURE_INSIDE_REPO_WHY
        elif user_state == POSTURE_UNREADABLE:
            why = _unreadable_why(user)
        lines.append(POSTURE_USER_IGNORED.format(path=user, why=why))
    if posture == FAIL_OPEN:
        lines.append(POSTURE_ACTIVE_ANNOUNCEMENT.format(source=source))
    return lines


def posture_facts(repo_state: str, posture: str) -> tuple:
    """Every posture FACT this configuration earns, as ``(ledger action,
    bounded code)`` pairs, for the caller to record once per repo per
    branch.

    Separate from the notices because they answer different questions: a
    notice tells the operator in front of the terminal, a row tells a
    fleet sweep weeks later. Only bounded vocabulary is returned -- never
    a path, which names a home directory and would be published with the
    tracked ledger.
    """
    facts = []
    if repo_state == FAIL_OPEN:
        facts.append((POSTURE_REFUSED_ACTION, "repo_scope_fail_open"))
    if repo_state == POSTURE_UNREADABLE:
        facts.append((POSTURE_REPO_IGNORED_ACTION, "repo_scope_unreadable"))
    if repo_state == POSTURE_MALFORMED:
        facts.append((POSTURE_REPO_IGNORED_ACTION, "repo_scope_malformed"))
    if posture == FAIL_OPEN:
        facts.append((POSTURE_ACTIVE_ACTION, "user_scope"))
    return tuple(facts)
