from app.config.env_settings import (
    api_key_from_environment,
    apply_environment_api_settings,
)


def test_environment_fills_openai_key_model_and_switches_empty_provider():
    app = {"llm_provider": "moonshot", "moonshot_api_key": ""}
    changed = apply_environment_api_settings(
        app,
        {
            "OPENAI_API_KEY": " sk-env-openai ",
            "OPENAI_BASE_URL": "https://api.openai.com/v1",
            "OPENAI_MODEL": "gpt-4.1-mini",
        },
    )

    assert app["llm_provider"] == "openai"
    assert app["openai_api_key"] == "sk-env-openai"
    assert app["openai_base_url"] == "https://api.openai.com/v1"
    assert app["openai_model_name"] == "gpt-4.1-mini"
    assert "llm_provider" in changed
    assert "openai_api_key" in changed


def test_environment_does_not_overwrite_saved_keys():
    app = {
        "llm_provider": "openai",
        "openai_api_key": "sk-saved",
        "openai_model_name": "saved-model",
    }
    changed = apply_environment_api_settings(
        app,
        {"OPENAI_API_KEY": "sk-env", "OPENAI_MODEL": "env-model"},
    )

    assert app["openai_api_key"] == "sk-saved"
    assert app["openai_model_name"] == "saved-model"
    assert changed == ()


def test_mpt_llm_variables_apply_to_the_selected_provider():
    app = {"llm_provider": "deepseek", "deepseek_api_key": ""}
    apply_environment_api_settings(
        app,
        {
            "MPT_LLM_PROVIDER": "deepseek",
            "MPT_LLM_API_KEY": "sk-deepseek-env",
            "MPT_LLM_MODEL_NAME": "deepseek-chat",
        },
    )

    assert app["llm_provider"] == "deepseek"
    assert app["deepseek_api_key"] == "sk-deepseek-env"
    assert app["deepseek_model_name"] == "deepseek-chat"


def test_image_environment_fills_base_url_model_and_optional_key():
    app = {}
    changed = apply_environment_api_settings(
        app,
        {
            "OPENAI_IMAGE_BASE_URL": "https://images.example/v1",
            "OPENAI_IMAGE_MODEL": "gpt-image-2",
            "OPENAI_IMAGE_API_KEY": "sk-image-env",
        },
    )

    assert app["openai_image_base_url"] == "https://images.example/v1"
    assert app["openai_image_model"] == "gpt-image-2"
    assert app["openai_image_api_keys"] == ["sk-image-env"]
    assert "openai_image_base_url" in changed


def test_api_key_from_environment_uses_standard_openai_name():
    assert (
        api_key_from_environment(
            "openai", {"OPENAI_API_KEY": "sk-from-env"}
        )
        == "sk-from-env"
    )
    assert api_key_from_environment("openai", {}) == ""
