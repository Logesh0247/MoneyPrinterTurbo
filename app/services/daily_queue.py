"""Persistent, opt-in daily YouTube script queue.

Run a single scheduler separately with ``python -m app.services.daily_queue``.
Generation is prepared ahead of the slot. Interrupted/uncertain jobs stop the
queue rather than being retried, to avoid duplicate paid work or uploads.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, time as clock_time
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from filelock import FileLock, Timeout

from app.utils import utils

ZONE = ZoneInfo("Asia/Kolkata")
SLOT = clock_time(18, 0)
GRACE_SECONDS = 900


def _root():
    path = Path(utils.storage_dir()) / "daily_queue"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load():
    path = _root() / "queue.json"
    if not path.exists():
        return {"enabled": False, "items": [], "last_slot": None}
    with path.open(encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("Invalid daily queue file; restore it from backup.")
    return data


def _save(data):
    root = _root()
    fd, name = tempfile.mkstemp(prefix=".queue-", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, root / "queue.json")
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_queue():
    with FileLock(str(_root() / "queue.lock"), timeout=10):
        return _load()


def set_enabled(enabled):
    if enabled:
        from app.services import youtube_safety
        if not youtube_safety.official_youtube_publishing_available():
            raise ValueError(youtube_safety.BLOCK_REASON)
    with FileLock(str(_root() / "queue.lock"), timeout=10):
        data = _load()
        data["enabled"] = bool(enabled)
        _save(data)


def skip_item(item_id):
    """Skip a stopped item only while paused; never cancel an active side effect."""
    with FileLock(str(_root() / "queue.lock"), timeout=10):
        data = _load()
        if data.get("enabled"):
            raise ValueError("Pause the queue before skipping an item.")
        item = next((item for item in data["items"] if item["id"] == item_id), None)
        if not item or item.get("status") not in {"queued", "ready", "failed", "interrupted", "unconfirmed"}:
            raise ValueError("This item cannot be skipped while it is active.")
        item["status"] = "skipped"
        _save(data)


def enqueue(scripts, params, privacy, made_for_kids):
    """Add (title, script) pairs in order. Credentials are never stored here."""
    from app.models.schema import VideoParams

    validated = VideoParams.model_validate(params)
    if validated.video_source not in {"pexels", "pixabay", "coverr"}:
        raise ValueError("Daily queue currently supports Pexels, Pixabay or Coverr footage only.")
    if validated.custom_audio_file or validated.video_materials or validated.video_count != 1:
        raise ValueError("Use one video per script, without uploaded audio or local footage.")
    if not validated.voice_name:
        raise ValueError("Select a voice before adding scripts.")
    if privacy not in {"public", "private", "unlisted"} or not isinstance(made_for_kids, bool):
        raise ValueError("Select valid YouTube visibility and audience settings.")
    if not scripts or len(scripts) > 100:
        raise ValueError("Add between 1 and 100 scripts at a time.")
    items = []
    for title, script in scripts:
        title, script = title.strip(), script.strip()
        if not title or len(title) > 100 or not script or len(script) > 8000:
            raise ValueError("Each script needs a title (up to 100 characters) and text (up to 8000 characters).")
        settings = validated.model_dump(mode="json")
        settings.update(video_subject=title, video_script=script, video_count=1)
        items.append({"id": str(uuid4()), "title": title, "params": settings,
                      "privacy": privacy, "made_for_kids": made_for_kids, "status": "queued"})
    with FileLock(str(_root() / "queue.lock"), timeout=10):
        data = _load()
        data["items"].extend(items)
        _save(data)


def _update(item_id, **changes):
    with FileLock(str(_root() / "queue.lock"), timeout=10):
        data = _load()
        item = next(item for item in data["items"] if item["id"] == item_id)
        item.update(changes)
        _save(data)


def _slot(now, last_slot):
    now = now.astimezone(ZONE)
    candidate = datetime.combine(now.date(), SLOT, ZONE)
    if now > candidate + timedelta(seconds=GRACE_SECONDS) or (last_slot and candidate.date().isoformat() <= last_slot):
        candidate += timedelta(days=1)
    return candidate


def run_once(now=None):
    """Execute one safe transition; callers must hold worker.lock."""
    from app.config import config
    from app.models.schema import VideoParams
    from app.services import task, review_publish, upload_post

    now = (now or datetime.now(ZONE)).astimezone(ZONE)
    data = read_queue()
    if not data.get("enabled"):
        return
    # Existing on-disk queues may have been enabled before Safe Mode was added.
    # Do not generate paid work or publish while required safeguards are absent.
    from app.services import youtube_safety
    if not youtube_safety.official_youtube_publishing_available():
        return
    # Never run a second, legacy auto-publisher alongside the queue.
    service = upload_post.upload_post_service
    if service.auto_upload or not service.is_configured():
        return
    items = data["items"]
    item = next((i for i in items if i.get("status") not in {"published", "skipped"}), None)
    if item is None:
        return
    status = item.get("status")
    if status in {"preparing", "sending"}:
        _update(item["id"], status="interrupted", error="Interrupted operation; inspect task and provider before further action.")
        return
    if status in {"failed", "interrupted", "unconfirmed"}:
        return
    if status == "queued":
        task_id = str(uuid4())
        _update(item["id"], status="preparing", task_id=task_id)
        try:
            params = VideoParams.model_validate(item["params"])
            # Worker has its own process and a configuration snapshot. Refuse
            # legacy auto-upload above, so preparation never publishes early.
            with config.runtime_config_lock():
                result = task.start(task_id=task_id, params=params)
            paths = result.get("videos") if isinstance(result, dict) else None
            if not paths or len(paths) != 1:
                raise ValueError("Video generation failed; inspect the task logs.")
            _update(item["id"], status="ready", video_path=paths[0])
        except Exception as exc:
            _update(item["id"], status="failed", error=f"Generation failed ({type(exc).__name__}); inspect server logs.")
        return
    if status != "ready":
        return
    slot = _slot(now, data.get("last_slot"))
    if now < slot:
        return
    # Persist the slot and sending state before the external side effect.
    # Pausing in the UI wins if it happens before this atomic transition.
    with FileLock(str(_root() / "queue.lock"), timeout=10):
        current = _load()
        match = next(i for i in current["items"] if i["id"] == item["id"])
        if not current.get("enabled") or match.get("status") != "ready":
            return
        match["status"] = "sending"
        current["last_slot"] = slot.date().isoformat()
        _save(current)
    try:
        result = review_publish.publish_reviewed(
            item["task_id"], item["video_path"], title=item["title"], description="",
            privacy=item["privacy"], made_for_kids=item["made_for_kids"],
        )
        _update(item["id"], status="published" if result.get("status") == "complete" else "unconfirmed",
                request_id=result.get("request_id"))
    except Exception as exc:
        _update(item["id"], status="unconfirmed", error=f"Publishing not confirmed ({type(exc).__name__}); check Upload-Post and YouTube.")


def main():
    if "--once" in sys.argv:
        try:
            with FileLock(str(_root() / "worker.lock"), timeout=0):
                run_once()
        except Timeout:
            pass
        return
    # Each invocation reloads current settings and credentials from config.toml.
    # The child holds worker.lock through generation/upload, so two daemons
    # cannot process the same queue concurrently.
    while True:
        try:
            subprocess.run([sys.executable, "-m", "app.services.daily_queue", "--once"], check=False)
        except OSError as exc:
            print(f"Daily queue worker could not start: {type(exc).__name__}", flush=True)
        time.sleep(30)


if __name__ == "__main__":
    main()
