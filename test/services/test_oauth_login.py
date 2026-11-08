from unittest.mock import patch

import pytest
import requests

from app.services import oauth_login
from app.services.user_accounts import UserAccountError


def test_pkce_pair_is_url_safe_and_unique():
    first = oauth_login.new_pkce_pair()
    second = oauth_login.new_pkce_pair()
    assert first[0] != second[0]
    assert first[1] != second[1]
    assert "=" not in first[1]


def test_google_authorize_url_includes_pkce(monkeypatch):
    monkeypatch.setattr(oauth_login, "google_client_id", lambda: "google-client")
    url = oauth_login.google_authorize_url(
        "https://app.example.com/", "google.state", "challenge"
    )
    assert "accounts.google.com" in url
    assert "client_id=google-client" in url
    assert "code_challenge=challenge" in url
    assert "redirect_uri=https%3A%2F%2Fapp.example.com%2F" in url


def test_github_authorize_url_asks_for_email(monkeypatch):
    monkeypatch.setattr(oauth_login, "github_client_id", lambda: "gh-client")
    url = oauth_login.github_authorize_url("https://app.example.com/", "github.state")
    assert "github.com/login/oauth/authorize" in url
    assert "scope=read%3Auser+user%3Aemail" in url or "user%3Aemail" in url


def test_complete_google_login_reads_verified_profile(monkeypatch):
    monkeypatch.setattr(oauth_login, "google_configured", lambda: True)
    monkeypatch.setattr(oauth_login, "google_client_id", lambda: "id")
    monkeypatch.setattr(oauth_login, "google_client_secret", lambda: "secret")

    class _Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_post(url, **kwargs):
        assert url == oauth_login.GOOGLE_TOKEN_URL
        assert kwargs["data"]["code_verifier"] == "verifier"
        return _Response({"access_token": "tok"})

    def fake_get(url, **kwargs):
        assert url == oauth_login.GOOGLE_USERINFO_URL
        return _Response(
            {
                "sub": "sub-1",
                "email": "user@gmail.com",
                "email_verified": True,
                "name": "User",
                "picture": "https://example.com/p.png",
            }
        )

    with patch.object(requests, "post", fake_post), patch.object(requests, "get", fake_get):
        profile = oauth_login.complete_google_login(
            "code", "https://app.example.com/", "verifier"
        )
    assert profile["provider"] == "google"
    assert profile["provider_id"] == "sub-1"
    assert profile["email"] == "user@gmail.com"


def test_complete_google_login_rejects_unverified_email(monkeypatch):
    monkeypatch.setattr(oauth_login, "google_configured", lambda: True)
    monkeypatch.setattr(oauth_login, "google_client_id", lambda: "id")
    monkeypatch.setattr(oauth_login, "google_client_secret", lambda: "secret")

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"access_token": "tok"} if "token" in str(self) else {
                "sub": "sub-1",
                "email": "user@gmail.com",
                "email_verified": False,
            }

    token_response = type("R", (), {"raise_for_status": lambda self: None, "json": lambda self: {"access_token": "tok"}})()
    profile_response = type(
        "R",
        (),
        {
            "raise_for_status": lambda self: None,
            "json": lambda self: {
                "sub": "sub-1",
                "email": "user@gmail.com",
                "email_verified": False,
            },
        },
    )()
    with patch.object(requests, "post", return_value=token_response), patch.object(
        requests, "get", return_value=profile_response
    ):
        with pytest.raises(UserAccountError) as exc:
            oauth_login.complete_google_login("code", "https://app.example.com/", "v")
    assert exc.value.code == "Auth OAuth Failed"


def test_complete_github_login_prefers_primary_verified_email(monkeypatch):
    monkeypatch.setattr(oauth_login, "github_configured", lambda: True)
    monkeypatch.setattr(oauth_login, "github_client_id", lambda: "id")
    monkeypatch.setattr(oauth_login, "github_client_secret", lambda: "secret")

    token_response = type(
        "R",
        (),
        {
            "raise_for_status": lambda self: None,
            "json": lambda self: {"access_token": "tok"},
        },
    )()

    def fake_get(url, **kwargs):
        if url == oauth_login.GITHUB_USER_URL:
            return type(
                "R",
                (),
                {
                    "raise_for_status": lambda self: None,
                    "json": lambda self: {
                        "id": 99,
                        "login": "octo",
                        "name": "Octo Cat",
                        "avatar_url": "https://example.com/o.png",
                        "email": None,
                    },
                },
            )()
        return type(
            "R",
            (),
            {
                "raise_for_status": lambda self: None,
                "json": lambda self: [
                    {"email": "other@example.com", "primary": False, "verified": True},
                    {"email": "octo@users.noreply.github.com", "primary": True, "verified": True},
                ],
            },
        )()

    with patch.object(requests, "post", return_value=token_response), patch.object(
        requests, "get", fake_get
    ):
        profile = oauth_login.complete_github_login("code", "https://app.example.com/")
    assert profile["provider"] == "github"
    assert profile["provider_id"] == "99"
    assert profile["email"] == "octo@users.noreply.github.com"
    assert profile["github_login"] == "octo"
