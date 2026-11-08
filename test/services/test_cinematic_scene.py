from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.schema import VideoAspect
from app.services import cinematic_scene, jalapeno_video
from app.services.volcengine_seedance import SeedanceEndpoint, VolcEngineSeedanceUnconfirmedTaskError


@pytest.fixture
def scene(tmp_path, monkeypatch):
    monkeypatch.setattr(cinematic_scene.utils, "task_dir", lambda task_id: str(tmp_path / task_id))
    def directory(task_id):
        path = tmp_path / task_id
        path.mkdir(exist_ok=True)
        return str(path)
    monkeypatch.setattr(cinematic_scene.utils, "task_dir", directory)
    monkeypatch.setattr(cinematic_scene.task_artifacts, "write_script_data", lambda *args: None)
    monkeypatch.setattr(cinematic_scene.task_artifacts, "patch_script_data", lambda *args, **kwargs: True)
    endpoint = SeedanceEndpoint(provider_id="jalapeno", display_name="Test", api_key="secret",
        tasks_url="https://example.com/tasks", model="native-audio-model", resolution="1080p",
        min_duration=2, max_duration=12, generate_audio=True)
    return tmp_path, endpoint


def test_scene_preserves_video_and_audio_bytes_and_full_prompt(scene, monkeypatch):
    root, endpoint = scene
    calls = []
    prompt = 'A kitchen. Arun says: "Paati, look!" Meena replies: "Beautiful."'
    def generate(text, duration, aspect, endpoint_snapshot):
        calls.append((text, duration, aspect, endpoint_snapshot))
        return [SimpleNamespace(url="https://example.com/signed", source_info={"asset_id": "remote-1"})]
    def download(url, save_dir):
        path = root / "provider.mp4"
        path.write_bytes(b"original native audio and video")
        return str(path)
    monkeypatch.setattr(jalapeno_video, "generate_videos", generate)
    monkeypatch.setattr(cinematic_scene.material, "save_video", download)
    monkeypatch.setattr(cinematic_scene.video_quality, "inspect_video", lambda _: {
        "ok": True, "warnings": [], "details": {"has_audio": True}})
    result = cinematic_scene.generate_scene(str(uuid4()), prompt, 10, VideoAspect.landscape, endpoint)
    assert calls[0][0] == prompt and calls[0][1] == 10
    assert calls[0][3].generate_audio is True
    assert (root / result["videos"][0]).read_bytes() == b"original native audio and video"
    assert result["scene_remote_task_id"] == "remote-1"


def test_silent_output_is_retained_with_warning(scene, monkeypatch):
    root, endpoint = scene
    monkeypatch.setattr(jalapeno_video, "generate_videos", lambda *a, **k: [SimpleNamespace(url="x", source_info={})])
    source = root / "scene.mp4"
    source.write_bytes(b"silent")
    monkeypatch.setattr(cinematic_scene.material, "save_video", lambda *a, **k: str(source))
    monkeypatch.setattr(cinematic_scene.video_quality, "inspect_video", lambda _: {"ok": True, "warnings": ["No audio track detected"], "details": {"has_audio": False}})
    result = cinematic_scene.generate_scene(str(uuid4()), "scene", 10, VideoAspect.landscape, endpoint)
    assert "No audio track detected" in result["warnings"]
    assert result["videos"]


def test_uncertain_paid_submission_is_not_retried(scene, monkeypatch):
    _, endpoint = scene
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise VolcEngineSeedanceUnconfirmedTaskError("uncertain", task_id="remote")
    monkeypatch.setattr(jalapeno_video, "generate_videos", fail)
    with pytest.raises(VolcEngineSeedanceUnconfirmedTaskError) as error:
        cinematic_scene.generate_scene(str(uuid4()), "scene", 10, VideoAspect.landscape, endpoint)
    assert error.value.task_id == "remote"
    assert len(calls) == 1


def test_invalid_duration_fails_before_provider_call(scene, monkeypatch):
    _, endpoint = scene
    calls = []
    monkeypatch.setattr(jalapeno_video, "generate_videos", lambda *a, **k: calls.append(1))
    with pytest.raises(cinematic_scene.CinematicSceneError):
        cinematic_scene.generate_scene(str(uuid4()), "scene", 13, VideoAspect.landscape, endpoint)
    assert not calls


def test_prepare_endpoint_enables_native_audio_without_mutation(scene, monkeypatch):
    _, endpoint = scene
    from dataclasses import replace
    original = replace(endpoint, generate_audio=False)
    monkeypatch.setattr(jalapeno_video, "endpoint", lambda: original)
    snapshot = cinematic_scene.prepare_endpoint()
    assert snapshot.generate_audio is True
    assert original.generate_audio is False


def test_provider_payload_requests_native_audio(scene, monkeypatch):
    from app.config import config
    from app.services import volcengine_seedance
    _, endpoint = scene
    class Response:
        status_code = 200
        def __init__(self, body): self.body = body
        def json(self): return self.body
    captured = []
    monkeypatch.setattr(config, "proxy", {})
    monkeypatch.setattr(volcengine_seedance.requests, "post", lambda url, **kwargs: captured.append(kwargs["json"]) or Response({"id": "remote"}))
    monkeypatch.setattr(volcengine_seedance.requests, "get", lambda url, **kwargs: Response({"status": "succeeded", "content": {"video_url": "https://example.com/video.mp4"}}))
    prompt = 'Meena says: "Hello"; Arun replies: "Hi".'
    jalapeno_video.generate_videos(prompt, 10, VideoAspect.landscape, endpoint_snapshot=endpoint)
    assert captured[0]["generate_audio"] is True
    assert captured[0]["content"][0]["text"] == prompt
    assert captured[0]["duration"] == 10


def test_duplicate_scene_requests_share_active_job(scene, monkeypatch):
    from app.services import state, webui_task
    _, endpoint = scene
    memory = state.MemoryState()
    monkeypatch.setattr(state, "state", memory)
    jobs = []
    monkeypatch.setattr(webui_task._task_manager, "add_task", lambda *args, **kwargs: jobs.append((args, kwargs)))
    first, second = str(uuid4()), str(uuid4())
    try:
        assert webui_task.submit_cinematic_scene(first, "scene", 10, VideoAspect.landscape, endpoint) == first
        assert webui_task.submit_cinematic_scene(second, "scene", 10, VideoAspect.landscape, endpoint) == first
        assert len(jobs) == 1
    finally:
        webui_task._scene_submissions.clear()
