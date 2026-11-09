from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.services import daily_queue, review_publish, youtube_safety
from app.services.upload_post import UploadPostService


def test_youtube_upload_is_blocked_before_any_network_call(tmp_path, monkeypatch):
    from app.services import upload_post
    monkeypatch.setattr(upload_post.config, "app", {"upload_post_enabled": True, "upload_post_api_key": "key", "upload_post_username": "user"})
    post = MagicMock()
    monkeypatch.setattr(upload_post.requests, "post", post)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"test")
    result = UploadPostService().upload_video(str(video), "title", ["YouTube_Shorts"])
    assert result["success"] is False
    assert "blocked" in result["error"]
    post.assert_not_called()


@pytest.mark.parametrize("platforms", [[], [""], ["unknown"], ["youtube_music"]])
def test_ambiguous_upload_targets_never_reach_network(tmp_path, monkeypatch, platforms):
    from app.services import upload_post
    monkeypatch.setattr(upload_post.config, "app", {
        "upload_post_enabled": True, "upload_post_api_key": "test-key",
        "upload_post_username": "test-user",
    })
    post = MagicMock()
    monkeypatch.setattr(upload_post.requests, "post", post)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"test")
    assert not UploadPostService().upload_video(str(video), "title", platforms)["success"]
    post.assert_not_called()


def test_unknown_targets_fail_closed():
    assert youtube_safety.is_youtube_target(None)
    assert youtube_safety.is_youtube_target([None])
    assert youtube_safety.is_youtube_target([])
    assert youtube_safety.is_youtube_target([""])
    assert youtube_safety.is_youtube_target(["unknown"])
    assert youtube_safety.is_youtube_target([" YouTube "])
    assert youtube_safety.is_youtube_target(["youtube", "tiktok"])
    assert not youtube_safety.is_youtube_target(["tiktok", "instagram"])
    assert not youtube_safety.is_youtube_target(["facebook", "linkedin"])


def test_reviewed_publish_stops_before_creating_a_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(review_publish.utils, "task_dir", lambda: str(tmp_path))
    task_id = str(uuid4())
    task_dir = tmp_path / task_id
    task_dir.mkdir()
    path = task_dir / "final-1.mp4"
    path.write_bytes(b"video")
    network = MagicMock()
    monkeypatch.setattr(review_publish.upload_post, "cross_post_video", network)
    with pytest.raises(review_publish.ReviewPublishError, match="blocked"):
        review_publish.publish_reviewed(task_id, str(path), title="title", description="", privacy="private", made_for_kids=False)
    assert not list(task_dir.glob(".review-publish-*.json"))
    network.assert_not_called()


def test_queue_cannot_start_or_resume_previously_enabled_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(daily_queue.utils, "storage_dir", lambda: str(tmp_path))
    with pytest.raises(ValueError, match="blocked"):
        daily_queue.set_enabled(True)
    # Simulate a queue enabled in an older version.
    daily_queue._save({"enabled": True, "items": [{"id": "id", "status": "queued"}]})
    from app.services import task
    generate = MagicMock()
    monkeypatch.setattr(task, "start", generate)
    daily_queue.run_once()
    assert daily_queue.read_queue()["items"][0]["status"] == "queued"
    generate.assert_not_called()
    daily_queue.set_enabled(False)
    assert daily_queue.read_queue()["enabled"] is False


def test_quality_report_never_claims_unverified_checks_passed():
    report = youtube_safety.safety_report()
    assert report["mode"] == "SAFE"
    assert report["status"] == "BLOCKED"
    assert report["quality_score"] is None
    assert set(report["checks"].values()) == {"NOT VERIFIED"}


def test_auto_publish_worker_blocks_before_metadata_or_upload(monkeypatch):
    from app.models import const
    from app.services import state, task
    backend = state.MemoryState()
    backend.update_task("task", state=const.TASK_STATE_COMPLETE, progress=100,
                        videos=["final.mp4"], cross_post_state=const.CROSS_POST_STATE_PENDING)
    monkeypatch.setattr(task.sm, "state", backend)
    metadata, upload = MagicMock(), MagicMock()
    monkeypatch.setattr(task.llm, "generate_social_metadata", metadata)
    monkeypatch.setattr(task.upload_post, "cross_post_video", upload)
    task._run_cross_post("task", ("final.mp4",), "subject", "script", "en", ("youtube",), "private")
    metadata.assert_not_called()
    upload.assert_not_called()
    assert backend.get_task("task")["cross_post_state"] == const.CROSS_POST_STATE_FAILED
    assert "blocked" in backend.get_task("task")["cross_post_error"]
