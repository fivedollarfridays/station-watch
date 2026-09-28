"""Constants shared by every ``base_branch_guard`` sibling module.

Split out of ``base_branch_guard.py`` (the entry point + import facade) so
each sibling module can import just the data it needs without triggering a
circular import back through the facade. Nothing here is behavior --
purely the shared vocabulary (env-var names, regexes, templates, path
constants) the rest of the guard is built from.
"""

from __future__ import annotations

import re
from pathlib import Path

ENGAGE_MARKER = "BPSAI_ENGAGE_MODE"

# The run's own worktree path(s), set ALONGSIDE `ENGAGE_MARKER` by the same
# `ClaudeCodeAdapter._build_env` call (`os.pathsep`-joined; today always
# exactly one entry, `self.working_dir`). Scopes the engage exemption to
# the directory the driver was actually dispatched into. REQUIRED, not
# optional: a producer that sets `ENGAGE_MARKER` without this companion
# gets NO exemption -- the narrowing cannot be opt-in.
ENGAGE_WORKTREE_MARKER = "BPSAI_ENGAGE_WORKTREE"

MUTATING_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")

# Every key a MUTATING_TOOLS tool_input might carry a write-target path
# under. `NotebookEdit` is the only tool that populates both today, but a
# future tool with its own alternate path field reproduces the shape --
# `mutating_targets` judges whichever of these are present, not just one.
PATH_KEYS = ("file_path", "notebook_path")
DEFAULT_PROTECTED = ("main", "master", "dev")

# Adjacent EXACT path components, never a substring: `.claude`/`agent-memory`
# must sit back-to-back so `evil.claude/agent-memory-backup/` stays denied.
AGENT_MEMORY_BOUNDARY = (".claude", "agent-memory")

# `git [global-opts...] commit`. The option alternation deliberately excludes
# subcommands, so `git log --grep commit` is not mistaken for a commit. Kept
# as the parser's fallback for text it cannot tokenize (unbalanced quotes).
GIT_COMMIT_RE = re.compile(
    r"\bgit\b(?:\s+(?:-C\s+\S+|-c\s+\S+|--git-dir=\S+|--work-tree=\S+|--\w[\w-]*))*"
    r"\s+commit\b"
)
GIT_DASH_C_RE = re.compile(r"\bgit\b\s+(?:-c\s+\S+\s+)*-C\s+(\S+)")

# Option tokens the tokenized git-commit detector treats as belonging
# BEFORE `commit`, not as a distinct subcommand -- kept in exact
# correspondence with `GIT_COMMIT_RE`'s alternation.
GIT_VALUE_FLAGS = ("-C", "-c")

# Shell interpreter basenames whose heredoc bodies are COMMAND TEXT the
# interpreter will actually execute, not data -- `bash <<EOF ... EOF`
# runs its body as shell commands, so a `git commit` inside it is real,
# unlike a `gh`/`cat`/`tee`/`python`/... heredoc body, which is a literal
# argument/file/string. Matched by BASENAME (see `_is_interpreter_token`
# in `base_branch_guard_parser.py`), so `/bin/bash`, `env bash`, and
# `Bash.exe` are all recognized the same as a bare `bash` token. A named
# constant (not inlined at the call site) so the recognized set is one
# place to extend.
INTERPRETER_BASENAMES = frozenset({"bash", "sh", "zsh", "dash", "ksh"})

# Shell control-operator tokens `shlex.shlex(..., punctuation_chars=True)`
# emits as their own token. Used to mark where a new simple-command
# "segment" begins, for the leading-`cd`-chain walk.
CONTROL_OPERATOR_TOKENS = frozenset({";", "&&", "||", "|", "&"})

# The one control operator whose following segment runs CONDITIONALLY on
# the previous one having failed: after `A || cd X`, the `cd` ran only if
# `A` failed, so the directory in force BEFORE the operator stays a
# candidate until an unconditionally-reached `cd` settles the question.
CONDITIONAL_OPERATOR = "||"

# Operators that end an `&&`/`||` chain outright. Everything after one of
# these runs REGARDLESS of how the chain before it ended, which is what
# makes them the point where a `cd` whose execution was conditional on
# that chain has to be admitted as "may never have happened". `&&`, `||`
# and `|` all keep the chain open.
SEQUENCE_OPERATOR_TOKENS = frozenset({";", "&"})

# A segment adjacent to `|` on EITHER side is a pipeline element, and
# bash gives every pipeline element its own subshell -- so a `cd` in it
# never reaches the parent. `&&` binds LOOSER than `|`, which is what
# makes this non-obvious: `A | cd X && git commit` parses as
# `(A | cd X) && git commit`, so the `cd` is inside the pipeline and the
# commit runs in the PARENT, at the original directory.
PIPELINE_OPERATOR = "|"

# `&` backgrounds the segment it TERMINATES -- `cd X & git commit` runs
# the `cd` in a background subshell and the commit in the parent. Unlike
# `|` this is one-sided: a `cd` that merely FOLLOWS a `&`
# (`A & cd X && git commit`) is an ordinary parent-shell command.
BACKGROUND_OPERATOR = "&"

