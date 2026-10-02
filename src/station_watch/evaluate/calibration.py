"""The held-out calibration check (HF3.8): prove a test split never touched calibration.

Thresholds are set from the *calibration* split; a ``--split held_out`` run must be
scored against clips that calibration never saw, or the number is not a held-out
number. Before any clip is scored, :func:`held_out_calibration` collects the
calibration provenance the config in force points at -- the measurement file named
by ``detect.step_times_path`` and every file under the optional ``calibration.provenance``
config key -- and refuses (raising :class:`CalibrationLeakError`, which the harness
turns into :data:`~station_watch.evaluate.harness.EXIT_CALIBRATION_LEAK`, writing
nothing) when a held-out clip, session or recording date is traceable into any of
them, or when a calibration file predates the hashes and dates that would prove
separation.

Calibration dates are the union of every calibration file's ``recorded_on`` values
(so a v1 and a v2 threshold file computed from separate manifests are both covered)
and the ``recorded_on`` of every ``split: calibration`` session in the held-out
run's own manifest. When the config names no calibration provenance at all, the run
proceeds and the provenance records ``calibration: none``.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import yaml

from station_watch.evaluate.manifest import Manifest, sha256_file


class CalibrationLeakError(RuntimeError):
    """A held-out clip, session or date is traceable into the calibration split."""


@dataclass(frozen=True)
class _CalibrationFile:
    path: str
    sha256: str
    clip_sha256s: set[str]
    sessions: set[str]
    recorded_on: dict[str, str]  # calibration session id -> recording date


def _calibration_paths(config_path: str) -> list[str]:
    """The calibration provenance files the config names: step_times + calibration.provenance."""
    data = yaml.safe_load(Path(config_path).read_text()) or {}
    paths: list[str] = []
    step_times = data.get("detect", {}).get("step_times_path")
    if step_times:
        paths.append(str(step_times))
    for extra in data.get("calibration", {}).get("provenance", []) or []:
        paths.append(str(extra))
    return paths


def _load_calibration_file(path: str) -> _CalibrationFile:
    file = Path(path)
    if not file.exists():
        raise CalibrationLeakError(f"calibration provenance file not found: {path}")
    prov = json.loads(file.read_text()).get("provenance", {})
    if prov.get("clip_sha256s") is None or prov.get("recorded_on") is None:
        raise CalibrationLeakError(
            f"calibration file {path} cannot prove separation: calibration provenance "
            f"predates clip hashes and dates; re-run calibration"
        )
    return _CalibrationFile(
        path=path,
        sha256=sha256_file(file),
        clip_sha256s=set(prov["clip_sha256s"]),
        sessions=set(prov.get("sessions", [])),
        recorded_on=dict(prov["recorded_on"]),
    )


def _calibration_dates(
    cal_files: list[_CalibrationFile], manifest: Manifest
) -> dict[str, list[str]]:
    """Date -> the calibration session ids recorded on it, over every source."""
    dates: dict[str, list[str]] = defaultdict(list)
    for cal in cal_files:
        for session, date in cal.recorded_on.items():
            if date:
                dates[date].append(session)
    seen: set[str] = set()
    for clip in manifest.clips:
        if clip.split == "calibration" and clip.recorded_on and clip.session not in seen:
            seen.add(clip.session)
            dates[clip.recorded_on].append(clip.session)
    return dates


def _check_separation(manifest: Manifest, cal_files: list[_CalibrationFile]) -> None:
    """Raise :class:`CalibrationLeakError` naming the first held-out leak found."""
    cal_shas = {sha: cal.path for cal in cal_files for sha in cal.clip_sha256s}
    cal_sessions = {s: cal.path for cal in cal_files for s in cal.sessions}
    cal_dates = _calibration_dates(cal_files, manifest)
    held = [clip for clip in manifest.clips if clip.split == "held_out"]
    for clip in held:
        if clip.clip_sha256 in cal_shas:
            raise CalibrationLeakError(
                f"held-out clip {clip.rel_path} has the same bytes (sha256 "
                f"{clip.clip_sha256}) as a clip in calibration file {cal_shas[clip.clip_sha256]}"
            )
    held_dates = {clip.session: clip.recorded_on for clip in held}
    for session, date in held_dates.items():
        if session in cal_sessions:
            raise CalibrationLeakError(
                f"held-out session {session} also appears in calibration file "
                f"{cal_sessions[session]}"
            )
        if not date:
            raise CalibrationLeakError(
                f"held-out session {session} has no recorded_on; cannot prove it is "
                f"separate from the calibration split"
            )
        if date in cal_dates:
            shared = ", ".join(sorted(set(cal_dates[date])))
            raise CalibrationLeakError(
                f"held-out session {session} recorded_on {date} collides with calibration "
                f"date {date} from session(s) {shared}"
            )


def held_out_calibration(config_path: str, manifest: Manifest) -> object:
    """Refuse on any calibration leak; return the provenance ``calibration`` value.

    ``"none"`` when the config names no calibration provenance, else a list of
    ``{"path", "sha256"}`` for each calibration file the held-out run was proven
    clear of. Raises :class:`CalibrationLeakError` on the first leak or stale file.
    """
    paths = _calibration_paths(config_path)
    if not paths:
        return "none"
    cal_files = [_load_calibration_file(path) for path in paths]
    _check_separation(manifest, cal_files)
    return [{"path": cal.path, "sha256": cal.sha256} for cal in cal_files]


__all__ = ["CalibrationLeakError", "held_out_calibration"]
