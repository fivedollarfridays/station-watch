"""Predicate-binary trust checks for `pr_merge_guard.py`.

Split out of the main hook module (architecture: file/function-count caps)
-- these functions answer one question, "should this resolved path be
trusted at all", independent of HOW it was found (`BPSAI_PAIR_BIN`, the
repo's own tree, or PATH). Stdlib-only, no `bpsai_pair` import, matching the
main hook's own runtime contract.

The permission floor is PLATFORM-DISPATCHED, because the question "could
an identity other than this install's owner have dropped this binary" has
two different answers on the two platforms, and only one of them is a mode
bit:

* POSIX -- `os.stat().st_mode` is authoritative. World-writable, or
  group-writable by a group this process is not in, is refused.
* Windows -- `os.stat().st_mode` is NOT authoritative and carries no
  information at all: CPython synthesizes it from the READONLY file
  attribute, so every writable file reports 0o666/0o777 and EVERY
  directory reports 0o777, `System32` included. Running the POSIX test
  there refuses every candidate on every host, and since this guard fails
  closed by design that blocked Windows operators from the sanctioned
  merge path entirely. The NTFS ACL is consulted instead; see
  `_check_trust_nt` for exactly what is and is not defended there.
"""

from __future__ import annotations

import os
import stat
import subprocess

#: Access-mask bits that let a holder replace, remove, or re-permission
#: the object -- the Windows analogue of POSIX write. Read/execute and
#: FILE_WRITE_ATTRIBUTES are deliberately excluded: neither lets an
#: attacker substitute the binary's contents.
_NT_WRITE_BITS = (
    0x00000002  # FILE_WRITE_DATA / FILE_ADD_FILE
    | 0x00000004  # FILE_APPEND_DATA / FILE_ADD_SUBDIRECTORY
    | 0x00010000  # DELETE
    | 0x00040000  # WRITE_DAC
    | 0x00080000  # WRITE_OWNER
    | 0x10000000  # GENERIC_ALL
    | 0x40000000  # GENERIC_WRITE
)

#: `WELL_KNOWN_SID_TYPE` values for the identities that stand in for POSIX
#: "other": anyone at all, any authenticated principal, and every local
#: interactive account. An ACE granting one of these write is the
#: PATH-hijack shape. Named local users and domain groups are NOT
#: enumerated -- see `_check_trust_nt`'s residual gaps.
_NT_OUTSIDER_SIDS = (
    1,  # WinWorldSid              (Everyone, S-1-1-0)
    17,  # WinAuthenticatedUserSid (S-1-5-11)
    27,  # WinBuiltinUsersSid      (BUILTIN\Users, S-1-5-32-545)
)


def _group_writable_by_outsider(path: str) -> bool:
    """True when *path* is group-writable AND that group is not one this
    PROCESS belongs to -- the shared-host PATH-hijack shape a blanket
    group-write allowance would otherwise miss entirely (a CI runner or
    multi-engineer VM directory whose group includes other identities, not
    just this one). A user-private-group install (`umask 002`, the
    directory's group contains only its owner) never trips this, since
    that group IS one this process belongs to. POSIX only."""
    st = os.stat(path)
    if not (st.st_mode & stat.S_IWGRP):
        return False
    try:
        my_groups = set(os.getgroups())
    except (AttributeError, OSError):
        my_groups = set()
    try:
        my_groups.add(os.getegid())
    except (AttributeError, OSError):
        pass
    return st.st_gid not in my_groups


def _nt_acl_types():
    """The two `advapi32` structures this module reads, built lazily.

    Defined inside a function, not at module scope: `ctypes.wintypes` does
    not import at all off Windows, and this module must stay importable on
    every platform.
    """
    import ctypes
    import ctypes.wintypes as wintypes

    class _ACL(ctypes.Structure):
        _fields_ = [
            ("AclRevision", wintypes.BYTE),
            ("Sbz1", wintypes.BYTE),
            ("AclSize", wintypes.WORD),
            ("AceCount", wintypes.WORD),
            ("Sbz2", wintypes.WORD),
        ]

    class _ACE(ctypes.Structure):
        """`ACCESS_ALLOWED_ACE`: header, access mask, then an inline SID."""

        _fields_ = [
            ("AceType", wintypes.BYTE),
            ("AceFlags", wintypes.BYTE),
            ("AceSize", wintypes.WORD),
            ("Mask", wintypes.DWORD),
            ("SidStart", wintypes.DWORD),
        ]

    return _ACL, _ACE


