"""One opt-in, native-audio cinematic scene; no narration replacement or publishing."""
from dataclasses import replace
import os
import shutil
import tempfile
from uuid import UUID

from app.models.schema import VideoAspect
from app.services import jalapeno_video, material, task_artifacts, video_quality
from app.utils import utils


class CinematicSceneError(ValueError):
    pass


def prepare_endpoint():
    """Snapshot the configured Seedance-compatible provider before queueing."""
    # The user must choose a model that supports native audio. The provider may
    # reject this flag or return silent video; no generic capability is assumed.
    return replace(jalapeno_video.endpoint(), generate_audio=True)


def generate_scene(task_id, prompt, duration, aspect, endpoint):
    """Generate one paid scene, preserve provider bytes/audio, and never retry POST."""
    try:
        task_id = str(UUID(str(task_id)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise CinematicSceneError("Invalid task ID") from exc
    prompt = str(prompt or "").strip()
    if not prompt or len(prompt) > 12000:
        raise CinematicSceneError("Scene prompt must contain 1–12000 characters")
    if isinstance(duration, bool) or not isinstance(duration, int) or not endpoint.min_duration <= duration <= endpoint.max_duration:
        raise CinematicSceneError("Duration is outside the configured model range")
    if not endpoint.generate_audio:
        raise CinematicSceneError("A native-audio endpoint is required")
    aspect = VideoAspect(aspect)
    task_dir = utils.task_dir(task_id)
    task_artifacts.write_script_data(task_id, {
        "script": prompt,
        "params": {"video_subject": "Cinematic scene", "video_script": prompt,
                   "video_aspect": aspect.value, "video_source": "jalapeno",
                   "scene_duration": duration, "scene_native_audio": True},
    })
    # Seedance's adapter does not automatically retry uncertain paid POSTs and
    # includes a remote task ID on known errors. Do not resubmit here.
    items = jalapeno_video.generate_videos(prompt, duration, aspect, endpoint_snapshot=endpoint)
    if len(items) != 1:
        raise CinematicSceneError("Provider returned no video; inspect its task status before retrying")
    source_id = (items[0].source_info or {}).get("asset_id", "")
    # Store only the remote ID, never the signed download URL or credentials.
    task_artifacts.patch_script_data(task_id, scene_remote_task_id=str(source_id))
    try:
        downloaded = material.save_video(items[0].url, save_dir=task_dir)
        if not downloaded:
            raise CinematicSceneError("Provider video download failed")
        inspection = video_quality.inspect_video(downloaded)
        if not inspection["ok"]:
            raise CinematicSceneError("Downloaded video could not be inspected")
        details = inspection["details"]
        warnings = list(inspection["warnings"])
        if abs(details.get("duration_seconds", duration) - duration) > 0.5:
            warnings.append("Returned duration differs from the requested duration; review the result.")
        short_side = min(details.get("width", 0), details.get("height", 0))
        expected_short = {"480p": 480, "720p": 720, "1080p": 1080}.get(endpoint.resolution)
        if expected_short and short_side and short_side < expected_short:
            warnings.append("Returned resolution is lower than requested; review the result.")
        final = os.path.join(task_dir, "final-1.mp4")
        descriptor, tmp = tempfile.mkstemp(prefix=".cinematic-", suffix=".mp4", dir=task_dir)
        os.close(descriptor)
        try:
            shutil.copyfile(downloaded, tmp)
            os.replace(tmp, final)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return {"videos": [final], "script": prompt, "scene_remote_task_id": str(source_id),
                "warnings": warnings, "details": details}
    except Exception as exc:
        raise CinematicSceneError(
            f"Scene task {source_id or 'unknown'} completed remotely but local download or inspection failed ({type(exc).__name__}). Check the provider before retrying."
        ) from exc
