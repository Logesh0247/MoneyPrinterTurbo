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


def test_later_signed_in_sync_writes_public_account_not_password_hash():
    state = {"device_key_values_user": "user-1", "session_token": "valid-token",
             "device_account": {"id": "user-1", "password_hash": "private-hash"}}
    sync, ctx, _ = make_sync({"id": "user-1", "display_name": "User"}, sample_vault(), state)
    sync()
    sent = ctx["render_device_key_store"].call_args.args[0]
    assert sent["account"] == {"id": "user-1", "display_name": "User"}
    assert "private-hash" not in json.dumps(sent)
