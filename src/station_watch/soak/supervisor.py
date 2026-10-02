"""Supervise a soak: three real children against one Log, sampled, then judged.

:func:`run_soak` starts the ``run``, ``watchdog`` and ``board`` subcommands as real
subprocesses (the ``run`` child through ``runner_argv`` -- the seam HF3.11 swaps for
its synthetic loop), samples them every ``--sample-s``, and fails the soak naming any
child that exits early and its exit code. At the end it stops every child (SIGTERM,
then SIGKILL after a bounded wait), runs HF3.3's session QA on the Log, reduces the
samples with :func:`~station_watch.soak.verdict.soak_verdict`, and writes a
measurement file (through :func:`~station_watch.evaluate.provenance.write_measurement`,
with ``--out`` resolved by :func:`~station_watch.evaluate.provenance.confine_out`) plus
a markdown table beside it. It exits 0 only when the soak passed and every child
stayed up.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import time
from pathlib import Path

import yaml

from station_watch.board.reader import BoardLogError, LogReader
from station_watch.config import validate_required_keys
from station_watch.evaluate.provenance import (
    DETECTOR,
    build_provenance,
    confine_out,
    write_measurement,
)
from station_watch.qa import session_qa, validate_qa_config
from station_watch.records import VerdictState
from station_watch.soak.report import render_table, reproduce_command
from station_watch.soak.sampler import take_sample
from station_watch.soak.verdict import load_soak_thresholds, soak_verdict

_EXE = [sys.executable, "-m", "station_watch"]
_POLL_S = 0.25
_TERM_GRACE_S = 5.0


def _load_config(path: str) -> dict:
    data = yaml.safe_load(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError(f"station config must be a YAML mapping, got {type(data).__name__}")
    validate_required_keys(data)
    return data


def _duration_s(args) -> float:
    if args.hours is not None:
        return float(args.hours) * 3600.0
    return float(args.minutes) * 60.0


def _is_live_source(source: str) -> bool:
    """A bare device index (all digits) is a live camera; anything else is a file."""
    return str(source).isdigit()


def _child_specs(args, runner_argv: list[str]) -> dict:
    """The argv for each named child: the runner (swappable) plus watchdog and board."""
    return {
        "run": runner_argv,
        "watchdog": [*_EXE, "watchdog", "--config", args.config, "--log", args.log],
        "board": [
            *_EXE,
            "board",
            "--config",
            args.config,
            "--log",
            args.log,
            "--port",
            str(args.board_port),
        ],
    }


def _start_children(specs: dict) -> dict:
    return {name: subprocess.Popen(argv) for name, argv in specs.items()}


def scan_children(children: dict) -> str | None:
    """A failure string naming the first child that has exited and its code, else None."""
    for name, child in children.items():
        code = child.poll()
        if code is not None:
            return f"child {name} exited early with code {code}"
    return None


def _stop_children(children: dict) -> None:
    """SIGTERM every child, then SIGKILL any that outlasts a bounded grace wait."""
    for child in children.values():
        if child.poll() is None:
            child.terminate()
    deadline = time.monotonic() + _TERM_GRACE_S
    for child in children.values():
        remaining = max(0.0, deadline - time.monotonic())
        try:
            child.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=_TERM_GRACE_S)


def _run_loop(children, *, duration_s, sample_s, log_path, board_port):
    """Sample every ``sample_s`` until ``duration_s``; stop early if a child dies.

    Returns ``(samples, early_failure)``. The wait is bounded by ``duration_s`` -- it
    polls children and takes samples, never blocks open-ended.
    """
    pids = {name: child.pid for name, child in children.items()}
    start = time.monotonic()
    samples = []
    next_sample = 0.0
    while True:
        elapsed = time.monotonic() - start
        failure = scan_children(children)
        if failure is not None:
            return samples, failure
        if elapsed >= next_sample:
            samples.append(
                take_sample(elapsed_s=elapsed, pids=pids, log_path=log_path, board_port=board_port)
            )
            next_sample += sample_s
        if elapsed >= duration_s:
            return samples, None
        time.sleep(min(_POLL_S, sample_s))


def _count_false_flags(log_path) -> int:
    """Faults raised over the session: the count of ``fault`` verdicts in the Log."""
    try:
        with LogReader(log_path) as reader:
            return sum(
                1 for v in reader.iter_newest("verdict", limit=-1) if v.state == VerdictState.FAULT
            )
    except BoardLogError:
        return 0


def _provenance(config: dict, config_path: str, dataset_kind: str) -> dict:
    return build_provenance(
        dataset=str(config.get("station_id", "station")),
        dataset_kind=dataset_kind,
        manifest_sha256="none",
        config_sha256=hashlib.sha256(Path(config_path).read_bytes()).hexdigest(),
        detector=DETECTOR,
        clips=0,
        sessions=[],
    )


def _assemble(verdict: dict, qa, duration_s: float, *, reported_flags: int | None) -> dict:
    """Overall metrics: the verdict metrics, the QA fractions, the duration, the status."""
    qa_pass = qa.status == "pass"
    overall = "pass" if (verdict["status"] == "pass" and qa_pass) else verdict["status"]
    if verdict["status"] == "pass" and not qa_pass:
        overall = "fail"
    metrics = {
        "status": overall,
        "failures": list(verdict["failures"]) + ([] if qa_pass else [f"QA: {qa.reason}"]),
        "duration_s": duration_s,
        "qa_status": qa.status,
        "qa_unobservable_fraction": qa.unobservable_fraction,
        "qa_observable_s": qa.observable_s,
        **verdict["metrics"],
    }
    if reported_flags is not None:
        metrics["false_flags_reported"] = reported_flags
    return metrics


def _finish(args, config, metrics: dict, resolved_out: Path) -> int:
    provenance = _provenance(config, args.config, args.dataset_kind)
    command = reproduce_command(args)
    write_measurement(resolved_out, provenance, metrics)
    resolved_out.with_suffix(".md").write_text(render_table(provenance, metrics, command))
    print(f"station-watch soak: {metrics['status'].upper()}")
    for failure in metrics["failures"]:
        print(f"  - {failure}")
    return 0 if metrics["status"] == "pass" else 1


def run_soak(args, *, runner_argv: list[str]) -> int:
    """Drive a soak end to end; 0 only on pass, 1 on fail, raises StartupError on K9."""
    config = _load_config(args.config)
    thresholds = load_soak_thresholds(config)
    validate_qa_config(config)
    resolved_out = confine_out(Path(args.out), args.dataset_kind, force_out=args.force_out)
    duration_s = _duration_s(args)

    children = _start_children(_child_specs(args, runner_argv))
    try:
        samples, early_failure = _run_loop(
            children,
            duration_s=duration_s,
            sample_s=args.sample_s,
            log_path=args.log,
            board_port=args.board_port,
        )
    finally:
        _stop_children(children)

    if early_failure is not None:
        return _finish(args, config, _early_metrics(early_failure, duration_s), resolved_out)

    live = _is_live_source(args.source)
    raised = _count_false_flags(args.log)
    verdict = soak_verdict(samples, thresholds, false_flags=0 if live else raised)
    qa = session_qa(config, args.log)
    metrics = _assemble(verdict, qa, duration_s, reported_flags=raised if live else None)
    return _finish(args, config, metrics, resolved_out)


def _early_metrics(failure: str, duration_s: float) -> dict:
    return {"status": "fail", "failures": [failure], "duration_s": duration_s}


__all__ = ["run_soak", "scan_children"]
