import io
from unittest.mock import patch

from loguru import logger

from app.models.schema import VideoParams
from app.services import state, task


def test_unexpected_pipeline_exception_does_not_persist_secret():
    backend = state.MemoryState()
    secret = "sensitive-provider-key-and-url"
    captured = io.StringIO()
    sink = logger.add(captured, format="{message}")
    try:
        with (patch.object(task.sm, "state", backend),
              patch.object(task, "_run_pipeline", side_effect=RuntimeError(secret))):
            result = task.start("redaction-test", VideoParams(video_subject="test"))
    finally:
        logger.remove(sink)
    assert result["failed_stage"] == "pipeline"
    assert "RuntimeError" in result["error"]
    assert secret not in str(result)
    assert secret not in str(backend.get_task("redaction-test"))
    assert secret not in captured.getvalue()
