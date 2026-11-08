import base64
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from openai import OpenAI

from app.config import config
from app.services import jalapeno_video, llm, material
from webui import settings_providers


def test_host_defaults_are_opt_in_and_never_copy_secrets():
    settings = {}
    config.apply_host_defaults(settings, {"OPENROUTER_API_KEY": "secret"})
    assert settings == {}
    config.apply_host_defaults(settings, {
        "MPT_DEFAULT_OPENROUTER": "1", "MPT_DEFAULT_PIXABAY": "1",
        "MPT_DEFAULT_OPENAI_IMAGE": "1", "MPT_OPENAI_IMAGE_MODEL": "example-model",
        "OPENROUTER_API_KEY": "secret", "OPENAI_IMAGE_API_KEY": "another-secret",
    })
    assert settings == {
        "llm_provider": "openrouter", "openrouter_model_name": "openrouter/free",
        "video_source": "pixabay", "openai_image_base_url": "https://api.openai.com/v1",
        "openai_image_model": "example-model",
    }


def test_openai_image_opt_in_has_official_defaults_and_preserves_custom_values():
    settings = {}
    config.apply_host_defaults(settings, {"MPT_DEFAULT_OPENAI_IMAGE": "1"})
    assert settings == {"openai_image_base_url": "https://api.openai.com/v1", "openai_image_model": "gpt-image-1"}
    custom = {"openai_image_base_url": "https://images.example/v1"}
    config.apply_host_defaults(custom, {"MPT_DEFAULT_OPENAI_IMAGE": "1"})
    assert "openai_image_model" not in custom
    config.apply_host_defaults(custom, {"MPT_DEFAULT_OPENAI_IMAGE": "1", "MPT_OPENAI_IMAGE_MODEL": "another-model"})
    assert custom["openai_image_model"] == "another-model"


def test_openai_image_readiness_detects_missing_official_key(monkeypatch):
    monkeypatch.delenv("OPENAI_IMAGE_API_KEY", raising=False)
    settings = {"openai_image_base_url": "https://api.openai.com/v1", "openai_image_model": "gpt-image-1"}
    assert "API key" in material.openai_image_configuration_issue(settings)
    assert not material.is_openai_image_enabled(settings)
    monkeypatch.setenv("OPENAI_IMAGE_API_KEY", "synthetic-secret")
    assert material.is_openai_image_enabled(settings)
    monkeypatch.delenv("OPENAI_IMAGE_API_KEY")
    assert material.is_openai_image_enabled({**settings, "openai_image_api_keys": ["synthetic-key"]})
    assert material.is_openai_image_enabled({**settings, "openai_image_base_url": "http://localhost:8080/v1"})
    assert "model" in material.openai_image_configuration_issue({"openai_image_base_url": "https://api.openai.com/v1"})


def test_pixabay_environment_key_is_used_without_writing_config(monkeypatch):
    monkeypatch.setenv("PIXABAY_API_KEY", "test-pixabay-env")
    monkeypatch.setattr(config, "app", {"pixabay_api_keys": []})
    assert material.get_api_key("pixabay_api_keys") == "test-pixabay-env"
    assert config.app["pixabay_api_keys"] == []
    groups = {"stock": ["pixabay"]}
    assert "pixabay" in settings_providers.configured_material_providers(config.app, groups)["stock"]


def test_openrouter_env_key_is_only_used_with_official_endpoint(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-env")
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    def client(**kwargs):
        return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(respond)))

    settings = {"llm_provider": "openrouter", "openrouter_model_name": "openrouter/free"}
    with patch.object(llm, "OpenAI", client):
        assert llm._generate_response("hello", app_config=settings) == "ok"
        assert seen[0].headers["authorization"] == "Bearer test-openrouter-env"
        assert "openrouter" in settings_providers.configured_llm_providers(settings, {})
        assert "api_key is not set" in llm._generate_response(
            "hello", app_config={**settings, "openrouter_base_url": "https://other.example/v1"}
        )
    assert len(seen) == 1
    assert "openrouter_api_key" not in settings


def test_openai_environment_key_is_restricted_to_official_endpoint(monkeypatch):
    monkeypatch.setenv("OPENAI_IMAGE_API_KEY", "test-image-env")
    monkeypatch.setattr(config, "app", {"openai_image_api_keys": []})
    monkeypatch.setattr(config, "proxy", {})
    response = SimpleNamespace(status_code=200, json=lambda: {"data": [{"b64_json": base64.b64encode(b"image").decode()}]})
    with patch.object(material.requests, "post", return_value=response) as post:
        material._request_openai_image("https://api.openai.com/v1/images/generations", {})
        assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer test-image-env"
        material._request_openai_image("https://other.example/v1/images/generations", {})
        assert "Authorization" not in post.call_args.kwargs["headers"]
    assert config.app["openai_image_api_keys"] == []


def test_jalapeno_environment_key_refuses_custom_endpoint(monkeypatch):
    monkeypatch.setenv("JALAPENO_VIDEO_API_KEY", "test-jalapeno-env")
    with pytest.raises(jalapeno_video.JalapenoVideoError, match="official"):
        jalapeno_video.endpoint({"jalapeno_video_base_url": "https://other.example/api/v3"})
    endpoint = jalapeno_video.endpoint({})
    assert endpoint.api_key == "test-jalapeno-env"
