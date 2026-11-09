"""Exercise final publication with real local FFmpeg, not a paid provider."""
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import imageio_ffmpeg
import pytest

from app.models.schema import VideoAspect, VideoParams
from app.services import state, task, video


def make_video(path, audio):
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-f", "lavfi", "-i",
               "color=red:s=64x64:r=5:d=1"]
    if audio:
        command += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "aac"]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", "1", str(path)]
    subprocess.run(command, check=True, capture_output=True)


class CopyClip:
    duration = 1

    def __init__(self, source):
        self.source = source

    def write_videofile(self, output_file, **kwargs):
        shutil.copyfile(self.source, output_file)


def test_real_encoded_audio_and_video_are_decoded_before_atomic_publish(tmp_path):
    source = tmp_path / "source.mp4"
    final = tmp_path / "final.mp4"
    make_video(source, audio=True)
    final.write_bytes(b"previous output")
    video._write_videofile_with_codec_fallback(CopyClip(source), str(final), "libx264", atomic_output=True)
    assert final.read_bytes() == source.read_bytes()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["final.mp4", "source.mp4"]


@pytest.mark.parametrize("kind", ["invalid", "truncated", "missing_audio"])
def test_invalid_output_never_replaces_previous_file(tmp_path, kind):
    source = tmp_path / "source.mp4"
    final = tmp_path / "final.mp4"
    if kind == "invalid":
        source.write_bytes(b"not an mp4")
    else:
        make_video(source, audio=kind != "missing_audio")
        if kind == "truncated":
            source.write_bytes(source.read_bytes()[:100])
    final.write_bytes(b"previous output")
    with pytest.raises(video.FinalVideoValidationError):
        video._write_videofile_with_codec_fallback(CopyClip(source), str(final), "libx264", atomic_output=True)
    assert final.read_bytes() == b"previous output"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["final.mp4", "source.mp4"]


def test_local_combine_render_and_integrity_check_integration(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    speech = tmp_path / "speech.wav"
    output_dir = tmp_path / "task"
    output_dir.mkdir()
    make_video(source, audio=False)
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-v", "error",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(speech)],
                   check=True, capture_output=True)
    monkeypatch.setattr(VideoAspect, "to_resolution", lambda self: (64, 64))
    monkeypatch.setattr(task.utils, "task_dir", lambda *_: str(output_dir))
    backend = state.MemoryState()
    monkeypatch.setattr(state, "state", backend)
    params = VideoParams(video_subject="Local integration", video_source="local",
                         subtitle_enabled=False, bgm_type="none", n_threads=1)
    videos, combined, warnings = task.generate_final_videos(
        "job", params, [str(source)], str(speech), "", 1.0)
    assert len(videos) == len(combined) == 1
    assert not warnings
    assert backend.get_task("job")["videos"] == videos
    video._verify_final_render(videos[0], 1.0)


def test_pipeline_reports_integrity_failure_without_publishing(monkeypatch):
    backend = state.MemoryState()
    monkeypatch.setattr(state, "state", backend)
    monkeypatch.setattr(task.utils, "check_ffmpeg_ready", lambda: True)
    monkeypatch.setattr(task, "generate_script", lambda *_: "script")
    monkeypatch.setattr(task, "save_script_data", lambda *_: None)
    monkeypatch.setattr(task, "generate_audio", lambda *a, **kw: ("voice.wav", 1, None))
    monkeypatch.setattr(task, "generate_subtitle", lambda *_: "")
    monkeypatch.setattr(task, "get_video_materials", lambda *a, **kw: ["visual.mp4"])
    def fail(*args, **kwargs):
        kwargs["partial_results"].update(videos=["first.mp4"], combined_videos=["first-combined.mp4"])
        raise video.FinalVideoValidationError("untrusted detail")
    monkeypatch.setattr(task, "generate_final_videos", fail)
    with patch.object(task, "_schedule_cross_post") as publish:
        result = task.start("job", VideoParams(video_subject="test", video_source="local",
                                               subtitle_enabled=False))
    assert result["state"] == task.const.TASK_STATE_FAILED
    assert result["videos"] == ["first.mp4"]
    assert result["error"] == "Final video integrity check failed; the output could not be verified."
    publish.assert_not_called()


def test_verifier_timeout_is_failure_and_keeps_previous_file(tmp_path):
    source = tmp_path / "source.mp4"
    final = tmp_path / "final.mp4"
    source.write_bytes(b"unverified")
    final.write_bytes(b"previous output")
    with patch.object(video.subprocess, "run", side_effect=subprocess.TimeoutExpired("ffmpeg", 120)):
        with pytest.raises(video.FinalVideoValidationError, match="timed out"):
            video._write_videofile_with_codec_fallback(CopyClip(source), str(final), "libx264", atomic_output=True)
    assert final.read_bytes() == b"previous output"
    assert not list(Path(tmp_path).glob(".final.mp4.*"))
