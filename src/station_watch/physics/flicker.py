"""``flicker``: lighting flicker vs camera exposure and frame rate (PLAN 3.4).

For clips tagged with a ``lamp`` type and a camera ``exposure_s``, this drives the clip
through the *real* HF1 ``Capture`` into a temporary ``Log`` and reads back two things:
the per-frame ``mean_luma`` series Capture already stamps on every ``FrameRecord``, and
whether Capture's dark monitor opened a ``dark`` ``BlindRecord`` during the clip. From
the luma series it reports the peak-to-peak swing and the dominant frequency the flicker
*aliases to* at the clip's frame rate -- a 120 Hz lamp sampled at 30 fps folds to 0 Hz
(a constant), so it is invisible as an oscillation and, crucially, must not trip the
darkness alarm. Flicker alone is not a blind condition; a ``dark`` record opening on a
flickering-but-lit clip would be a false alarm, so the measurement reports that it did
not. It does not reimplement the dark threshold -- it reads what the real Capture did.
"""

from __future__ import annotations

import numpy as np

from station_watch.records import BlindReason, BlindState

_RUN_ID = "physics-flicker"
_EPOCH = "1970-01-01T00:00:00.000000+00:00"
_SIGNIFICANT_AMP = 0.5  # luma; an aliased peak below this is noise, reported as no flicker


class Flicker:
    """Measure lighting flicker: luma swing, aliased frequency, and no false dark alarm."""

    name = "flicker"

    def selects(self, clip) -> bool:
        """A flicker input carries a lamp type and the camera exposure it was shot at."""
        return "lamp" in clip.tags and "exposure_s" in clip.tags

    def build_synthetic(self, work_dir):
        """The ``--synthetic`` proving input: a 120 Hz lamp sampled at 30 fps."""
        from station_watch.physics.synthetic_flicker import build_synthetic

        return build_synthetic(work_dir)

    def measure(self, clips, config, backend_factory) -> dict:
        """Measure every clip; return the metrics block."""
        results = [_clip_result(clip, config) for clip in clips]
        return _assemble(results)


def _fold(freq: float, fps: float) -> float:
    """Fold ``freq`` into ``[0, fps/2]`` -- the frequency it aliases to when sampled at ``fps``."""
    aliased = freq % fps
    return min(aliased, fps - aliased)


def _dominant_alias(luma: list[float], fps: float) -> tuple[float, float]:
    """The strongest oscillation in the luma series: ``(frequency_hz, amplitude_luma)``."""
    arr = np.asarray(luma, dtype=np.float64)
    n = len(arr)
    if n < 2:
        return 0.0, 0.0
    spectrum = np.abs(np.fft.rfft(arr - arr.mean()))
    freqs = np.fft.rfftfreq(n, d=1.0 / fps)
    peak = int(np.argmax(spectrum[1:])) + 1  # skip DC (removed by the mean subtraction)
    amplitude = 2.0 * spectrum[peak] / n
    return float(freqs[peak]), float(amplitude)


def _run_capture(clip_path, config) -> tuple[list[float], bool]:
    """Drive the clip through the real Capture; return its luma series and the dark flag."""
    import tempfile
    from pathlib import Path

    from station_watch.capture import BlindThresholds, Capture, FrameSource
    from station_watch.log import Log

    thresholds = BlindThresholds.from_station_config(config)
    with tempfile.TemporaryDirectory() as work:
        with Log(Path(work) / "flicker.db") as log:
            source = FrameSource(str(clip_path))
            capture = Capture(
                source,
                station_id=config.station_id,
                camera_id=config.camera_id,
                run_id=_RUN_ID,
                sleep=lambda _s: None,
            )
            capture.run(log, thresholds)
            frames = sorted(log.since(_EPOCH, ["frame"]), key=lambda record: record.frame_id)
            blinds = log.since(_EPOCH, ["blind"])
    luma = [record.mean_luma for record in frames]
    dark_opened = any(
        record.reason == BlindReason.DARK and record.state == BlindState.OPENED for record in blinds
    )
    return luma, dark_opened


def _clip_result(clip, config) -> dict:
    """One clip: luma swing, measured aliased frequency beside the expected fold, dark flag."""
    fps = float(clip.tags.get("fps", 30.0))
    luma, dark_opened = _run_capture(clip.clip_path, config)
    freq, amplitude = _dominant_alias(luma, fps)
    significant = amplitude >= _SIGNIFICANT_AMP
    flicker_hz = clip.tags.get("flicker_hz")
    expected = round(_fold(float(flicker_hz), fps), 3) if flicker_hz is not None else None
    peak_to_peak = round(max(luma) - min(luma), 3) if luma else 0.0
    return {
        "clip": clip.rel_path,
        "lamp": clip.tags["lamp"],
        "exposure_s": float(clip.tags["exposure_s"]),
        "fps": fps,
        "frames": len(luma),
        "peak_to_peak": peak_to_peak,
        "aliased_freq_hz": round(freq, 3) if significant else 0.0,
        "aliased_amplitude": round(amplitude, 3),
        "expected_alias_hz": expected,
        "dark_record_opened": dark_opened,
    }


def _assemble(results: list[dict]) -> dict:
    """Aggregate the per-clip flicker results."""
    return {
        "clips_total": len(results),
        "clips": results,
        "any_dark_record_opened": any(result["dark_record_opened"] for result in results),
    }


__all__ = ["Flicker"]
