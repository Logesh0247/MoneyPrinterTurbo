import subprocess
from pathlib import Path
from types import SimpleNamespace

import imageio_ffmpeg
import pytest

from app.models.schema import TaskVideoRequest
from app.services import video


def test_high_quality_concat_uses_h264_and_crf_18(tmp_path, monkeypatch):
    commands = []
    def run(command, output):
        commands.append(command)
        Path(output).write_bytes(b"encoded")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(video, "_run_concat_with_heartbeat", run)
    video.concat_video_clips_with_ffmpeg(["clip.mp4"], str(tmp_path / "out.mp4"), 1, str(tmp_path), high_quality=True)
    command = commands[0]
    assert command[command.index("-c:v") + 1] == "libx264"
    assert command[command.index("-crf") + 1] == "18"


def test_profile_rejects_invalid_values():
    with pytest.raises(ValueError):
        TaskVideoRequest(video_subject="x", video_quality="ultra")
    assert TaskVideoRequest(video_subject="x").video_quality == "standard"


def test_high_quality_final_encode_is_playable_with_audio(tmp_path, monkeypatch):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    footage, speech, output = (tmp_path / name for name in ("input.mp4", "speech.wav", "final.mp4"))
    base = [ffmpeg, "-nostdin", "-v", "error", "-y"]
    subprocess.run([*base, "-f", "lavfi", "-i", "color=c=red:s=64x64:r=30:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(footage)], check=True)
    subprocess.run([*base, "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1", str(speech)], check=True)
    request = TaskVideoRequest(video_subject="test", video_quality="high", subtitle_enabled=False, bgm_type="none", n_threads=1)
    assert video.generate_video(str(footage), str(speech), "", str(output), request)
    with video.VideoFileClip(str(output)) as clip:
        assert clip.fps == 30
        assert clip.audio is not None
        assert clip.duration >= 0.9
    data = output.read_bytes()
    assert data.index(b"moov") < data.index(b"mdat")
    probe = subprocess.run([ffmpeg, "-i", str(output)], capture_output=True, text=True).stderr
    assert "h264" in probe and "aac" in probe
    assert "48000 Hz" in probe


def test_high_quality_combination_uses_playable_constant_rate_output(tmp_path, monkeypatch):
    from app.models.schema import VideoAspect, VideoConcatMode

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    source, audio, combined = (tmp_path / name for name in ("source.mp4", "voice.wav", "combined.mp4"))
    base = [ffmpeg, "-nostdin", "-v", "error", "-y"]
    subprocess.run([*base, "-f", "lavfi", "-i", "color=blue:s=64x64:r=24:d=1.5", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)], check=True)
    subprocess.run([*base, "-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(audio)], check=True)
    monkeypatch.setattr(VideoAspect, "to_resolution", lambda self: (64, 64))
    video.combine_videos(str(combined), [str(source)], str(audio), video_concat_mode=VideoConcatMode.sequential, high_quality=True, threads=1)
    with video.VideoFileClip(str(combined)) as clip:
        assert clip.fps == 30
        assert clip.duration >= 0.9
        assert clip.get_frame(0).shape == (64, 64, 3)
