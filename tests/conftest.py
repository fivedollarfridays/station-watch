"""Shared pytest fixtures.

A real ``station-watch run`` keeps evidence frames under ``data/local/evidence``
by default (HF3.4). Pointing ``$STATION_WATCH_EVIDENCE_DIR`` at a session temp dir
here means the many subprocess ``run`` tests never splatter the source tree with
per-run evidence directories; a test that cares about the default or a specific
dir still passes ``--evidence-dir`` (or asserts the constant) explicitly.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolate_evidence_dir(tmp_path_factory):
    evidence_dir = tmp_path_factory.mktemp("evidence-root")
    previous = os.environ.get("STATION_WATCH_EVIDENCE_DIR")
    os.environ["STATION_WATCH_EVIDENCE_DIR"] = str(evidence_dir)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("STATION_WATCH_EVIDENCE_DIR", None)
        else:
            os.environ["STATION_WATCH_EVIDENCE_DIR"] = previous