# POSIX shell env-assignment prefix grammar (`NAME=value`). One or more of
# these can precede the real command in a segment; the leading-`cd`/`git`
# detection skips past all of them so an assignment prefix cannot defeat
# either.
ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def skip_env_assignments(tokens: list, index: int) -> int:
    """Advance *index* past any leading ``NAME=value`` env-assignment
    tokens at the start of a command segment.

    Colocated with `ENV_ASSIGNMENT_RE` (rather than living in
    `base_branch_guard_parser.py`, its sole caller until the heredoc
    interpreter-detection split needed the same primitive) so both
    `base_branch_guard_parser.py` and `base_branch_guard_heredoc.py` can
    share it without importing from each other.
    """
    while index < len(tokens) and ENV_ASSIGNMENT_RE.match(tokens[index]):
        index += 1
    return index


DENY_TEMPLATE = (
    "Protected base branch ({branch}). Run `bpsai-pair engage <backlog>` to "
    "dispatch this work, or create a feature branch first."
)

UPGRADE_DENY_TEMPLATE = (
    "Protected base branch ({branch}). This looks like `bpsai-pair upgrade` "
    "artifacts (`.claude`/config payload) -- there is no backlog to dispatch "
    "for an upgrade commit. Create a feature branch first."
)

BOOKKEEPING_DENY_TEMPLATE = (
    "Protected base branch ({branch}). This looks like bookkeeping "
    "(state/task/history updates) -- there is no backlog to dispatch for a "
    "bookkeeping commit. Create a feature branch first."
)

# Kept split: collapses to a 90-char line under a line-length=100 consumer
# config, which ruff-format-check would then flag as drift.
GENERIC_DENY_TEMPLATE = (
    "Protected base branch ({branch}). Create a feature branch first."
)  # fmt: skip

# `classify_change_shape` buckets a denied call's targets into one of
# these keys; `"code"` is the only shape that keeps the engage
# prescription -- any key absent here (an unrecognized or ambiguous
# shape) falls back to `GENERIC_DENY_TEMPLATE`.
DENY_TEMPLATE_BY_SHAPE = {
    "code": DENY_TEMPLATE,
    "upgrade": UPGRADE_DENY_TEMPLATE,
    "bookkeeping": BOOKKEEPING_DENY_TEMPLATE,
}

# Upgrade-artifact paths, relative to a repo root: the whole `.claude`
# payload tree plus the handful of top-level files `bpsai-pair upgrade`
# also refreshes.
UPGRADE_SHAPE_ROOT = ".claude"
UPGRADE_SHAPE_FILES = (".paircoder/config.yaml", "CLAUDE.md", "AGENTS.md")
UPGRADE_SHAPE_SUFFIXES = ("claude_payload_manifest.json",)

# Bookkeeping paths: the state/task/history subtrees `bpsai-pair`'s own
# task-completion workflow writes -- none of these is backlog-dispatchable
# code either.
BOOKKEEPING_SHAPE_FILES = (".paircoder/context/state.md",)
BOOKKEEPING_SHAPE_ROOTS = (".paircoder/tasks", ".paircoder/history")

OPT_OUT_MARKER = Path(".paircoder/hooks/base_branch_guard.off")

# Unified sink: same ledger `bpsai_pair.core.bypass_log` writes.
# `LEGACY_BYPASS_LOG_REL` is the pre-unification root-level path this hook
# used to write -- never read, moved, or deleted, only noted once if still
# present on a repo mid-transition.
BYPASS_LOG_REL = Path(".paircoder/history/bypass_log.jsonl")
LEGACY_BYPASS_LOG_REL = Path(".paircoder/bypass_log.jsonl")

# Every row this hook (or its sibling git_commit_guard.py) appends carries
# the CLI's own record shape (`timestamp`/`command`/`bypass_type`, matching
# `bpsai_pair.core.bypass_log.log_bypass`) ALONGSIDE its own extra fields
# (`gate`/`action`/`branch`/`tool`/`target`) -- both writers share this one
# ledger. `bypass_type` values below are registered in
# `core.bypass_log.KNOWN_BYPASS_TYPES`.
BYPASS_TYPE_BY_ACTION = {
    "marker_write": "base_branch_guard_marker_write",
    "marker_disarm": "base_branch_guard_marker_disarm",
    "init_first_commit": "git_commit_guard_init_first_commit",
    "engage_mode_exempt": "base_branch_guard_engage_mode_exempt",
    "agent_memory_write": "base_branch_guard_agent_memory_write",
}

ADVISORY_TEMPLATE = (
    "base_branch_guard: disabled by {marker} -- protected branch ({branch}) "
    "edits are NOT being guarded.\n"
)

SYMLINK_WARNING_TEMPLATE = (
    "base_branch_guard: {marker} is a symlink -- treating as tampering, "
    "not an opt-out. Guard remains ARMED.\n"
)

AUDIT_FAILURE_TEMPLATE = (
    "Protected base branch ({branch}). The {action} bypass at {marker} "
    "could not be recorded to {log_path} ({error}), so it was not "
    "honored -- an unauditable bypass is denied rather than granted "
    "silently. Fix the log path's permissions and retry."
)
