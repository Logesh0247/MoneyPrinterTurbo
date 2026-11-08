from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from app.models.schema import VideoParams
from app.services import task, voice


def test_edge_error_hints_do_not_expose_provider_response():
    class Refused(Exception):
        status = 403

    hint = voice._edge_failure_hint(Refused("secret response and credential"))
    assert "403" in hint
    assert "secret" not in hint
    assert "timed out" in voice._edge_failure_hint(TimeoutError())
    assert "could not connect" in voice._edge_failure_hint(ConnectionError())


def test_gemini_error_hints_do_not_expose_provider_response():
    class QuotaExceeded(Exception):
        code = 429

    hint = voice._gemini_failure_hint(QuotaExceeded("secret response and credential"))
    assert "429" in hint
    assert "quota" in hint
    assert "secret" not in hint
    assert "timed out" in voice._gemini_failure_hint(TimeoutError())


def test_gemini_missing_key_has_actionable_failure(tmp_path):
    with patch.dict(voice.config.app, {"gemini_api_key": ""}):
        result = voice.tts("Hello", "gemini:Kore", 1.0, str(tmp_path / "a.mp3"))
    assert result is None
    assert "Gemini API key" in voice.get_tts_failure_hint()
    voice.clear_tts_failure_hint()


def test_failure_hints_are_isolated_between_threads():
    voice._tts_failure_hint.set("main thread")
    with ThreadPoolExecutor(max_workers=1) as executor:
        assert executor.submit(voice.get_tts_failure_hint).result() == ""
        executor.submit(voice._tts_failure_hint.set, "worker").result()
        assert executor.submit(voice.get_tts_failure_hint).result() == "worker"
    assert voice.get_tts_failure_hint() == "main thread"
    voice.clear_tts_failure_hint()


def test_audio_failure_propagates_safe_diagnostic(tmp_path):
    params = VideoParams(video_subject="test", voice_name="en-US-AriaNeural")

    def fail_tts(**kwargs):
        voice._tts_failure_hint.set("Edge TTS timed out; check network access to the speech service.")
        return None

    with (patch.object(task.utils, "task_dir", return_value=str(tmp_path)),
          patch.object(task.voice, "tts", side_effect=fail_tts),
          patch.object(task, "_mark_task_failed") as mark):
        assert task.generate_audio("task", params, "Hello") == (None, None, None)
    assert "timed out" in mark.call_args.args[2]


def test_edge_failure_records_safe_hint(tmp_path):
    class Refused(Exception):
        status = 403

    with patch.object(voice, "create_edge_tts_communicate", side_effect=Refused("sensitive")):
        result = voice.tts("Hello", "en-US-AriaNeural", 1.0, str(tmp_path / "a.mp3"))
    assert result is None
    assert "403" in voice.get_tts_failure_hint()
    assert "sensitive" not in voice.get_tts_failure_hint()
    voice.clear_tts_failure_hint()


def test_pause_silence_failure_is_not_silently_ignored(tmp_path):
    with patch.object(voice, "generate_silent_audio", return_value=False):
        result = voice.tts("[pause: 1s] Hello", "en-US-AriaNeural", 1.0, str(tmp_path / "a.mp3"))
    assert result is None
