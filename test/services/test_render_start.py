from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts import render_start


def test_refuses_missing_password_before_start(monkeypatch):
    monkeypatch.delenv("WEBUI_PASSWORD", raising=False)
    process = MagicMock()
    monkeypatch.setattr(render_start.subprocess, "Popen", process)
    with pytest.raises(SystemExit, match="WEBUI_PASSWORD"):
        render_start.main()
    process.assert_not_called()


def test_rejects_invalid_port_before_start(monkeypatch):
    monkeypatch.setenv("WEBUI_PASSWORD", "a" * 30)
    monkeypatch.setenv("PORT", "8501")
    process = MagicMock()
    monkeypatch.setattr(render_start.subprocess, "Popen", process)
    with pytest.raises(SystemExit, match="PORT"):
        render_start.main()
    process.assert_not_called()


def test_proxy_protects_app_and_does_not_forward_password(tmp_path, monkeypatch):
    secret = "a-strong-unique-password-value"
    monkeypatch.setenv("WEBUI_PASSWORD", secret)
    monkeypatch.setenv("PORT", "10000")
    monkeypatch.setattr(render_start.tempfile, "mkdtemp", lambda **_: str(tmp_path))
    seen = {}
    def htpasswd(args, **kwargs):
        seen["args"] = args
        seen["stdin"] = kwargs["input"]
        Path(args[-2]).write_text("operator:hash\n")
    monkeypatch.setattr(render_start.subprocess, "run", htpasswd)
    def spawn(args):
        assert "WEBUI_PASSWORD" not in render_start.os.environ
        assert secret not in " ".join(args)
        child = MagicMock()
        child.poll.return_value = 1
        return child
    monkeypatch.setattr(render_start.subprocess, "Popen", spawn)
    with pytest.raises(SystemExit, match="web process exited"):
        render_start.main()
    assert secret not in " ".join(seen["args"])
    assert seen["stdin"] == secret + "\n"
    config = (tmp_path / "nginx.conf").read_text()
    assert "auth_basic_user_file" in config
    assert "proxy_pass http://127.0.0.1:8501" in config
    assert "location = /_render_health" in config
    assert secret not in config
