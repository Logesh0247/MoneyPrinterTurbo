from webui.device_key_store import (
    backup_has_credentials,
    is_blank_value,
    is_credential_key,
    keys_to_apply,
)


def test_credential_key_detection_matches_project_suffixes():
    assert is_credential_key("openai_api_key")
    assert is_credential_key("pexels_api_keys")
    assert is_credential_key("loomloom_api_token")
    assert is_credential_key("speech_key")
    assert not is_credential_key("llm_provider")
    assert not is_credential_key("openai_base_url")


def test_blank_value_treats_empty_lists_and_whitespace_as_missing():
    assert is_blank_value("")
    assert is_blank_value("   ")
    assert is_blank_value([])
    assert is_blank_value(["", " "])
    assert is_blank_value(None)
    assert not is_blank_value("sk-live")
    assert not is_blank_value(["pexels-1"])
    assert not is_blank_value(0)


def test_blank_server_restores_the_whole_device_copy():
    stored = {
        "app": {
            "openai_api_key": "sk-phone",
            "llm_provider": "openai",
            "video_source": "pexels",
            "pexels_api_keys": ["pexels-1"],
        }
    }
    current = {"app": {"llm_provider": "moonshot", "openai_api_key": ""}}

    assert backup_has_credentials(current) is False
    assert keys_to_apply(stored, current) == stored


def test_server_with_keys_only_fills_missing_credentials():
    stored = {
        "app": {
            "openai_api_key": "sk-stale",
            "gemini_api_key": "gemini-phone",
            "llm_provider": "openai",
        },
        "elevenlabs": {"api_key": "eleven-phone"},
    }
    current = {
        "app": {"openai_api_key": "sk-server", "llm_provider": "moonshot"},
    }

    assert keys_to_apply(stored, current) == {
        "app": {"gemini_api_key": "gemini-phone"},
        "elevenlabs": {"api_key": "eleven-phone"},
    }


def test_device_store_ignores_empty_or_invalid_payloads():
    assert keys_to_apply(None, {}) == {}
    assert keys_to_apply({}, {"app": {"openai_api_key": "sk"}}) == {}
    assert keys_to_apply("not-a-dict", {}) == {}
