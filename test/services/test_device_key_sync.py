"""Exercise the real WebUI sync function without launching a Streamlit server."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from webui.device_key_store import build_device_vault, keys_to_apply, unwrap_device_payload


class Rerun(Exception):
    pass


def make_sync(user, payload, state=None):
    source = Path("webui/Main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_sync_device_remembered_keys")
    state = {} if state is None else state
    backup = {"app": {"llm_provider": "openrouter"}}
    applied = []
    render = MagicMock(return_value={"ready": True, "payload": payload})
    namespace = {
        "json": json,
        "st": SimpleNamespace(session_state=state, rerun=MagicMock(side_effect=Rerun)),
        "webui_auth": SimpleNamespace(current_user=lambda: user),
        "config": SimpleNamespace(project_version="test"),
        "user_accounts": SimpleNamespace(update_user_keys=MagicMock()),
        "_RUNTIME_CONFIG_SECTIONS": {},
        "_collect_key_backup": lambda _: backup,
        "_count_backup_keys": lambda obj: sum(len(v) for v in obj.values()),
        "_build_key_backup_payload": lambda *_: {"keys": backup},
        "_parse_key_backup": lambda raw, _: json.loads(raw)["keys"],
        "_device_keys_to_apply": keys_to_apply,
        "_apply_key_backup": lambda keys: applied.append(keys) or 1,
        "_save_runtime_config": MagicMock(),
        "build_device_vault": build_device_vault,
        "unwrap_device_payload": unwrap_device_payload,
        "render_device_key_store": render,
        "logger": SimpleNamespace(warning=MagicMock(), info=MagicMock()),
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), "webui/Main.py", "exec"), namespace)
    return namespace["_sync_device_remembered_keys"], namespace, applied


def sample_vault():
    return build_device_vault(account={"id": "user-1"}, session_token="valid-token",
                              keys_backup={"keys": {"app": {"openai_api_key": "saved-key"}}})


def test_anonymous_startup_only_reads_and_does_not_send_or_apply_server_secrets():
    sync, ctx, applied = make_sync(None, sample_vault())
    result = sync()
    ctx["render_device_key_store"].assert_called_once_with()
    ctx["user_accounts"].update_user_keys.assert_not_called()
    assert result["vault"]["keys_backup"]["keys"]["app"]["openai_api_key"] == "saved-key"
    assert not applied


def test_first_authenticated_run_restores_before_writing():
    sync, ctx, applied = make_sync({"id": "user-1"}, sample_vault())
    with pytest.raises(Rerun):
        sync()
    ctx["render_device_key_store"].assert_called_once_with()
    ctx["user_accounts"].update_user_keys.assert_not_called()
    assert applied == [{"app": {"openai_api_key": "saved-key"}}]
    assert ctx["st"].session_state["device_key_values_user"] == "user-1"


def test_other_account_cannot_restore_old_device_keys():
    sync, _, applied = make_sync({"id": "different-user"}, sample_vault())
    with pytest.raises(Rerun):
        sync()
    assert not applied


def test_account_backup_is_not_overwritten_before_it_is_loaded():
    state = {"device_key_values_user": "user-1", "session_token": "valid-token"}
    sync, ctx, _ = make_sync({"id": "user-1"}, sample_vault(), state)
    sync()
    ctx["render_device_key_store"].assert_called_once_with()
    ctx["user_accounts"].update_user_keys.assert_not_called()


def test_later_signed_in_sync_does_not_write_before_widgets():
    state = {"device_key_values_user": "user-1", "user_keys_applied": True, "session_token": "valid-token",
             "device_account": {"id": "user-1", "password_hash": "private-hash"}}
    sync, ctx, _ = make_sync({"id": "user-1", "display_name": "User"}, sample_vault(), state)
    sync()
    ctx["render_device_key_store"].assert_called_once_with()
    ctx["user_accounts"].update_user_keys.assert_not_called()


def _load_persist():
    source = Path("webui/Main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "_persist_remembered_keys"
    )
    return node


def make_persist(user, state=None, backup=None, auth_enabled=True):
    node = _load_persist()
    state = {} if state is None else state
    backup = {"app": {"openai_api_key": "saved-key"}} if backup is None else backup
    render = MagicMock(return_value={"ready": True, "payload": None})
    namespace = {
        "st": SimpleNamespace(session_state=state),
        "webui_auth": SimpleNamespace(
            current_user=lambda: user,
            auth_gate_enabled=lambda: auth_enabled,
        ),
        "config": SimpleNamespace(project_version="test"),
        "user_accounts": SimpleNamespace(update_user_keys=MagicMock()),
        "_RUNTIME_CONFIG_SECTIONS": {},
        "_collect_key_backup": lambda _: backup,
        "_count_backup_keys": lambda obj: sum(len(v) for v in obj.values()),
        "_build_key_backup_payload": lambda *_: {"keys": backup},
        "build_device_vault": build_device_vault,
        "render_device_key_store": render,
    }
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
            "webui/Main.py",
            "exec",
        ),
        namespace,
    )
    return namespace["_persist_remembered_keys"], namespace


def test_persist_writes_keys_after_widgets_without_password_hash():
    persist, ctx = make_persist(
        {"id": "user-1", "display_name": "User"},
        state={"session_token": "valid-token"},
    )
    persist("mpt_device_key_store_settings")
    ctx["user_accounts"].update_user_keys.assert_called_once_with(
        "user-1", {"app": {"openai_api_key": "saved-key"}}
    )
    sent = ctx["render_device_key_store"].call_args
    assert sent.args[0]["account"] == {"id": "user-1", "display_name": "User"}
    assert sent.args[0]["keys_backup"]["keys"]["app"]["openai_api_key"] == "saved-key"
    assert sent.kwargs["session_token"] == "valid-token"
    assert sent.kwargs["component_key"] == "mpt_device_key_store_settings"
    assert "private-hash" not in json.dumps(sent.args[0])


def test_persist_does_not_wipe_stored_keys_when_config_is_still_empty():
    persist, ctx = make_persist(
        {"id": "user-1"},
        state={"session_token": "valid-token"},
        backup={},
    )
    persist("mpt_device_key_store_persist")
    ctx["user_accounts"].update_user_keys.assert_not_called()
    ctx["render_device_key_store"].assert_called_once_with(
        session_token="valid-token", component_key="mpt_device_key_store_persist"
    )


def test_persist_skips_server_secrets_before_sign_in():
    persist, ctx = make_persist(None, state={"session_token": "valid-token"})
    persist("mpt_device_key_store_persist")
    ctx["user_accounts"].update_user_keys.assert_not_called()
    ctx["render_device_key_store"].assert_not_called()


def test_settings_dialog_and_app_persist_keys_after_widgets():
    source = Path("webui/Main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    def calls_persist(name):
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
        return any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "_persist_remembered_keys"
            for child in ast.walk(node)
        )

    assert calls_persist("_render_settings_dialog")
    assert calls_persist("_render_application")
