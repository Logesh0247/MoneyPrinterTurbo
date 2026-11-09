import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.models import const
from app.models.schema import VideoParams
from app.services import generation_progress, state, task


def test_stage_history_records_transitions_and_failure(monkeypatch):
    backend = state.MemoryState()
    monkeypatch.setattr(state, "state", backend)
    backend.update_task("job")
    with generation_progress.task_scope("job"):
        generation_progress.stage("script")
        generation_progress.stage("audio")
        generation_progress.submitting("provider")
        generation_progress.finish("failed")
    stages = backend.get_task("job")["generation_stages"]
    assert [(item["name"], item["status"]) for item in stages] == [
        ("script", "completed"), ("audio", "failed")
    ]
    assert all(item["ended_at"] >= item["started_at"] for item in stages)


def test_skipped_stage_is_not_marked_completed(monkeypatch):
    backend = state.MemoryState()
    monkeypatch.setattr(state, "state", backend)
    backend.update_task("job")
    with generation_progress.task_scope("job"):
        generation_progress.stage("subtitle")
        generation_progress.finish("skipped")
        generation_progress.stage("materials")
    assert backend.get_task("job")["generation_stages"][0]["status"] == "skipped"


def test_pipeline_terminal_state_closes_last_stage():
    backend = state.MemoryState()
    def run(task_id, *_args, **_kwargs):
        backend.update_task(task_id, state=const.TASK_STATE_PROCESSING)
        generation_progress.stage("audio")
        backend.update_task(task_id, state=const.TASK_STATE_COMPLETE, progress=100)
        return {"audio_file": "example.mp3"}
    with patch.object(state, "state", backend), patch.object(task, "_run_pipeline", run):
        task.start("job", VideoParams(video_subject="test"))
    stages = backend.get_task("job")["generation_stages"]
    assert stages[-1]["status"] == "completed"


def test_stage_view_renders_recorded_status_and_duration():
    tree = ast.parse(Path("webui/Main.py").read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                and n.name == "_render_generation_stages")
    view = SimpleNamespace(expander=MagicMock(), write=MagicMock())
    namespace = {"st": view, "time": SimpleNamespace(time=lambda: 15)}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                 "webui/Main.py", "exec"), namespace)
    namespace["_render_generation_stages"]({"generation_stages": [
        {"name": "audio", "status": "failed", "started_at": 10, "ended_at": 13}
    ]})
    view.write.assert_called_once_with("🔴 Narration · failed · 3s")


def test_reporting_failure_does_not_break_generation(monkeypatch):
    backend = state.MemoryState()
    monkeypatch.setattr(state, "state", backend)
    monkeypatch.setattr(backend, "patch_task", lambda *a, **kw: (_ for _ in ()).throw(OSError()))
    with generation_progress.task_scope("job"):
        generation_progress.stage("audio")
        generation_progress.finish("failed")
