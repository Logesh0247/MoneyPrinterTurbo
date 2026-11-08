"""Remember API keys in this browser so a phone or cloud restart does not ask again.

Render, Docker and Streamlit sessions can lose ``config.toml``. Password widgets
also replay as empty after a mobile reconnect. The device store keeps a copy of
the same key-backup payload in ``localStorage`` on this origin, and the WebUI
restores any credentials the server no longer has.
"""

from pathlib import Path

from loguru import logger

_UNSET = object()


LOCAL_STORAGE_KEY = "moneyprinterturbo.device-keys.v1"
DEVICE_VAULT_SCHEMA = "moneyprinterturbo.device-vault"
DEVICE_VAULT_VERSION = 1
KEY_BACKUP_SCHEMA = "moneyprinterturbo.key-backup"
COMPONENT_KEY = "mpt_device_key_store"
SESSION_COOKIE_NAME = "mpt_session"
SESSION_COOKIE_MAX_AGE = 90 * 24 * 60 * 60
CREDENTIAL_KEY_SUFFIXES = (
    "api_key",
    "api_keys",
    "api_token",
    "access_key",
    "secret_key",
    "speech_key",
)

_COMPONENT = None
_COMPONENT_UNAVAILABLE = False


def is_credential_key(key):
    """Return True when a config item name is an API credential."""
    return str(key).endswith(CREDENTIAL_KEY_SUFFIXES)


def is_blank_value(value):
    """Return True when a config value does not contain a usable secret."""
    if isinstance(value, (list, tuple)):
        return not any(str(item).strip() for item in value if item is not None)
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    return not str(value).strip()


def backup_has_credentials(backup):
    """Return True when the backup contains at least one non-empty secret."""
    if not isinstance(backup, dict):
        return False
    for entries in backup.values():
        if not isinstance(entries, dict):
            continue
        for key, value in entries.items():
            if is_credential_key(key) and not is_blank_value(value):
                return True
    return False


def unwrap_device_payload(payload):
    """
    Split a device blob into (account, session_token, keys_backup).

    Older installs stored only the key-backup JSON. New installs wrap account,
    session and keys together so a phone can restore sign-in after a restart.
    """
    if not isinstance(payload, dict):
        return None, None, None
    if payload.get("schema") == DEVICE_VAULT_SCHEMA:
        account = payload.get("account")
        token = str(payload.get("session_token") or "").strip() or None
        keys_backup = payload.get("keys_backup")
        if not isinstance(account, dict):
            account = None
        if not isinstance(keys_backup, dict):
            keys_backup = None
        return account, token, keys_backup
    if payload.get("schema") == KEY_BACKUP_SCHEMA or "keys" in payload:
        return None, None, payload
    return None, None, None


def build_device_vault(account=None, session_token=None, keys_backup=None, replace=False):
    """Build the JSON blob written to this device. Empty parts are omitted."""
    vault = {"schema": DEVICE_VAULT_SCHEMA, "version": DEVICE_VAULT_VERSION}
    if isinstance(account, dict) and account.get("id"):
        vault["account"] = account
    token = str(session_token or "").strip()
    if token:
        vault["session_token"] = token
    if isinstance(keys_backup, dict) and keys_backup.get("keys"):
        vault["keys_backup"] = keys_backup
    if replace:
        return vault
    return vault if ("account" in vault or "session_token" in vault or "keys_backup" in vault) else None


def keys_to_apply(stored_keys, current_keys):
    """
    Choose which device-stored credentials should be written to the server.

    A blank server (typical after a free-tier sleep or container recreate)
    takes the whole device copy, including the selected LLM / video providers.
    A server that already has secrets only receives keys it is still missing,
    so a stale phone copy cannot overwrite newer values.
    """
    if not isinstance(stored_keys, dict) or not stored_keys:
        return {}
    current_keys = current_keys if isinstance(current_keys, dict) else {}
    if not backup_has_credentials(current_keys):
        return {
            section: dict(entries)
            for section, entries in stored_keys.items()
            if isinstance(entries, dict) and entries
        }

    restored = {}
    for section, entries in stored_keys.items():
        if not isinstance(entries, dict):
            continue
        current_section = current_keys.get(section)
        if not isinstance(current_section, dict):
            current_section = {}
        section_entries = {}
        for key, value in entries.items():
            if is_blank_value(value) or not is_blank_value(current_section.get(key)):
                continue
            section_entries[key] = value
        if section_entries:
            restored[section] = section_entries
    return restored


def _component():
    """Load the localStorage iframe once; tests and headless runs can skip it."""
    global _COMPONENT, _COMPONENT_UNAVAILABLE
    if _COMPONENT_UNAVAILABLE:
        return None
    if _COMPONENT is not None:
        return _COMPONENT
    component_dir = Path(__file__).resolve().parent / "components" / "device_key_store"
    if not (component_dir / "index.html").is_file():
        logger.debug("device key store component is missing")
        _COMPONENT_UNAVAILABLE = True
        return None
    try:
        import streamlit.components.v1 as components

        _COMPONENT = components.declare_component(
            "mpt_device_key_store",
            path=str(component_dir),
        )
    except Exception as exc:
        logger.debug(f"device key store unavailable: {exc}")
        _COMPONENT_UNAVAILABLE = True
        return None
    return _COMPONENT


def device_key_store_available():
    """False when the iframe cannot run (tests, missing files)."""
    return _component() is not None


def render_device_key_store(payload_to_save=None, session_token=_UNSET):
    """
    Read this device's remembered keys and optionally overwrite them.

    The first Streamlit render returns ``None`` (the iframe default). After the
    browser replies, the value is ``{"ready": True, "payload": ...}``. Passing
    ``payload_to_save=None`` never clears localStorage. Pass ``session_token=""``
    to delete the session cookie; omit it to leave the cookie unchanged.
    """
    component = _component()
    if component is None:
        return None
    kwargs = {
        "payload_to_save": payload_to_save,
        "default": None,
        "key": COMPONENT_KEY,
    }
    if session_token is not _UNSET:
        kwargs["session_token"] = "" if session_token is None else str(session_token)
        kwargs["session_max_age"] = SESSION_COOKIE_MAX_AGE
        kwargs["cookie_name"] = SESSION_COOKIE_NAME
    try:
        return component(**kwargs)
    except Exception as exc:
        logger.debug(f"device key store render failed: {exc}")
        return None
