"""Fill blank API credentials from the process environment.

Render, Docker and local shells can keep keys in environment variables so the
WebUI does not ask the user to paste them on every visit. Existing config.toml
values always win; environment values only fill empty fields.
"""

from __future__ import annotations

import os

from loguru import logger

from app.models.llm_provider import DEFAULT_LLM_PROVIDER_ID, get_llm_provider


def _is_blank(value) -> bool:
    if isinstance(value, (list, tuple)):
        return not any(str(item).strip() for item in value if item is not None)
    return not str(value or "").strip()


def environment_text(environ, *names) -> str:
    """Return the first non-empty environment value among ``names``."""
    for name in names:
        if not name:
            continue
        text = str(environ.get(name, "") or "").strip()
        if text:
            return text
    return ""


def _fill(app_section, key, value, *, as_list=False) -> bool:
    if not value or not _is_blank(app_section.get(key)):
        return False
    app_section[key] = [value] if as_list else value
    return True


def _provider_env_prefix(provider_id: str) -> str:
    return str(provider_id or "").strip().upper()


def apply_environment_api_settings(app_section, environ=None) -> tuple[str, ...]:
    """
    Copy host environment credentials into a config ``app`` mapping.

    Returns the names of fields that were filled. Never logs secret values.
    """
    if not isinstance(app_section, dict):
        return ()
    environ = os.environ if environ is None else environ
    changed = []

    requested_provider = environment_text(
        environ, "MPT_LLM_PROVIDER", "LLM_PROVIDER"
    ).lower()
    if requested_provider == "openai_compatible":
        requested_provider = "openai"
    if requested_provider and get_llm_provider(requested_provider) is not None:
        current = str(app_section.get("llm_provider") or "").strip()
        if current != requested_provider:
            app_section["llm_provider"] = requested_provider
            changed.append("llm_provider")

    active_id = str(
        app_section.get("llm_provider") or DEFAULT_LLM_PROVIDER_ID
    ).strip().lower()
    provider = get_llm_provider(active_id)

    llm_key = environment_text(environ, "MPT_LLM_API_KEY")
    llm_base = environment_text(environ, "MPT_LLM_BASE_URL")
    llm_model = environment_text(environ, "MPT_LLM_MODEL_NAME")
    if provider is not None:
        prefix = _provider_env_prefix(provider.provider_id)
        extra_key_names = (f"{prefix}_API_KEY",)
        extra_base_names = (f"{prefix}_BASE_URL",)
        extra_model_names = (f"{prefix}_MODEL_NAME", f"{prefix}_MODEL")
        if provider.provider_id == "openai":
            extra_key_names = ("OPENAI_API_KEY", *extra_key_names)
            extra_base_names = ("OPENAI_BASE_URL", *extra_base_names)
            extra_model_names = (
                "OPENAI_MODEL_NAME",
                "OPENAI_MODEL",
                *extra_model_names,
            )
        if _fill(
            app_section,
            provider.config_key("api_key"),
            llm_key or environment_text(environ, *extra_key_names),
        ):
            changed.append(provider.config_key("api_key"))
        if _fill(
            app_section,
            provider.config_key("base_url"),
            llm_base or environment_text(environ, *extra_base_names),
        ):
            changed.append(provider.config_key("base_url"))
        if _fill(
            app_section,
            provider.config_key("model_name"),
            llm_model or environment_text(environ, *extra_model_names),
        ):
            changed.append(provider.config_key("model_name"))

    openai_key = environment_text(environ, "OPENAI_API_KEY", "MPT_OPENAI_API_KEY")
    if _fill(app_section, "openai_api_key", openai_key):
        changed.append("openai_api_key")
    if _fill(
        app_section,
        "openai_base_url",
        environment_text(environ, "OPENAI_BASE_URL", "MPT_OPENAI_BASE_URL"),
    ):
        changed.append("openai_base_url")
    if _fill(
        app_section,
        "openai_model_name",
        environment_text(
            environ, "OPENAI_MODEL_NAME", "OPENAI_MODEL", "MPT_OPENAI_MODEL_NAME"
        ),
    ):
        changed.append("openai_model_name")

    if (
        openai_key
        and provider is not None
        and provider.requires_api_key
        and _is_blank(app_section.get(provider.config_key("api_key")))
        and not requested_provider
    ):
        app_section["llm_provider"] = "openai"
        changed.append("llm_provider")
        if _fill(app_section, "openai_api_key", openai_key):
            changed.append("openai_api_key")

    if _fill(
        app_section,
        "openai_image_base_url",
        environment_text(
            environ, "OPENAI_IMAGE_BASE_URL", "MPT_OPENAI_IMAGE_BASE_URL"
        ),
    ):
        changed.append("openai_image_base_url")
    if _fill(
        app_section,
        "openai_image_model",
        environment_text(
            environ, "OPENAI_IMAGE_MODEL", "MPT_OPENAI_IMAGE_MODEL"
        ),
    ):
        changed.append("openai_image_model")
    if _fill(
        app_section,
        "openai_image_api_keys",
        environment_text(
            environ,
            "OPENAI_IMAGE_API_KEY",
            "OPENAI_IMAGE_API_KEYS",
            "MPT_OPENAI_IMAGE_API_KEY",
        ),
        as_list=True,
    ):
        changed.append("openai_image_api_keys")

    material_keys = (
        (
            "pexels_api_keys",
            True,
            ("MPT_PEXELS_API_KEY", "PEXELS_API_KEY", "PEXELS_API_KEYS"),
        ),
        (
            "pixabay_api_keys",
            True,
            ("MPT_PIXABAY_API_KEY", "PIXABAY_API_KEY"),
        ),
        (
            "coverr_api_keys",
            True,
            ("MPT_COVERR_API_KEY", "COVERR_API_KEY"),
        ),
        (
            "volcengine_seedance_api_key",
            False,
            ("MPT_VOLCENGINE_ARK_API_KEY", "VOLCENGINE_ARK_API_KEY"),
        ),
        ("ofox_api_key", False, ("MPT_OFOX_API_KEY", "OFOX_API_KEY")),
        (
            "metaso_minimax_api_key",
            False,
            ("MPT_METASO_MINIMAX_API_KEY", "METASO_MINIMAX_API_KEY"),
        ),
        ("muapi_api_key", False, ("MPT_MUAPI_API_KEY", "MUAPI_API_KEY")),
        (
            "infistar_video_api_key",
            False,
            ("MPT_INFISTAR_VIDEO_API_KEY", "INFISTAR_VIDEO_API_KEY"),
        ),
        (
            "jalapeno_video_api_key",
            False,
            ("MPT_JALAPENO_VIDEO_API_KEY", "JALAPENO_VIDEO_API_KEY"),
        ),
    )
    for key, as_list, names in material_keys:
        if _fill(app_section, key, environment_text(environ, *names), as_list=as_list):
            changed.append(key)

    if changed:
        logger.info("applied environment credentials: " + ", ".join(changed))
    return tuple(changed)


def api_key_from_environment(provider_id, environ=None) -> str:
    """Return an environment API key for one LLM provider, if present."""
    environ = os.environ if environ is None else environ
    pid = str(provider_id or "").strip().lower()
    prefix = _provider_env_prefix(pid)
    names = [f"MPT_{prefix}_API_KEY", f"{prefix}_API_KEY"]
    if pid == "openai":
        names.extend(("OPENAI_API_KEY", "MPT_LLM_API_KEY"))
    else:
        names.append("MPT_LLM_API_KEY")
    return environment_text(environ, *names)
