import ast
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
WEBUI_MAIN = ROOT_DIR / "webui" / "Main.py"
WEBUI_STYLES = ROOT_DIR / "webui" / "styles.css"
DEVICE_STORE = ROOT_DIR / "webui" / "components" / "device_key_store" / "index.html"


def _attribute_name(node):
    names = []
    while isinstance(node, ast.Attribute):
        names.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        names.append(node.id)
    return ".".join(reversed(names))


def _functions_by_name(tree):
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }


def _decorator_run_every(function):
    if not function.decorator_list:
        return None
    decorator = function.decorator_list[0]
    if not isinstance(decorator, ast.Call):
        return None
    if _attribute_name(decorator.func) != "st.fragment":
        return None
    for keyword in decorator.keywords:
        if keyword.arg == "run_every":
            return ast.unparse(keyword.value)
    return None


def test_styles_disable_streamlit_stale_fade():
    css = WEBUI_STYLES.read_text(encoding="utf-8")
    assert '[data-stale="true"]' in css
    assert "opacity: 1 !important" in css
    assert "transition: none !important" in css


def test_device_key_store_does_not_repost_the_same_value():
    source = DEVICE_STORE.read_text(encoding="utf-8")
    assert "setComponentValueIfChanged" in source
    assert "lastSentValue" in source
    assert "setComponentValue({ ready: true, payload: readStore() });" not in source


def test_idle_top_bar_does_not_poll_every_second():
    tree = ast.parse(WEBUI_MAIN.read_text(encoding="utf-8"))
    functions = _functions_by_name(tree)

    assert "_render_pending_version_check" not in functions
    assert _decorator_run_every(functions["_render_task_manager_entry"]) is None
    assert _decorator_run_every(functions["_render_task_manager_entry_live"]) == "'2s'"

    dispatcher = functions["_render_task_manager_entry"]
    called = [
        node.func.id
        for node in ast.walk(dispatcher)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "_task_manager_needs_live_refresh" in called
    assert "_render_task_manager_entry_live" in called
    assert "_render_task_manager_body" in called
