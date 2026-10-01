"""``fps_sweep``: frame rate vs keep-out event duration (PLAN 3.4).

For each labeled keep-out reach event, the clip is subsampled to 30, 15 and 5 fps by
dropping frames, HF2's Detect runs on the kept frames, and the sweep reports -- per
fps -- the frames that fell inside the event beside the predicted minimum ``f * d``,
plus whether the event was still detected and the aggregate detection rate. A low
frame rate that drops every frame of a short reach is the failure this surfaces: the
predicted and measured counts sit side by side so the gap is visible, never hidden.

The sweep consumes the HF2.8 manifest, selecting clips tagged with their native
``fps`` that carry keep-out intervals, and drives HF2.3's ``Detector`` with HF2.6's
injectable keep-out backend (``detect_people`` -- the real YOLOX model for real clips,
a scripted fake for ``--synthetic``). It reads frames only; it does not run the Judge.
"""

from __future__ import annotations

from station_watch.clock import offset_iso
from station_watch.records import ObservationKind

TARGETS = (30, 15, 5)
_BASE_TS = "2026-01-01T00:00:00.000000+00:00"
_RUN_ID = "physics-fps-sweep"


class FpsSweep:
    """The fps sweep script: selects fps-tagged keep-out clips and measures them."""

    name = "fps_sweep"

    def selects(self, clip) -> bool:
        """An fps_sweep input carries a native ``fps`` tag and a keep-out interval."""
        return "fps" in clip.tags and bool(clip.keepouts)

    def build_synthetic(self, work_dir):
        """The ``--synthetic`` proving input (a keep-out reach of known duration)."""
        from station_watch.physics.synthetic import build_synthetic

        return build_synthetic(work_dir)

    def measure(self, clips, config, backend_factory) -> dict:
        """Run the sweep over ``clips``; return the metrics block for the measurement file."""
        events = []
        for clip in clips:
            native = float(clip.tags["fps"])
            frames = _read_frames(clip.clip_path)
            for interval in clip.keepouts:
                events.append(
                    _measure_event(clip, interval, native, frames, config, backend_factory)
                )
        return _assemble(events)


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


def _kept_ids(total: int, native: float, fps: int) -> tuple[list[int], int]:
    """Frame ids kept when subsampling ``native`` fps down to ``fps`` by dropping frames."""
    stride = max(round(native / fps), 1)
    return [i for i in range(total) if i % stride == 0], stride


def _detected(frames, kept_ids, in_event, config, backend, native) -> bool:
    """True when Detect emits ``person_in_keepout`` on a kept frame inside the event."""
    from station_watch.detect.detector import Detector

    detector = Detector(config, _RUN_ID, keepout_backend=backend)
    in_event_set = set(in_event)
    for i in kept_ids:
        ts = offset_iso(_BASE_TS, i / native)
        for obs in detector.process(frames[i], i, ts):
            if obs.kind == ObservationKind.PERSON_IN_KEEPOUT and obs.frame_id in in_event_set:
                return True
    return False


def _measure_event(clip, interval, native, frames, config, backend_factory) -> dict:
    """One keep-out event across all fps targets: measured frames beside predicted f*d."""
    start, end = interval["start_frame"], interval["end_frame"]
    duration_s = (end - start) / native
    by_fps = {}
    for fps in TARGETS:
        kept, stride = _kept_ids(len(frames), native, fps)
        in_event = [i for i in kept if start <= i < end]
        by_fps[str(fps)] = {
            "stride": stride,
            "frames_in_event": len(in_event),
            "predicted_frames": round(fps * duration_s, 3),
            "detected": _detected(frames, kept, in_event, config, backend_factory(clip), native),
        }
    return {
        "clip": clip.rel_path,
        "zone": interval.get("zone"),
        "native_fps": native,
        "duration_s": round(duration_s, 3),
        "by_fps": by_fps,
    }


def _assemble(events: list[dict]) -> dict:
    """Aggregate per-fps detection across every event into the metrics block."""
    by_fps = {}
    for fps in TARGETS:
        key = str(fps)
        detected = sum(1 for event in events if event["by_fps"][key]["detected"])
        by_fps[key] = {
            "events_detected": detected,
            "detection_rate": round(detected / len(events), 3) if events else 0.0,
        }
    return {
        "fps_targets": list(TARGETS),
        "events_total": len(events),
        "events": events,
        "by_fps": by_fps,
    }


__all__ = ["FpsSweep", "TARGETS"]
