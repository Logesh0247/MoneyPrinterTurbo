import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from app.services import review_publish


@pytest.fixture
def environment(tmp_path, monkeypatch):
    # Simulate future gate availability to test legacy retry mechanics only.
    from app.services import youtube_safety
    monkeypatch.setattr(youtube_safety, "official_youtube_publishing_available", lambda: True)
    task_id = str(uuid4())
    task_dir = tmp_path / task_id
    task_dir.mkdir()
    video = task_dir / "final-1.mp4"
    video.write_bytes(b"video")
    monkeypatch.setattr(review_publish.utils, "task_dir", lambda: str(tmp_path))
    monkeypatch.setattr(review_publish.video_quality, "inspect_video", lambda _: {"ok": True})

    class Service:
        auto_upload = False
        def is_configured(self):
            return True
        def snapshot_account(self):
            return {"upload_post_api_key": "secret"}
    monkeypatch.setattr(review_publish.upload_post, "upload_post_service", Service())
    return task_id, video


def publish(env, **kwargs):
    task, video = env
    return review_publish.publish_reviewed(task, str(video), title="A title", description="Description",
                                           privacy="private", made_for_kids=False, **kwargs)


def test_reviewed_publish_passes_metadata_and_blocks_repetition(environment, monkeypatch):
    calls = []
    def upload(**kwargs):
        calls.append(kwargs)
        kwargs["on_background_start"]("request-1")
        return {"success": True, "request_id": "request-1"}
    monkeypatch.setattr(review_publish.upload_post, "cross_post_video", upload)
    result = publish(environment)
    assert result["status"] == "complete"
    assert calls[0]["platforms"] == ["youtube"]
    assert calls[0]["youtube_extra"]["privacyStatus"] == "private"
    assert calls[0]["youtube_extra"]["youtube_description"] == "Description"
    assert review_publish.get_review_status(environment[0], str(environment[1]))["request_id"] == "request-1"
    assert "secret" not in next(environment[1].parent.glob(".review-publish-*.json")).read_text()
    with pytest.raises(review_publish.ReviewPublishError, match="already attempted"):
        publish(environment)
    assert len(calls) == 1


def test_uncertain_failure_is_not_retried(environment, monkeypatch):
    def fail(**kwargs):
        kwargs["on_background_start"]("pending-id")
        raise OSError("connection lost")
    monkeypatch.setattr(review_publish.upload_post, "cross_post_video", fail)
    with pytest.raises(review_publish.ReviewPublishError, match="unknown"):
        publish(environment)
    status = review_publish.get_review_status(environment[0], str(environment[1]))
    assert status["status"] == "processing"
    assert status["request_id"] == "pending-id"
    with pytest.raises(review_publish.ReviewPublishError, match="already attempted"):
        publish(environment)


def test_quality_failure_and_auto_upload_do_not_start(environment, monkeypatch):
    monkeypatch.setattr(review_publish.video_quality, "inspect_video", lambda _: {"ok": False})
    with pytest.raises(review_publish.ReviewPublishError, match="inspection failed"):
        publish(environment)
    assert not list(environment[1].parent.glob(".review-publish-*.json"))
    review_publish.upload_post.upload_post_service.auto_upload = True
    with pytest.raises(review_publish.ReviewPublishError, match="Disable auto"):
        publish(environment)


def test_rejects_path_outside_task(environment, tmp_path):
    outside = tmp_path / "final-2.mp4"
    outside.write_bytes(b"video")
    with pytest.raises(ValueError):
        review_publish.publish_reviewed(environment[0], str(outside), title="x", description="", privacy="private", made_for_kids=False)


def test_concurrent_submissions_only_upload_once(environment, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    calls = []
    def upload(**kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return {"success": True}
    monkeypatch.setattr(review_publish.upload_post, "cross_post_video", upload)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(publish, environment)
        assert entered.wait(5)
        second = executor.submit(publish, environment)
        release.set()
        assert first.result()["status"] == "complete"
        with pytest.raises(review_publish.ReviewPublishError, match="already attempted"):
            second.result()
    assert len(calls) == 1
