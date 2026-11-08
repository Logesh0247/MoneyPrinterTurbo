import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest

from app.services import user_accounts
from app.services.user_accounts import UserAccountError
from webui.device_key_store import build_device_vault, unwrap_device_payload


@pytest.fixture
def accounts_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MPT_ACCOUNTS_DIR", str(tmp_path))
    return tmp_path


def test_email_signup_and_login_round_trip(accounts_dir):
    public, token, device = user_accounts.create_email_user(
        "Logesh@Gmail.com", "correct-horse", "Logesh"
    )
    assert public["email"] == "logesh@gmail.com"
    assert public["display_name"] == "Logesh"
    assert "password_hash" not in public
    assert device["password_hash"]
    restored = user_accounts.user_from_session(token)
    assert restored["id"] == public["id"]

    again, token2, _ = user_accounts.authenticate_email("logesh@gmail.com", "correct-horse")
    assert again["id"] == public["id"]
    assert token2


def test_duplicate_email_is_rejected(accounts_dir):
    user_accounts.create_email_user("user@example.com", "password12")
    with pytest.raises(UserAccountError) as exc:
        user_accounts.create_email_user("user@example.com", "password12")
    assert exc.value.code == "Auth Email Taken"


def test_wrong_password_does_not_reveal_whether_email_exists(accounts_dir):
    user_accounts.create_email_user("user@example.com", "password12")
    with pytest.raises(UserAccountError) as missing:
        user_accounts.authenticate_email("missing@example.com", "password12")
    with pytest.raises(UserAccountError) as wrong:
        user_accounts.authenticate_email("user@example.com", "wrong-password")
    assert missing.value.code == wrong.value.code == "Auth Login Failed"


def test_short_password_and_invalid_email_are_rejected(accounts_dir):
    with pytest.raises(UserAccountError) as short:
        user_accounts.create_email_user("user@example.com", "short")
    assert short.value.code == "Auth Password Too Short"
    with pytest.raises(UserAccountError) as invalid:
        user_accounts.create_email_user("not-an-email", "password12")
    assert invalid.value.code == "Auth Invalid Email"


def test_oauth_google_creates_user_and_links_by_email(accounts_dir):
    public, token, _ = user_accounts.upsert_oauth_user(
        "google",
        "google-123",
        "person@gmail.com",
        display_name="Person",
        avatar_url="https://example.com/a.png",
    )
    assert public["google_id"] == "google-123"
    assert "google" in public["auth_providers"]
    assert user_accounts.user_from_session(token)["email"] == "person@gmail.com"

    linked, _, _ = user_accounts.upsert_oauth_user(
        "github",
        "42",
        "person@gmail.com",
        github_login="person",
    )
    assert linked["id"] == public["id"]
    assert linked["github_id"] == "42"
    assert linked["google_id"] == "google-123"


def test_expired_session_is_rejected(accounts_dir):
    public, _, _ = user_accounts.create_email_user("user@example.com", "password12")
    store = user_accounts._load_store_unlocked()
    expired = jwt.encode(
        {
            "uid": public["id"],
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
        },
        store["secret"],
        algorithm="HS256",
    )
    assert user_accounts.user_from_session(expired) is None


def test_device_restore_recreates_account_after_server_wipe(accounts_dir):
    public, _, device = user_accounts.create_email_user(
        "phone@gmail.com", "password12", "Phone"
    )
    user_accounts.update_user_keys(public["id"], {"app": {"openai_api_key": "sk-phone"}})
    device["keys"] = {"app": {"openai_api_key": "sk-phone"}}

    Path(os.environ["MPT_ACCOUNTS_DIR"], "accounts.json").unlink()

    restored = user_accounts.restore_device_account(device)
    assert restored is not None
    restored_public, token, _ = restored
    assert restored_public["email"] == "phone@gmail.com"
    assert user_accounts.user_from_session(token)["id"] == public["id"]
    assert user_accounts.get_user_keys(public["id"])["app"]["openai_api_key"] == "sk-phone"
    _, login_token, _ = user_accounts.authenticate_email("phone@gmail.com", "password12")
    assert login_token


def test_per_user_keys_are_isolated(accounts_dir):
    first, _, _ = user_accounts.create_email_user("a@example.com", "password12")
    second, _, _ = user_accounts.create_email_user("b@example.com", "password12")
    user_accounts.update_user_keys(first["id"], {"app": {"openai_api_key": "sk-a"}})
    user_accounts.update_user_keys(second["id"], {"app": {"openai_api_key": "sk-b"}})
    assert user_accounts.get_user_keys(first["id"])["app"]["openai_api_key"] == "sk-a"
    assert user_accounts.get_user_keys(second["id"])["app"]["openai_api_key"] == "sk-b"


def test_oauth_pending_survives_a_new_browser_session(accounts_dir):
    user_accounts.save_oauth_pending(
        "google.abc", "google", "verifier", "https://example.com/"
    )
    pending = user_accounts.pop_oauth_pending("google.abc")
    assert pending["provider"] == "google"
    assert pending["verifier"] == "verifier"
    assert user_accounts.pop_oauth_pending("google.abc") is None


def test_auth_gate_is_disabled_during_pytest():
    from webui.auth import auth_gate_enabled

    assert auth_gate_enabled() is False


def test_public_origin_comes_from_environment(monkeypatch):
    monkeypatch.setenv("MPT_PUBLIC_URL", "https://videos.example.com/")
    from webui import auth as webui_auth

    assert webui_auth.public_base_url() == "https://videos.example.com"
    assert webui_auth.oauth_redirect_uri() == "https://videos.example.com/"


def test_device_vault_wraps_account_and_keeps_legacy_key_backup():
    vault = build_device_vault(
        account={"id": "usr_1", "email": "a@example.com"},
        session_token="tok",
        keys_backup={"schema": "moneyprinterturbo.key-backup", "keys": {"app": {"k": "v"}}},
    )
    account, token, keys = unwrap_device_payload(vault)
    assert account["id"] == "usr_1"
    assert token == "tok"
    assert keys["keys"]["app"]["k"] == "v"

    legacy = {"schema": "moneyprinterturbo.key-backup", "version": 1, "keys": {"app": {}}}
    account, token, keys = unwrap_device_payload(legacy)
    assert account is None
    assert token is None
    assert keys["schema"] == "moneyprinterturbo.key-backup"
