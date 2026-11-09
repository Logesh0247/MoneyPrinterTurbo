from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.services import oauth_login, user_accounts
from webui import auth


@pytest.fixture
def accounts(monkeypatch, tmp_path):
    monkeypatch.setenv("MPT_ACCOUNTS_DIR", str(tmp_path))


def test_oauth_collision_with_unverified_local_signup_is_rejected(accounts):
    user, _, _ = user_accounts.create_email_user("victim@example.com", "long-passphrase")
    with pytest.raises(user_accounts.UserAccountError):
        user_accounts.upsert_oauth_user("google", "google-victim", "victim@example.com")
    assert user_accounts.get_user(user["id"])["google_id"] == ""


def test_oauth_pending_state_expires_on_consumption(accounts):
    user_accounts.save_oauth_pending("google.test", "google", "verifier", "https://example.test/")
    def expire(store):
        store["oauth_pending"]["google.test"]["created_at"] = user_accounts._iso(
            user_accounts._utc_now() - timedelta(minutes=21))
    user_accounts._with_store(expire)
    assert user_accounts.pop_oauth_pending("google.test") is None
    assert user_accounts.pop_oauth_pending("google.test") is None


def test_github_requires_verified_email(monkeypatch):
    monkeypatch.setattr(oauth_login, "github_configured", lambda: True)
    token = MagicMock()
    token.json.return_value = {"access_token": "synthetic"}
    profile = MagicMock()
    profile.json.return_value = {"id": 1, "email": "unverified@example.com"}
    emails = MagicMock()
    emails.json.return_value = [{"email": "unverified@example.com", "verified": False}]
    with (patch.object(oauth_login.requests, "post", return_value=token),
          patch.object(oauth_login.requests, "get", side_effect=[profile, emails])):
        with pytest.raises(user_accounts.UserAccountError):
            oauth_login.complete_github_login("code", "https://example.test/")


def test_callback_rejects_state_not_in_initiating_session(accounts):
    user_accounts.save_oauth_pending("google.attacker", "google", "verifier", "https://example.test/")
    with (patch.object(auth.st, "session_state", {}),
          patch.object(auth.st, "query_params", {"code": "code", "state": "google.attacker"}),
          patch.object(oauth_login, "complete_google_login") as exchange):
        assert not auth.consume_oauth_callback()
        exchange.assert_not_called()
    assert user_accounts.pop_oauth_pending("google.attacker") is not None


def test_valid_oauth_callback_consumes_state_once(accounts):
    state = "google.valid"
    user_accounts.save_oauth_pending(state, "google", "verifier", "https://example.test/")
    browser = {"oauth_google_state": state, "oauth_google_verifier": "verifier"}
    profile = {"provider": "google", "provider_id": "id-1", "email": "person@example.com"}
    with (patch.object(auth.st, "session_state", browser),
          patch.object(auth.st, "query_params", {"code": "code", "state": state}),
          patch.object(oauth_login, "complete_google_login", return_value=profile)):
        assert auth.consume_oauth_callback()
        assert auth.current_user()["email"] == "person@example.com"
    assert user_accounts.pop_oauth_pending(state) is None


def test_oauth_redirect_never_uses_request_host(monkeypatch):
    monkeypatch.delenv("MPT_PUBLIC_URL", raising=False)
    monkeypatch.delenv("OAUTH_REDIRECT_URI", raising=False)
    with patch.object(auth.st, "context", MagicMock(headers={"Host": "attacker.test"})):
        assert auth.oauth_redirect_uri() == ""
    monkeypatch.setenv("MPT_PUBLIC_URL", "https://example.test/path")
    assert auth.oauth_redirect_uri() == ""
    monkeypatch.setenv("MPT_PUBLIC_URL", "https://example.test/")
    assert auth.oauth_redirect_uri() == "https://example.test/"


def test_profile_update_requires_valid_session_and_is_user_scoped(accounts):
    first, first_token, _ = user_accounts.create_email_user("a@example.com", "long-passphrase")
    second, _, _ = user_accounts.create_email_user("b@example.com", "long-passphrase")
    updated = user_accounts.update_display_name(first_token, "Alice")
    assert updated["id"] == first["id"]
    assert user_accounts.get_user(first["id"])["display_name"] == "Alice"
    assert user_accounts.get_user(second["id"])["display_name"] != "Alice"
    with pytest.raises(user_accounts.UserAccountError):
        user_accounts.update_display_name(first_token, "  ")
    user_accounts.revoke_session(first_token)
    with pytest.raises(user_accounts.UserAccountError):
        user_accounts.update_display_name(first_token, "Changed")
    assert user_accounts.get_user(first["id"])["display_name"] == "Alice"


def test_live_session_rechecks_revocation_and_logout_revokes(accounts):
    user, token, _ = user_accounts.create_email_user("user@example.com", "long-passphrase")
    browser = {"current_user": user, "session_token": token}
    with patch.object(auth.st, "session_state", browser):
        assert auth.current_user()["id"] == user["id"]
        auth.sign_out()
        assert auth.current_user() is None
    assert user_accounts.user_from_session(token) is None


def test_logout_fails_closed_if_revocation_store_unavailable():
    browser = {"current_user": {"id": "u"}, "session_token": "token"}
    with (patch.object(auth.st, "session_state", browser),
          patch.object(user_accounts, "revoke_session", side_effect=OSError("secret")),
          patch.object(auth.logger, "error", MagicMock())):
        assert auth.sign_out() is False
    assert browser["session_token"] == "token"
    assert browser["auth_logout_error"] is True
