import io
from contextlib import nullcontext
from unittest.mock import patch

import pytest
from loguru import logger

from app.controllers.manager.base_manager import TaskQueueFullError
from app.models import const
from app.models.schema import VideoParams
from app.services import state, webui_task


def test_worker_failure_preserves_progress_and_redacts_message():
    backend = state.MemoryState()
    backend.update_task("job", state=const.TASK_STATE_PROCESSING, progress=43)
    captured = io.StringIO()
    sink = logger.add(captured, format="{message}")
    try:
        with (patch.object(state, "state", backend),
              patch.object(webui_task.config, "runtime_config_lock", return_value=nullcontext()),
              patch.object(webui_task.tm, "start", side_effect=RuntimeError("synthetic-secret"))):
            result = webui_task._run_generation("job", VideoParams(video_subject="test"), False)
    finally:
        logger.remove(sink)
    assert result["progress"] == 43
    assert backend.get_task("job")["failed_stage"] == "webui_worker"
    assert "synthetic-secret" not in str(result)
    assert "synthetic-secret" not in str(backend.get_task("job"))
    assert "synthetic-secret" not in captured.getvalue()


def test_worker_does_not_recreate_deleted_task():
    backend = state.MemoryState()
    with (patch.object(state, "state", backend),
          patch.object(webui_task.config, "runtime_config_lock", return_value=nullcontext()),
          patch.object(webui_task.tm, "start", side_effect=RuntimeError("synthetic-secret"))):
        result = webui_task._run_generation("deleted", VideoParams(video_subject="test"), False)
    assert result["state"] == const.TASK_STATE_FAILED
    assert backend.get_task("deleted") is None


def test_queue_full_has_safe_actionable_error():
    backend = state.MemoryState()
    with (patch.object(state, "state", backend),
          patch.object(webui_task._task_manager, "add_task", side_effect=TaskQueueFullError("synthetic-secret"))):
        with pytest.raises(TaskQueueFullError, match="queue is full") as error:
            webui_task.submit_generation("queue-job", VideoParams(video_subject="test"), False)
    assert "synthetic-secret" not in str(error.value)
    assert backend.get_task("queue-job")["failed_stage"] == "scheduling"
    assert "synthetic-secret" not in str(backend.get_task("queue-job"))
