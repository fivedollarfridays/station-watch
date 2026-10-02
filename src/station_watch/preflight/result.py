"""The one result type a preflight check returns: ``CheckResult``.

Every check answers with a frozen ``CheckResult(name, status, detail)`` whose
``status`` is one of ``PASS``/``FAIL``/``WARN``/``SKIP``. Keeping the status
vocabulary here (not as bare strings scattered through the checks) means the
renderers and the exit-code rule read the same four names the checks write.
"""

from __future__ import annotations

from dataclasses import dataclass

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"
SKIP = "SKIP"
_STATUSES = (PASS, FAIL, WARN, SKIP)


@dataclass(frozen=True)
class CheckResult:
    """One preflight check's verdict: its ``name``, a ``status``, and a ``detail`` line."""

    name: str
    status: str
    detail: str = ""

    def __post_init__(self) -> None:
        if self.status not in _STATUSES:
            raise ValueError(f"unknown check status {self.status!r}, expected one of {_STATUSES}")


__all__ = ["CheckResult", "PASS", "FAIL", "WARN", "SKIP"]