def _nt_outsider_write_grant(path: str):
    """The SID string of an ACE granting an outsider write on *path*, or
    `None` when the DACL grants none. Raises when the ACL cannot be read --
    the caller treats that as untrusted, the way the POSIX branch treats a
    failed `stat`.

    ctypes/`advapi32` rather than a `pywin32` dependency or an `icacls`
    subprocess: these hooks are stdlib-only by contract, and `icacls`
    output is localized, so parsing it would be a correctness bug on any
    non-English host.
    """
    import ctypes
    import ctypes.wintypes as wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    acl_type, ace_type = _nt_acl_types()
    dacl = ctypes.POINTER(acl_type)()
    descriptor = ctypes.c_void_p()
    rc = advapi32.GetNamedSecurityInfoW(
        wintypes.LPCWSTR(path),
        ctypes.c_int(1),  # SE_FILE_OBJECT
        wintypes.DWORD(0x00000004),  # DACL_SECURITY_INFORMATION
        None,
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if rc != 0:
        raise OSError(0, ctypes.FormatError(rc).strip(), path, rc)
    try:
        if not dacl:
            # A NULL DACL grants everyone full control.
            return "S-1-1-0 (null DACL)"
        return _nt_first_outsider_ace(advapi32, dacl, ace_type)
    finally:
        kernel32.LocalFree(descriptor)


def _nt_first_outsider_ace(advapi32, dacl, ace_type):
    """Scan *dacl*'s allow ACEs for an outsider write grant; SID or `None`.

    DENY and audit ACEs are skipped rather than netted out, and inherit-only
    ACEs (which govern children, not this object) are ignored.
    """
    import ctypes
    import ctypes.wintypes as wintypes

    outsiders = [_nt_well_known_sid(advapi32, kind) for kind in _NT_OUTSIDER_SIDS]
    for index in range(dacl.contents.AceCount):
        ace_ptr = ctypes.c_void_p()
        if not advapi32.GetAce(dacl, wintypes.DWORD(index), ctypes.byref(ace_ptr)):
            continue
        ace = ctypes.cast(ace_ptr, ctypes.POINTER(ace_type)).contents
        if ace.AceType != 0:  # ACCESS_ALLOWED_ACE_TYPE
            continue
        if ace.AceFlags & 0x08:  # INHERIT_ONLY_ACE
            continue
        if not (ace.Mask & _NT_WRITE_BITS):
            continue
        sid = ctypes.c_void_p(ace_ptr.value + ace_type.SidStart.offset)
        for outsider in outsiders:
            if advapi32.EqualSid(sid, ctypes.cast(outsider, ctypes.c_void_p)):
                return _nt_sid_string(advapi32, sid)
    return None


def _nt_well_known_sid(advapi32, kind: int):
    """A buffer holding the well-known SID of `WELL_KNOWN_SID_TYPE` *kind*."""
    import ctypes
    import ctypes.wintypes as wintypes

    size = wintypes.DWORD(0)
    # Hoisted into locals so the second call fits on ONE line under both
    # the 88- and the 100-column payload formatters.
    kind_arg = ctypes.c_int(kind)
    size_ref = ctypes.byref(size)
    # First call is the sizing probe; it fails and sets `size`.
    advapi32.CreateWellKnownSid(kind_arg, None, None, size_ref)
    buffer = ctypes.create_string_buffer(size.value)
    if not advapi32.CreateWellKnownSid(kind_arg, None, buffer, size_ref):
        raise OSError(0, "CreateWellKnownSid failed", None, ctypes.get_last_error())
    return buffer


def _nt_sid_string(advapi32, sid) -> str:
    """*sid* rendered as `S-1-...`, or a placeholder if it cannot be."""
    import ctypes
    import ctypes.wintypes as wintypes

    text = wintypes.LPWSTR()
    if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        return "<unprintable SID>"
    try:
        return text.value or "<unprintable SID>"
    finally:
        ctypes.WinDLL("kernel32").LocalFree(text)


def _check_trust_nt(resolved: str):
    r"""Windows permission floor: `None` (trusted) or a denial reason.

    Refuses when the NTFS DACL of *resolved* or its containing directory
    grants write, delete, or re-permission rights to Everyone,
    Authenticated Users, or BUILTIN\Users -- the Windows statement of "an
    identity other than this install's owner could substitute the binary".
    An unreadable ACL is refused, matching the POSIX branch's treatment of
    a failed `stat`.

    What Windows operators DO get: the world/all-local-users hijack shape,
    checked against the real ACL rather than a mode bit that carries no
    information there, plus the git-tracked refusal in `_is_git_tracked`,
    which is platform-independent.

    What Windows operators do NOT get, stated plainly so that nobody reads
    more protection into this than exists:

    * No equivalent of the POSIX outsider-GROUP check. A grant to a named
      local user, or to a domain group this operator is not a member of,
      is NOT detected -- only the three well-known "everyone-ish" SIDs
      above are compared. A targeted multi-account or domain-joined
      hijack is out of scope here.
    * DENY ACEs are not netted out. A DACL that grants Everyone write and
      then denies it is refused anyway; that is a false positive in the
      safe direction, not a hole.
    * Ownership is not examined, so the "attacker controls a directory
      writable only by its owner" gap is the same on both platforms.
    * The two gaps the POSIX branch already documents -- the TOCTOU window
      before `ask_predicate` execs the path, and checking only the
      immediate containing directory rather than the whole ancestor chain
      -- apply identically here.
    """
    directory = os.path.dirname(resolved)
    for candidate, label in ((directory, "directory"), (resolved, "file")):
        try:
            grantee = _nt_outsider_write_grant(candidate)
        except Exception as exc:  # OSError, or ctypes/advapi32 unavailable
            return (
                f"resolved to {resolved!r}, but the Windows ACL of its "
                f"{label} {candidate!r} could not be read "
                f"({type(exc).__name__}: {exc}) -- refusing to trust it"
            )
        if grantee is not None:
            return (
                f"resolved to {resolved!r}, but its {label} {candidate!r} "
                f"grants write to {grantee} (a PATH-hijack shape) -- "
                "refusing to trust it"
            )
    return None


def _check_trust_posix(resolved: str):
    """POSIX permission floor: `None` (trusted) or a denial reason.

    Refuses a candidate that -- itself, or its containing directory -- is
    WORLD-writable, or group-writable by a group this process does not
    belong to, a meaningful, testable floor. Deliberately NOT extended to a
    blanket group-write refusal: on a user-private-group system (the common
    default -- `umask 002`, each user's primary group contains only that
    user) an ordinary venv or `pip install --user` directory is
    group-writable by the SAME user who owns it, and flagging that would
    fail closed on totally normal installs.

    This is NOT full supply-chain verification. Three acknowledged residual
    gaps: an attacker who controls a directory writable only by its OWNER,
    earlier in the search order than the real install, still wins; there is
    an inherent, unclosed TOCTOU window between this check and
    `ask_predicate` actually exec'ing the resolved path (closing that fully
    would mean opening the file into a held descriptor and exec'ing THAT,
    which `subprocess.run`'s argv-based API does not offer); and this stats
    only *resolved*'s immediate containing directory, not its full ancestor
    chain -- a world-writable directory further up the tree (a shared
    parent above the venv itself) could let an attacker recreate the
    intermediate directories/file even though the immediate directory
    looks fine at check time (security-review follow-up). All three are
    the same class of residual risk `git_commit_guard.py`'s own bare `git`
    PATH invocation already carries; this floor closes the cheap version of
    the attack the audit named, not every version.
    """
    directory = os.path.dirname(resolved)
    for candidate, label in ((directory, "directory"), (resolved, "file")):
        try:
            mode = os.stat(candidate).st_mode
        except OSError as exc:
            return f"could not stat {candidate!r} ({type(exc).__name__}: {exc})"
        if mode & stat.S_IWOTH:
            return (
                f"resolved to {resolved!r}, but its {label} {candidate!r} is "
                "world-writable (a PATH-hijack shape) -- refusing to trust it"
            )
    if _group_writable_by_outsider(directory):
        return (
            f"resolved to {resolved!r}, but {directory!r} is writable by a "
            "group this process does not belong to (a PATH-hijack shape on "
            "a shared host) -- refusing to trust it"
        )
    return None


def _check_trust(resolved: str):
    """`None` (trusted) or a denial reason for the absolute, symlink-
    resolved path *resolved*.

    `os.path.realpath` (not `abspath`) is required upstream of this check --
    a symlinked candidate's own directory permissions are irrelevant; what
    matters is the directory the link actually resolves into, which is what
    actually gets executed.

    Dispatches to the permission model that actually governs the file.
    Neither branch is full supply-chain verification, and each documents
    its own residual gaps; they are NOT the same set.
    """
    if os.name == "nt":
        return _check_trust_nt(resolved)
    return _check_trust_posix(resolved)


def _is_git_tracked(cwd: str, path: str):
    """``True``/``False``/``None`` (undeterminable) for whether *path* is
    tracked by git in *cwd*'s checkout.

    Security-audit finding: a repo-tree venv candidate sits INSIDE the very
    checkout this hook is evaluating a merge for -- normally the event's
    own PR branch. A `.venv`/`venv` directory is conventionally
    gitignored, so a genuine local install never appears here; but nothing
    stops a PR's author from committing a malicious script at e.g.
    `.venv/bin/bpsai-pair` in their own branch, which git preserves as an
    ordinary, non-world/group-writable executable file -- invisible to
    `_check_trust`'s permission-based floor entirely. Refusing any
    candidate git considers TRACKED closes that: tracked content is
    exactly the PR's own committed diff, the thing this hook exists to
    gate, never something to trust as its own predicate. ``None`` (git
    missing, `cwd` not a repo, or any other unexpected failure) is treated
    by the caller as "cannot verify" -- skip the candidate rather than
    guess either way. Platform-independent: this one is unaffected by the
    POSIX/Windows split above.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, "ls-files", "--error-unmatch", "--", path],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return None
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    return None
