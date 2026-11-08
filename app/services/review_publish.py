"""Explicit, single-attempt YouTube publishing for reviewed local task outputs.

A durable marker is written before sending anything. An interrupted or uncertain
attempt is never retried automatically: the user must check Upload-Post/YouTube.
"""
import json
import os
import re
import tempfile
from pathlib import Path
from uuid import UUID

from filelock import FileLock

from app.services import upload_post, video_quality
from app.utils import file_security, utils


class ReviewPublishError(ValueError):
    pass


def _paths(task_id, video_path):
    try:
        task_id = str(UUID(str(task_id)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ReviewPublishError("Invalid task ID.") from exc
    root = Path(utils.task_dir()).resolve()
    task_dir = (root / task_id).resolve()
    if task_dir.parent != root or not task_dir.is_dir():
        raise ReviewPublishError("Task directory not found.")
    path = Path(file_security.resolve_path_within_directory(str(task_dir), str(video_path)))
    if path.parent != task_dir or not re.fullmatch(r"final-\d+\.mp4", path.name, re.IGNORECASE):
        raise ReviewPublishError("Only final task videos can be published.")
    return task_dir, path


def _write_record(path, record):
    fd, name = tempfile.mkstemp(prefix=".review-publish-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def publish_reviewed(task_id, video_path, *, title, description, privacy, made_for_kids):
    """Publish one approved output once. Never retries even on an uncertain failure."""
    title = str(title).strip()
    description = str(description).strip()
    if not title or len(title) > 100:
        raise ReviewPublishError("YouTube title must be between 1 and 100 characters.")
    if len(description) > 5000:
        raise ReviewPublishError("YouTube description must be at most 5000 characters.")
    if privacy not in {"private", "unlisted", "public"} or not isinstance(made_for_kids, bool):
        raise ReviewPublishError("Invalid privacy or audience setting.")
    # Do not create an attempt marker or contact a third-party publisher while
    # the mandatory official OAuth, QC, and approval workflow is unavailable.
    from app.services import youtube_safety
    if not youtube_safety.official_youtube_publishing_available():
        raise ReviewPublishError(youtube_safety.BLOCK_REASON)
    service = upload_post.upload_post_service
    if not service.is_configured():
        raise ReviewPublishError("Configure and enable Upload-Post first.")
    if service.auto_upload:
        raise ReviewPublishError("Disable auto-publishing before using reviewed publishing.")
    task_dir, path = _paths(task_id, video_path)
    record_path = task_dir / f".review-publish-{path.stem}.json"
    with FileLock(str(task_dir / f".review-publish-{path.stem}.lock"), timeout=1):
        if record_path.exists():
            raise ReviewPublishError("An upload was already attempted for this video. Check Upload-Post and YouTube before taking further action.")
        quality = video_quality.inspect_video(str(path))
        if not quality["ok"]:
            raise ReviewPublishError("Video inspection failed; no upload was started.")
        # Snapshot credentials before the call. Do not persist them in the record.
        account = service.snapshot_account()
        record = {"status": "sending", "video": path.name, "title": title,
                  "privacy": privacy, "made_for_kids": made_for_kids}
        _write_record(record_path, record)

        def background_started(request_id):
            record.update(status="processing", request_id=request_id)
            _write_record(record_path, record)

        try:
            result = upload_post.cross_post_video(
                video_path=str(path), title=title, platforms=["youtube"],
                youtube_extra={"youtube_title": title, "youtube_description": description,
                               "privacyStatus": privacy, "selfDeclaredMadeForKids": made_for_kids},
                on_background_start=background_started, account=account,
            )
            record["status"] = "complete" if isinstance(result, dict) and result.get("success") is True else "unconfirmed"
            if isinstance(result, dict) and isinstance(result.get("request_id"), str):
                record["request_id"] = result["request_id"]
            _write_record(record_path, record)
        except Exception:
            # The request may already have reached the provider. The existing
            # marker intentionally remains and prevents an automatic retry.
            raise ReviewPublishError("Upload outcome is unknown. Check Upload-Post and YouTube before trying again.") from None
        return record


def get_review_status(task_id, video_path):
    task_dir, path = _paths(task_id, video_path)
    marker = task_dir / f".review-publish-{path.stem}.json"
    if not marker.exists():
        return None
    try:
        with marker.open(encoding="utf-8") as handle:
            data = json.load(handle)
        return {key: data.get(key) for key in ("status", "request_id", "title", "privacy")}
    except (OSError, ValueError, AttributeError):
        return {"status": "unconfirmed"}
