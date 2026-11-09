import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt
import pytest

from app.services import user_accounts
from app.services.user_accounts import UserAccountError
from webui.device_key_store import build_device_vault, unwrap_device_payload


@pytest.fixture
def accounts_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MPT_ACCOUNTS_DIR", str(tmp_path))
    return tmp_path


@pytest.mark.parametrize("payload", [
    {"secret": "stable", "users": None},
    {"secret": "stable", "users": ["invalid-user"]},
    {"secret": "", "users": []},
    {"users": []},
])
def test_corrupt_account_store_fails_without_overwriting(accounts_dir, payload):
    path = accounts_dir / "accounts.json"
    original = json.dumps(payload)
    path.write_text(original, encoding="utf-8")
    with pytest.raises(UserAccountError, match="Auth Storage Error"):
        user_accounts.create_email_user("new@example.com", "password1234")
    assert path.read_text(encoding="utf-8") == original


def test_email_signup_and_login_round_trip(accounts_dir):
    public, token, device = user_accounts.create_email_user(
        "Logesh@Gmail.com", "correct-horse", "Logesh"
    )
    assert public["email"] == "logesh@gmail.com"
    assert public["display_name"] == "Logesh"
    assert "password_hash" not in public
    assert "password_hash" not in device
    assert "password_salt" not in device
    restored = user_accounts.user_from_session(token)
    assert restored["id"] == public["id"]

    again, token2, _ = user_accounts.authenticate_email("logesh@gmail.com", "correct-horse")
    assert again["id"] == public["id"]
    assert token2


def test_legacy_password_is_upgraded_only_after_valid_login(accounts_dir):
    user, _, _ = user_accounts.create_email_user("user@example.com", "password1234")
    salt = "ab" * 16
    def legacy(store):
        record = user_accounts._find_user(store, user_id=user["id"])
        record["password_salt"] = salt
        record["password_hash"] = user_accounts._hash_password("old-pass", salt)
    user_accounts._with_store(legacy)
    with pytest.raises(UserAccountError):
        user_accounts.authenticate_email("user@example.com", "wrong")
    assert user_accounts._read_store()["users"][0]["password_salt"] == salt
    user_accounts.authenticate_email("user@example.com", "old-pass")
    updated = user_accounts._read_store()["users"][0]
    assert updated["password_hash"].startswith("$argon2id$")
    assert not updated["password_salt"]


def test_duplicate_email_is_rejected(accounts_dir):
    user_accounts.create_email_user("user@example.com", "password1234")
    with pytest.raises(UserAccountError) as exc:
        user_accounts.create_email_user("user@example.com", "password1234")
    assert exc.value.code == "Auth Email Taken"


def test_wrong_password_does_not_reveal_whether_email_exists(accounts_dir):
    user_accounts.create_email_user("user@example.com", "password1234")
    with pytest.raises(UserAccountError) as missing:
        user_accounts.authenticate_email("missing@example.com", "password1234")
    with pytest.raises(UserAccountError) as wrong:
        user_accounts.authenticate_email("user@example.com", "wrong-password")
    assert missing.value.code == wrong.value.code == "Auth Login Failed"


def test_short_password_and_invalid_email_are_rejected(accounts_dir):
    with pytest.raises(UserAccountError) as short:
        user_accounts.create_email_user("user@example.com", "short")
    assert short.value.code == "Auth Password Too Short"
    with pytest.raises(UserAccountError) as invalid:
        user_accounts.create_email_user("not-an-email", "password1234")
    assert invalid.value.code == "Auth Invalid Email"


def test_oauth_identity_cannot_link_by_email_without_consent(accounts_dir):
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

    with pytest.raises(UserAccountError, match="Auth OAuth Failed"):
        user_accounts.upsert_oauth_user(
            "github", "42", "person@gmail.com", github_login="person"
        )
    again, _, _ = user_accounts.upsert_oauth_user("google", "google-123", "person@gmail.com")
    assert again["id"] == public["id"]


def test_expired_session_is_rejected(accounts_dir):
    public, _, _ = user_accounts.create_email_user("user@example.com", "password1234")
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


def test_device_account_cannot_recreate_or_authenticate_a_user(accounts_dir):
    forged = {"id": "victim", "email": "victim@example.com", "password_hash": "fake"}
    assert user_accounts.restore_device_account(forged) is None
    assert not (accounts_dir / "accounts.json").exists()


def test_logout_revokes_only_one_of_two_sessions(accounts_dir):
    user, token1, _ = user_accounts.create_email_user("user@example.com", "password1234")
    _, token2, _ = user_accounts.authenticate_email("user@example.com", "password1234")
    assert token1 != token2
    store = user_accounts._load_store_unlocked()
    assert token1 not in json.dumps(store)
    assert user_accounts.revoke_session(token1)
    assert user_accounts.user_from_session(token1) is None
    assert user_accounts.user_from_session(token2)["id"] == user["id"]
    assert not user_accounts.revoke_session(token1)


def test_legacy_stateless_token_is_not_accepted(accounts_dir):
    public, _, _ = user_accounts.create_email_user("user@example.com", "password1234")
    store = user_accounts._load_store_unlocked()
    legacy = jwt.encode({"uid": public["id"], "exp": datetime.now(timezone.utc) + timedelta(days=1)},
                        store["secret"], algorithm="HS256")
    assert user_accounts.user_from_session(legacy) is None


def test_unchanged_key_backup_does_not_rewrite_accounts(accounts_dir):
    user, _, _ = user_accounts.create_email_user("user@example.com", "password1234")
    keys = {"app": {"openai_api_key": "synthetic"}}
    user_accounts.update_user_keys(user["id"], keys)
    with patch.object(user_accounts, "_save_store_unlocked") as save:
        user_accounts.update_user_keys(user["id"], keys)
        user_accounts.update_user_keys("missing-user", keys)
    save.assert_not_called()
    assert user_accounts.get_user_keys(user["id"]) == keys


def test_per_user_keys_are_isolated(accounts_dir):
    first, _, _ = user_accounts.create_email_user("a@example.com", "password1234")
    second, _, _ = user_accounts.create_email_user("b@example.com", "password1234")
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
