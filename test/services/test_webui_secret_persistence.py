import ast
from pathlib import Path

from webui.device_key_store import is_blank_value, is_credential_key


ROOT_DIR = Path(__file__).parent.parent.parent
WEBUI_MAIN = ROOT_DIR / "webui" / "Main.py"
HELPERS = {
    "_is_credential_config_key",
    "_credential_widget_state_keys",
    "_normalize_backup_value",
    "_format_secret_widget_value",
    "_restore_secret_widgets_from_config",
    "_set_runtime_config",
}
CONSTANTS = {
    "CREDENTIAL_KEY_SUFFIXES",
    "CREDENTIAL_WIDGET_STATE_ALIASES",
    "IMAGE_MATERIAL_COMPANION_KEYS",
}


class _FakeStreamlit:
    def __init__(self):
        self.session_state = {}


class _FakeLogger:
    def __init__(self):
        self.debugs = []

    def debug(self, message):
        self.debugs.append(message)


class _FakeConfig:
    def __init__(self, sections):
        self.sections = sections
        self.updates = []

    def snapshot_config_with_pending(self, section):
        return dict(section)

    def update_config_nonblocking(self, section, key, value):
        self.updates.append((id(section), key, value))
        section[key] = value
        return True


def _load_helpers():
    tree = ast.parse(WEBUI_MAIN.read_text(encoding="utf-8"))
    selected_nodes = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in CONSTANTS
            for target in node.targets
        ):
            selected_nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in HELPERS:
            selected_nodes.append(node)

    app_section = {"openai_api_key": "saved-openai", "pexels_api_keys": ["pexels-1"]}
    siliconflow_section = {"api_key": "saved-siliconflow"}
    fake_st = _FakeStreamlit()
    fake_config = _FakeConfig(
        {"app": app_section, "siliconflow": siliconflow_section}
    )
    namespace = {
        "st": fake_st,
        "config": fake_config,
        "logger": _FakeLogger(),
        "_is_secret_config_key": is_credential_key,
        "_is_blank_secret_value": is_blank_value,
        "_RUNTIME_CONFIG_SECTIONS": {
            "app": app_section,
            "siliconflow": siliconflow_section,
        },
    }
    module = ast.fix_missing_locations(ast.Module(body=selected_nodes, type_ignores=[]))
    exec(compile(module, str(WEBUI_MAIN), "exec"), namespace)
    namespace["fake_st"] = fake_st
    namespace["fake_config"] = fake_config
    namespace["app_section"] = app_section
    namespace["siliconflow_section"] = siliconflow_section
    return namespace


NAMESPACE = _load_helpers()


def test_empty_password_replay_does_not_clear_saved_llm_key():
    set_runtime_config = NAMESPACE["_set_runtime_config"]
    NAMESPACE["app_section"]["openai_api_key"] = "saved-openai"
    NAMESPACE["fake_config"].updates.clear()

    updated = set_runtime_config("app", "openai_api_key", "")

    assert updated is False
    assert NAMESPACE["app_section"]["openai_api_key"] == "saved-openai"
    assert NAMESPACE["fake_config"].updates == []


def test_empty_password_replay_does_not_clear_saved_tts_key():
    set_runtime_config = NAMESPACE["_set_runtime_config"]
    NAMESPACE["siliconflow_section"]["api_key"] = "saved-siliconflow"
    NAMESPACE["fake_config"].updates.clear()

    updated = set_runtime_config("siliconflow", "api_key", "")

    assert updated is False
    assert NAMESPACE["siliconflow_section"]["api_key"] == "saved-siliconflow"


def test_non_empty_key_is_still_saved():
    set_runtime_config = NAMESPACE["_set_runtime_config"]
    NAMESPACE["app_section"]["openai_api_key"] = "saved-openai"
    NAMESPACE["fake_config"].updates.clear()

    updated = set_runtime_config("app", "openai_api_key", "sk-new")

    assert updated is True
    assert NAMESPACE["app_section"]["openai_api_key"] == "sk-new"


def test_restore_fills_empty_password_widgets_from_saved_config():
    restore = NAMESPACE["_restore_secret_widgets_from_config"]
    NAMESPACE["app_section"]["openai_api_key"] = "saved-openai"
    NAMESPACE["app_section"]["pexels_api_keys"] = ["pexels-1"]
    NAMESPACE["siliconflow_section"]["api_key"] = "saved-siliconflow"
    NAMESPACE["fake_st"].session_state.clear()
    NAMESPACE["fake_st"].session_state.update(
        {
            "openai_api_key_input": "",
            "pexels_api_keys_input": "",
            "siliconflow_api_key_input": "",
            "video_subject": "untouched",
        }
    )

    restore()

    assert NAMESPACE["fake_st"].session_state == {
        "openai_api_key_input": "saved-openai",
        "pexels_api_keys_input": "pexels-1",
        "siliconflow_api_key_input": "saved-siliconflow",
        "video_subject": "untouched",
    }


def test_restore_does_not_invent_widgets_that_were_never_shown():
    restore = NAMESPACE["_restore_secret_widgets_from_config"]
    NAMESPACE["fake_st"].session_state.clear()

    restore()

    assert NAMESPACE["fake_st"].session_state == {}


def test_empty_image_url_replay_does_not_clear_saved_endpoint():
    set_runtime_config = NAMESPACE["_set_runtime_config"]
    NAMESPACE["app_section"]["openai_image_base_url"] = "https://images.example/v1"
    NAMESPACE["app_section"]["openai_image_model"] = "local-image"
    NAMESPACE["fake_config"].updates.clear()

    assert set_runtime_config("app", "openai_image_base_url", "") is False
    assert set_runtime_config("app", "openai_image_model", "   ") is False
    assert (
        NAMESPACE["app_section"]["openai_image_base_url"] == "https://images.example/v1"
    )
    assert NAMESPACE["app_section"]["openai_image_model"] == "local-image"
    assert NAMESPACE["fake_config"].updates == []


def test_restore_fills_empty_image_endpoint_widgets_from_saved_config():
    restore = NAMESPACE["_restore_secret_widgets_from_config"]
    NAMESPACE["app_section"]["openai_image_base_url"] = "https://images.example/v1"
    NAMESPACE["app_section"]["openai_image_model"] = "local-image"
    NAMESPACE["fake_st"].session_state.clear()
    NAMESPACE["fake_st"].session_state.update(
        {
            "openai_image_base_url_input": "",
            "openai_image_model_input": "  ",
            "video_subject": "untouched",
        }
    )

    restore()

    assert NAMESPACE["fake_st"].session_state == {
        "openai_image_base_url_input": "https://images.example/v1",
        "openai_image_model_input": "local-image",
        "video_subject": "untouched",
    }
