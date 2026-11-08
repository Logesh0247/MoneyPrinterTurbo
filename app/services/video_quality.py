"""Advisory checks for a finished video; never publish or modify the file."""

import math
import os

import numpy as np
from moviepy import VideoFileClip


def inspect_video(path: str) -> dict:
    """Inspect metadata and sample a few frames. Warnings require human review.

    This is deliberately not a copyright, factual accuracy or full decode check.
    Missing audio can be intentional. Dark frames can be legitimate content.
    """
    result = {"ok": False, "errors": [], "warnings": [], "details": {}}
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        result["errors"].append("Video is missing or empty.")
        return result
    try:
        with VideoFileClip(path, audio=True) as clip:
            duration = float(clip.duration)
            width, height = clip.size
            if not math.isfinite(duration) or duration <= 0 or width <= 0 or height <= 0:
                raise ValueError("Invalid duration or dimensions")
            fps = float(clip.fps)
            if not math.isfinite(fps) or fps <= 0:
                raise ValueError("Invalid frame rate")
            result["details"] = {
                "duration_seconds": round(duration, 2),
                "width": width,
                "height": height,
                "fps": round(fps, 2),
                "has_audio": clip.audio is not None,
            }
            if clip.audio is None:
                result["warnings"].append("No audio track detected; check whether silence was intended.")
            else:
                # Small, bounded samples are an indication only; they cannot
                # establish loudness, true peak, sync, or full-file quality.
                try:
                    audio_duration = min(duration, float(clip.audio.duration))
                    if not math.isfinite(audio_duration) or audio_duration <= 0:
                        raise ValueError("Invalid audio duration")
                    peaks = []
                    for fraction in (0.2, 0.5, 0.8):
                        center = audio_duration * fraction
                        start = max(0.0, center - 0.05)
                        end = min(audio_duration, center + 0.05)
                        timestamps = np.linspace(start, end, 2205, endpoint=False)
                        samples = np.asarray(clip.audio.get_frame(timestamps))
                        if not np.isfinite(samples).all():
                            raise ValueError("Invalid audio samples")
                        peaks.append(float(np.max(np.abs(samples))))
                    peak = max(peaks)
                    result["details"]["sampled_audio_peak"] = round(peak, 4)
                    if peak >= 0.99:
                        result["warnings"].append("Sampled audio is near or above full scale; listen for clipping.")
                    elif peak < 0.001:
                        result["warnings"].append("Sampled audio is very quiet; check whether silence was intended.")
                except Exception:
                    result["warnings"].append("Audio samples could not be inspected; check the track manually.")
            if min(width, height) < 720:
                result["warnings"].append("Resolution is below 720 pixels on the shorter side.")
            dark_samples = 0
            for fraction in (0.2, 0.5, 0.8):
                frame = clip.get_frame(duration * fraction)
                if np.asarray(frame).mean() < 5:
                    dark_samples += 1
            if dark_samples:
                result["warnings"].append(
                    f"{dark_samples} of 3 sampled frames are very dark; review them manually."
                )
            result["ok"] = True
    except Exception as exc:
        result["errors"].append(f"Unable to inspect video ({type(exc).__name__}).")
    return result
