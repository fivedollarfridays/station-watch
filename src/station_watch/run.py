"""Per-process run identity.

A ``run_id`` is minted once per process start (UUID4) and prefixes every
``record_id`` so counters that reset to zero on restart can never collide with a
previous run's rows.
"""

from __future__ import annotations

import uuid


def new_run_id() -> str:
    """Return a fresh UUID4 string to identify one process run."""
    return str(uuid.uuid4())
