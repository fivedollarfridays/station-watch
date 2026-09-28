"""The wrapper-unavailable bypass for `issue_filing_gate.py`: probing
whether the installed `bpsai-pair` CLI genuinely lacks the `gh` command
group, and the audited ledger write that is the ONLY thing allowed to turn
that finding into an allow.

Split out of the main hook module (architecture: file/function-count
caps). Stdlib-only, no `bpsai_pair` import, matching the main hook's own
runtime contract.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone

# The first `bpsai-pair` release carrying the `gh` command group this hook
# redirects toward. Named deliberately NOT `MIN_CLI_VERSION` --
# `claude_payload_manifest.compute_min_cli_version` scrapes every shipped
# hook for a module-level `MIN_CLI_VERSION = "X.Y.Z"` constant and lifts the
# payload's own installed-CLI floor to match it; using that name here would
# make `bpsai-pair upgrade` refuse the exact <=2.49.2 installs this bypass
# exists to unbrick.
# 2.49.3 introduced the `gh` command group, but its exec seam was broken on
# Windows (argv space-shattered, exit code lost) until the subprocess.run
# rewrite -- pointing a Windows operator at 2.49.3-2.49.5 would redirect
# them to a wrapper that still corrupts every multiline --body. The floor
# this message advertises is therefore the first release where the wrapper
# WORKS everywhere, not merely exists. 2.49.6 was tagged but never published,
# and the fixed exec seam first ships in 2.49.8.
WRAPPER_MIN_VERSION = "2.49.8"

# The gh-inert bypass marker: a PreToolUse hook can only allow/deny, never
# rewrite argv, and `gh` has no `--reason` flag -- an ALLOWED command still
# carries whatever this hook lets through straight into `gh`'s own parser.
# A leading shell env-assignment (`NAME=value`, POSIX assignment grammar)
# is the one marker shape that is inherently invisible to the program it
# precedes: the shell consumes and strips it before `gh` ever sees argv, so
# no flag-shaped token can leak through.
BYPASS_ENV_NAME = "PAIRCODER_FILING_BYPASS_REASON"

ISSUE_FILING_GATE_WRAPPER_UNAVAILABLE_BYPASS = (
    "issue_filing_gate_wrapper_unavailable_bypass"
)

#: Click/Typer's own phrasing for a missing `gh` command group -- "Error: No
#: such command 'gh'." The command name is matched explicitly (not a bare
#: "no such command" substring): a generic match could misclassify a NEWER
#: CLI's usage error for an unrelated reason as evidence the group is
#: missing, and wrongly grant a bypass a properly-installed CLI never
#: needed.
_NO_SUCH_GH_COMMAND_RE = re.compile(r"no such command\s+'gh'", re.IGNORECASE)

_WRAPPER_PROBE_TIMEOUT = 10
_MAX_BYPASS_REASON_LEN = 500
#: A `scheme://user:pass@host` credential embedded in a free-typed reason --
#: this ledger is tracked and committed, so a pasted error message must not
#: carry a credential into git history. No `bpsai_pair.telemetry.privacy`
#: import available here (stdlib-only contract), so this is a narrow,
#: purpose-built stand-in for `redact_url_credentials`, not a general PII
#: scrubber.
_CREDENTIALED_URL_RE = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@")

#: Recognizable secret-token shapes in operator-typed free text (security
#: audit P1 on the lane that introduced this module): GitHub tokens
#: (classic/fine-grained/oauth/server/refresh prefixes) and Bearer values.
#: The ledger this text lands in is tracked and committed, so redaction
#: here is the last line before a pasted secret becomes permanent git
#: history. Still deliberately pattern-based and stdlib-only -- not a
#: general secret scanner; the scheme://user:pass@ pattern above now
#: covers ALL schemes (git, ssh, ftp), not just http(s).
_SECRET_TOKEN_RES = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
)
_REDACTED = "[redacted-credential]"

_MAX_PAIRCODER_DIR_WALK = 64


def wrapper_genuinely_unavailable(command: str, shadow_re: re.Pattern) -> bool:
    """True only when the installed `bpsai-pair` CLI can be PROVEN to lack
    the `gh` command group -- never when that simply could not be checked.

    Denies (returns False) on every unprovable shape: `bpsai-pair` shadowed
    by a function/alias/assignment anywhere in *command* (the same taint
    *shadow_re* already withholds the wrapper-invocation exemption for -- a
    probe result can't be trusted from a name that command text itself is
    trying to retarget), `bpsai-pair` not found on PATH at all (unauditable:
    nothing here can distinguish "genuinely absent" from "the probe itself
    is broken"), the wrapper genuinely present, or a probe result this hook
    cannot interpret one way or the other.
    """
    if shadow_re.search(command):
        return False
    binary = shutil.which("bpsai-pair")
    if binary is None:
        return False
    # Windows resolves the CURRENT DIRECTORY ahead of PATH for bare command
    # names -- a `bpsai-pair.exe`/`.bat` planted in the checkout the hook
    # fires from would be the resolved binary and would get EXECUTED by the
    # probe below. A CLI that genuinely lives inside the checkout being
    # policed is indistinguishable from a plant, so it is refused outright:
    # unprovable means no bypass, same as every other unprovable shape.
    try:
        resolved = os.path.abspath(binary)
        if resolved.startswith(os.path.abspath(os.getcwd()) + os.sep):
            return False
    except OSError:
        return False
    try:
        probe = subprocess.run(
            [binary, "gh", "--help"],
            capture_output=True,
            text=True,
            timeout=_WRAPPER_PROBE_TIMEOUT,
        )
    except Exception:
        return False
    if probe.returncode == 0:
        return False
    output = (probe.stdout or "") + (probe.stderr or "")
    return bool(_NO_SUCH_GH_COMMAND_RE.search(output))


def find_paircoder_dir(cwd: str) -> str | None:
    """The first `<ancestor>/.paircoder` directory walking UP from *cwd*,
    stopping at the first bare `.git` sighting too (a nested checkout with
    no `.paircoder` of its own must not walk past its own root into an
    unrelated ancestor repo's ledger). Mirrors
    `pr_merge_guard_gate._find_paircoder_dir`'s own walk, kept as a literal
    duplicate rather than a shared import per each hook's stdlib-only,
    no-sibling-module-outside-its-own-family contract."""
    current = os.path.abspath(cwd)
    for _ in range(_MAX_PAIRCODER_DIR_WALK):
        if os.path.isdir(os.path.join(current, ".paircoder")):
            return os.path.join(current, ".paircoder")
        if os.path.exists(os.path.join(current, ".git")):
            return None
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent
    return None


def resolve_bypass_user() -> str:
    """Mirrors `core/bypass_log.py`'s own `_resolve_user` -- a literal
    duplicate, not an import, per this hook's stdlib-only contract."""
    try:
        user = getpass.getuser()
        if user:
            return user
    except Exception:
        pass
    return os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"


def _scrub_bypass_reason(reason: str) -> str:
    reason = _CREDENTIALED_URL_RE.sub(r"\1", reason)
    for pattern in _SECRET_TOKEN_RES:
        reason = pattern.sub(_REDACTED, reason)
    if len(reason) > _MAX_BYPASS_REASON_LEN:
        reason = reason[:_MAX_BYPASS_REASON_LEN] + "...[truncated]"
    return reason


def grant_bypass(cwd: str, reason: str) -> bool:
    """Append one audit row to `<repo_root>/.paircoder/history/
    bypass_log.jsonl`, in the same schema `core.bypass_log.log_bypass`
    writes; return True only once that write has actually succeeded.

    Audit-or-deny: no `.paircoder/` found, or the ledger append itself
    fails, both return False -- an unauditable bypass is never granted.
    """
    paircoder_dir = find_paircoder_dir(cwd)
    if paircoder_dir is None:
        return False
    entry = {
        "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
        "command": "issue_filing_gate",
        "target": "wrapper_unavailable",
        "reason": _scrub_bypass_reason(reason),
        "bypass_type": ISSUE_FILING_GATE_WRAPPER_UNAVAILABLE_BYPASS,
        "user": resolve_bypass_user(),
        "session_id": os.environ.get("CLAUDE_SESSION_ID", ""),
        "cwd": cwd,
        "metadata": {"probe": "bpsai-pair gh --help"},
    }
    log_path = os.path.join(paircoder_dir, "history", "bypass_log.jsonl")
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    except OSError:
        return False
    return True
