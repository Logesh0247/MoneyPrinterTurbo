from datetime import datetime

import pytest

from app.models.schema import VideoParams
from app.services import daily_queue, review_publish, task, upload_post


@pytest.fixture
def env(tmp_path, monkeypatch):
    # Exercise queue mechanics with a simulated future compliant publisher.
    # Production remains blocked; test_youtube_safety covers that boundary.
    from app.services import youtube_safety
    monkeypatch.setattr(youtube_safety, "official_youtube_publishing_available", lambda: True)
    monkeypatch.setattr(daily_queue.utils, "storage_dir", lambda: str(tmp_path))
    service = upload_post.upload_post_service
    monkeypatch.setattr(type(service), "auto_upload", property(lambda self: False))
    monkeypatch.setattr(service, "is_configured", lambda: True)
    events = []
    monkeypatch.setattr(task, "start", lambda task_id, params: events.append(("generate", params.video_subject)) or {"videos": [f"/task/{task_id}/final-1.mp4"]})
    monkeypatch.setattr(review_publish, "publish_reviewed", lambda task_id, path, **kwargs: events.append(("publish", kwargs["title"])) or {"status": "complete", "request_id": "id"})
    params = VideoParams(video_subject="", video_source="pixabay", voice_name="en-US-AriaNeural-Female")
    daily_queue.enqueue([("First", "First script"), ("Second", "Second script")], params.model_dump(mode="json"), "private", False)
    return events


def at(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=daily_queue.ZONE)


def test_prepares_then_publishes_one_per_day_in_order(env):
    daily_queue.run_once(at(7, 10))
    assert env == []  # disabled by default
    daily_queue.set_enabled(True)
    daily_queue.run_once(at(7, 10))
    daily_queue.run_once(at(7, 17, 59))
    assert env == [("generate", "First")]
    daily_queue.run_once(at(7, 18))
    daily_queue.run_once(at(7, 18, 1))
    daily_queue.run_once(at(7, 18, 2))
    assert env == [("generate", "First"), ("publish", "First"), ("generate", "Second")]
    daily_queue.run_once(at(8, 18))
    assert env[-1] == ("publish", "Second")
    assert [i["status"] for i in daily_queue.read_queue()["items"]] == ["published", "published"]


def test_missed_slot_waits_for_next_day(env):
    daily_queue.set_enabled(True)
    daily_queue.run_once(at(7, 10))
    daily_queue.run_once(at(7, 18, 30))
    assert env == [("generate", "First")]
    daily_queue.run_once(at(8, 18))
    assert env[-1] == ("publish", "First")


def test_interrupted_work_stops_queue_without_retry(env):
    daily_queue.set_enabled(True)
    first = daily_queue.read_queue()["items"][0]
    daily_queue._update(first["id"], status="sending")
    daily_queue.run_once(at(7, 18))
    daily_queue.run_once(at(8, 18))
    assert daily_queue.read_queue()["items"][0]["status"] == "interrupted"
    assert not env
    daily_queue.set_enabled(False)
    daily_queue.skip_item(first["id"])
    daily_queue.set_enabled(True)
    daily_queue.run_once(at(9, 10))
    assert env == [("generate", "Second")]


def test_publish_failure_halts_next_item(env, monkeypatch):
    daily_queue.set_enabled(True)
    daily_queue.run_once(at(7, 10))
    def fail(*args, **kwargs):
        raise OSError("unknown upload result")
    monkeypatch.setattr(review_publish, "publish_reviewed", fail)
    daily_queue.run_once(at(7, 18))
    daily_queue.run_once(at(8, 18))
    assert daily_queue.read_queue()["items"][0]["status"] == "unconfirmed"
    assert env == [("generate", "First")]


def test_pause_and_legacy_auto_publish_prevent_work(env, monkeypatch):
    daily_queue.set_enabled(True)
    monkeypatch.setattr(type(upload_post.upload_post_service), "auto_upload", property(lambda self: True))
    daily_queue.run_once(at(7, 10))
    assert not env
    first = daily_queue.read_queue()["items"][0]
    with pytest.raises(ValueError, match="Pause"):
        daily_queue.skip_item(first["id"])


def test_rejects_invalid_scripts_without_partial_insert(env):
    before = daily_queue.read_queue()
    params = VideoParams(video_subject="", video_source="pixabay", voice_name="en-US-AriaNeural-Female")
    with pytest.raises(ValueError):
        daily_queue.enqueue([("Valid", "script"), ("Invalid", "")], params.model_dump(mode="json"), "private", False)
    assert daily_queue.read_queue() == before
