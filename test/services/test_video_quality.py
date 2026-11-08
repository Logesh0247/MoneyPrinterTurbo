import subprocess

import imageio_ffmpeg

from app.services.video_quality import inspect_video


def make_video(path, color, audio=False):
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-f", "lavfi", "-i",
               f"color=c={color}:s=64x64:r=5:d=1"]
    if audio:
        command += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "aac"]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", "1", str(path)]
    subprocess.run(command, check=True, capture_output=True)


def test_detects_audio_and_dimensions(tmp_path):
    path = tmp_path / "video.mp4"
    make_video(path, "red", audio=True)
    result = inspect_video(str(path))
    assert result["ok"]
    assert result["details"]["has_audio"] is True
    assert (result["details"]["width"], result["details"]["height"]) == (64, 64)
    assert any("Resolution" in warning for warning in result["warnings"])
    assert not any("audio" in warning.lower() for warning in result["warnings"])


def test_warns_about_dark_frames_and_missing_audio(tmp_path):
    path = tmp_path / "video.mp4"
    make_video(path, "black")
    result = inspect_video(str(path))
    assert result["ok"]
    assert any("No audio" in warning for warning in result["warnings"])
    assert any("3 of 3" in warning for warning in result["warnings"])


def test_missing_empty_and_corrupt_files_are_errors(tmp_path):
    path = tmp_path / "video.mp4"
    assert not inspect_video(str(path))["ok"]
    path.touch()
    assert not inspect_video(str(path))["ok"]
    path.write_bytes(b"not a video")
    assert not inspect_video(str(path))["ok"]


def test_reports_sampled_silence_and_frame_rate(tmp_path):
    path = tmp_path / "silent.mp4"
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([ff, "-y", "-f", "lavfi", "-i", "color=red:s=64x64:r=24:d=1",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                    "-t", "1", "-c:v", "libx264", "-c:a", "aac", str(path)],
                   check=True, capture_output=True)
    result = inspect_video(str(path))
    assert result["ok"]
    assert result["details"]["fps"] == 24
    assert result["details"]["sampled_audio_peak"] < 0.001
    assert any("very quiet" in warning for warning in result["warnings"])
