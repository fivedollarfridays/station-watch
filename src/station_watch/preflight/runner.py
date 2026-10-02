"""Run the ordered preflight checks under a timeout and render the result.

``CHECKS`` is the ordered name->callable registry (``config`` first, ``board``
last); HF3.13 extends it with a new entry without touching the others. Each check
runs on its own worker thread with a join timeout so a check that hangs or raises
becomes a ``FAIL`` naming the reason (K10) rather than taking the run down.
``run_preflight`` threads one :class:`PreflightContext` through them in order and
returns the per-check results; the renderers and the exit-code rule read those.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

import station_watch.preflight.camera_checks as cam
import station_watch.preflight.system_checks as sysc
from station_watch.preflight.context import PreflightContext
from station_watch.preflight.result import FAIL, CheckResult

# The one ordered check list. HF3.13 adds an entry; nothing else moves.
CHECKS: dict = {
    "config": sysc.check_config,
    "camera": cam.check_camera,
    "frames_live": cam.check_frames_live,
    "fiducial": cam.check_fiducial,
    "camera_stability": cam.check_camera_stability,
    "model_weights": sysc.check_model_weights,
    "log_writable": sysc.check_log_writable,
    "disk_space": sysc.check_disk_space,
    "clock": sysc.check_clock,
    "alarm_sinks": sysc.check_alarm_sinks,
    "board": sysc.check_board,
}

# A single check may never block the rest; this bounds frames_live's window, the
# 2 s board probe and the SQLite/disk calls with generous headroom.
_CHECK_TIMEOUT_S = 20.0


def _run_one(name: str, check, ctx: PreflightContext) -> CheckResult:
    """Run one check under the timeout; a timeout or any exception is a FAIL (K10)."""
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(check, ctx)
    try:
        return future.result(timeout=_CHECK_TIMEOUT_S)
    except FutureTimeout:
        return CheckResult(name, FAIL, f"timed out after {_CHECK_TIMEOUT_S}s")
    except Exception as exc:  # K10: a raising check is a FAIL that names the reason
        return CheckResult(name, FAIL, f"{type(exc).__name__}: {exc}")
    finally:
        pool.shutdown(wait=False)


def run_preflight(
    config_path: str,
    source: str,
    log_path: str,
    *,
    board_port: int | None = None,
    drift_s: float = 5.0,
    checks: dict | None = None,
) -> list[CheckResult]:
    """Run the checks in order against one station and return the per-check results."""
    registry = CHECKS if checks is None else checks
    ctx = PreflightContext(
        config_path=config_path,
        source=source,
        log_path=log_path,
        board_port=board_port,
        drift_s=drift_s,
    )
    results: list[CheckResult] = []
    try:
        for name, check in registry.items():
            results.append(_run_one(name, check, ctx))
    finally:
        if ctx.frame_source is not None:
            try:
                ctx.frame_source.release()
            except Exception:  # releasing a source must never mask the results
                pass
    return results


def preflight_ok(results: list[CheckResult]) -> bool:
    """A preflight passes when no check is a FAIL (WARN and SKIP do not fail it)."""
    return not any(result.status == FAIL for result in results)


def render_text(results: list[CheckResult]) -> str:
    """One aligned line per check, then ``PREFLIGHT PASS`` or ``PREFLIGHT FAIL``."""
    width = max((len(result.name) for result in results), default=0)
    lines = [
        f"{result.name:<{width}}  {result.status:<4}  {result.detail}".rstrip()
        for result in results
    ]
    lines.append("PREFLIGHT PASS" if preflight_ok(results) else "PREFLIGHT FAIL")
    return "\n".join(lines)


def render_json(results: list[CheckResult]) -> str:
    """The same results as JSON: a ``checks`` list and the overall ``ok`` boolean."""
    return json.dumps(
        {
            "checks": [
                {"name": r.name, "status": r.status, "detail": r.detail} for r in results
            ],
            "ok": preflight_ok(results),
        }
    )


__all__ = ["CHECKS", "run_preflight", "preflight_ok", "render_text", "render_json"]
