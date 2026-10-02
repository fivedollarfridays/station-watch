"""``occlusion``: rail-position readability vs camera angle (PLAN 3.4, "occlusion").

Clips are tagged with the camera angle they were shot at (``angle_deg``, degrees
from overhead). For each angle this runs HF2's Detect -- the
:class:`~station_watch.detect.positions.PositionTracker` and its N-frame
persistence rule -- over every clip at that angle through ``Detector.process`` and
reports, per angle:

* the fraction of slot judgments that came back ``part_unknown``,
* how many of those unknowns were caused by *occlusion* (a skin-toned hand blob
  over the position, cause ``occluded``) versus every other cause
  (``fiducial_missing`` / ``dark`` / ``blurred`` / ``out_of_frame``),
* the longest unbroken unknown run in seconds, and
* the count of slot judgments made.

A steep angle that lets a reaching hand cover a rail position is the failure this
surfaces: at that angle the occlusion-caused unknown fraction climbs, and it sits
in the table beside the shallower angle's.

The measurement reads slot judgments at a *per-frame* cadence -- it overrides the
station's ``emit_interval_s`` to ``0`` so every frame yields a judgment, because
an angle is scored on the unknown *share of frames*, not on the sparser
change-only stream a live station logs. It drives the real Detect path; only
``--synthetic`` swaps in a rendered hand blob with a known occluded share so the
measured occlusion fraction can be checked against it.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace

from station_watch.clock import offset_iso
from station_watch.detect.regions import CAUSE_OCCLUDED
from station_watch.records import ObservationKind

_BASE_TS = "2026-01-01T00:00:00.000000+00:00"
_RUN_ID = "physics-occlusion"
_SLOT_KINDS = frozenset(
    {ObservationKind.PART_PRESENT, ObservationKind.PART_ABSENT, ObservationKind.PART_UNKNOWN}
)


class Occlusion:
    """The occlusion script: group fps-and-angle-tagged clips by angle and score each."""

    name = "occlusion"

    def selects(self, clip) -> bool:
        """An occlusion input is tagged with the camera angle it was shot at."""
        return "angle_deg" in clip.tags

    def build_synthetic(self, work_dir):
        """The ``--synthetic`` proving input: hand blobs occluding a known frame share."""
        from station_watch.physics.occlusion_synthetic import build_synthetic

        return build_synthetic(work_dir)

    def measure(self, clips, config, backend_factory) -> dict:
        """Run Detect per angle over ``clips``; return the occlusion metrics block."""
        from station_watch.evaluate.manifest import ManifestError

        dense = replace(config, detect={**config.detect, "emit_interval_s": 0.0})
        by_angle: dict[str, list] = {}
        for clip in clips:
            frames = _read_frames(clip.clip_path)
            try:
                native = _native_fps(clip)
            except ValueError as exc:  # no fps tag and the file reports none: name the clip
                raise ManifestError(str(exc)) from exc
            per_target = _run_detector(frames, dense, native, backend_factory(clip))
            by_angle.setdefault(str(clip.tags["angle_deg"]), []).append(
                (clip, len(frames), native, per_target)
            )
        return _assemble(by_angle)


def _read_frames(clip_path) -> list:
    """Every frame of the clip, in order (frame id is the list index)."""
    from station_watch.capture.source import FrameSource

    source = FrameSource(str(clip_path))
    frames = []
    try:
        while True:
            frame = source.read()
            if frame is None:
                break
            frames.append(frame)
    finally:
        source.release()
    return frames


def _native_fps(clip) -> float:
    """The clip's native capture rate: its ``fps`` tag, else read from the file itself."""
    if "fps" in clip.tags:
        return float(clip.tags["fps"])
    from station_watch.capture.source import FrameSource

    source = FrameSource(str(clip.clip_path))
    try:
        fps = source.fps
    finally:
        source.release()
    if fps <= 0:
        raise ValueError(f"clip reports no fps and carries no fps tag: {clip.rel_path}")
    return fps


def _run_detector(frames, config, native: float, backend) -> dict[str, list]:
    """Drive Detect over every frame; return per rail position its slot-judgment stream."""
    from station_watch.detect.detector import Detector

    detector = Detector(config, _RUN_ID, keepout_backend=backend)
    per_target: dict[str, list] = {}
    for frame_id, frame in enumerate(frames):
        ts = offset_iso(_BASE_TS, frame_id / native)
        for obs in detector.process(frame, frame_id, ts):
            if obs.kind in _SLOT_KINDS:
                cause = obs.detector_output.get("cause")
                per_target.setdefault(obs.target, []).append((frame_id, obs.kind, cause))
    return per_target


def _longest_unknown_run(seq: list) -> int:
    """Longest run of consecutive ``part_unknown`` judgments (one per frame), in frames."""
    longest = run = 0
    for _frame_id, kind, _cause in seq:
        if kind == ObservationKind.PART_UNKNOWN:
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    return longest


def _occluded_frames(clip) -> int:
    """Rendered/labeled frames a position is occluded on this clip (ground truth)."""
    return sum(int(o["end_frame"]) - int(o["start_frame"]) for o in clip.occlusions)


def _angle_block(entries: list) -> dict:
    """Aggregate every clip at one angle into its row of the occlusion table."""
    judgments = unknown = occluded = occluded_frames = total_frames = 0
    by_cause: Counter = Counter()
    longest_s = 0.0
    for clip, frame_count, native, per_target in entries:
        total_frames += frame_count
        occluded_frames += _occluded_frames(clip)
        for seq in per_target.values():
            judgments += len(seq)
            for _frame_id, kind, cause in seq:
                if kind == ObservationKind.PART_UNKNOWN:
                    unknown += 1
                    by_cause[cause or "unspecified"] += 1
                    occluded += cause == CAUSE_OCCLUDED
            longest_s = max(longest_s, _longest_unknown_run(seq) / native)
    return {
        "clips": len(entries),
        "slot_judgments": judgments,
        "unknown_fraction": round(unknown / judgments, 4) if judgments else 0.0,
        "occlusion_unknown_fraction": round(occluded / judgments, 4) if judgments else 0.0,
        "rendered_occlusion_fraction": round(occluded_frames / total_frames, 4)
        if total_frames
        else 0.0,
        "unknown_by_cause": dict(by_cause),
        "longest_unknown_run_s": round(longest_s, 3),
    }


def _assemble(by_angle: dict[str, list]) -> dict:
    """Assemble the per-angle rows and the overall slot-judgment total."""
    blocks = {angle: _angle_block(entries) for angle, entries in by_angle.items()}
    return {
        "angles_deg": sorted(float(angle) for angle in blocks),
        "slot_judgments_total": sum(block["slot_judgments"] for block in blocks.values()),
        "by_angle": blocks,
    }


__all__ = ["Occlusion"]
