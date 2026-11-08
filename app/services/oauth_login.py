"""Google and GitHub OAuth helpers for WebUI sign-in.

Client IDs come from the host environment or config.toml. These functions never
log tokens. The WebUI supplies the redirect URI for the current public origin.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from urllib.parse import urlencode

import requests

from app.config import config
from app.services.user_accounts import UserAccountError


GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
GITHUB_AUTH_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_EMAILS_URL = "https://api.github.com/user/emails"
OAUTH_TIMEOUT = 20


def _first_env(*names):
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def google_client_id() -> str:
    return _first_env("GOOGLE_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_ID") or str(
        config.app.get("google_oauth_client_id", "") or ""
    ).strip()


def google_client_secret() -> str:
    return _first_env("GOOGLE_CLIENT_SECRET", "GOOGLE_OAUTH_CLIENT_SECRET") or str(
        config.app.get("google_oauth_client_secret", "") or ""
    ).strip()


def github_client_id() -> str:
    return _first_env("GITHUB_CLIENT_ID", "GITHUB_OAUTH_CLIENT_ID") or str(
        config.app.get("github_oauth_client_id", "") or ""
    ).strip()


def github_client_secret() -> str:
    return _first_env("GITHUB_CLIENT_SECRET", "GITHUB_OAUTH_CLIENT_SECRET") or str(
        config.app.get("github_oauth_client_secret", "") or ""
    ).strip()


def google_configured() -> bool:
    return bool(google_client_id() and google_client_secret())


def github_configured() -> bool:
    return bool(github_client_id() and github_client_secret())


def new_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .decode("ascii")
        .rstrip("=")
    )
    return verifier, challenge


def new_oauth_state() -> str:
    return secrets.token_urlsafe(24)


def google_authorize_url(redirect_uri, state, code_challenge) -> str:
    params = {
        "client_id": google_client_id(),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "access_type": "online",
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def github_authorize_url(redirect_uri, state) -> str:
    params = {
        "client_id": github_client_id(),
        "redirect_uri": redirect_uri,
        "scope": "read:user user:email",
        "state": state,
        "allow_signup": "true",
    }
    return f"{GITHUB_AUTH_URL}?{urlencode(params)}"


def complete_google_login(code, redirect_uri, code_verifier) -> dict:
    if not google_configured():
        raise UserAccountError("Auth Google Not Configured")
    try:
        token_response = requests.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": google_client_id(),
                "client_secret": google_client_secret(),
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            },
            timeout=OAUTH_TIMEOUT,
        )
        token_response.raise_for_status()
        access_token = str(token_response.json().get("access_token") or "").strip()
        if not access_token:
            raise UserAccountError("Auth OAuth Failed")
        profile_response = requests.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=OAUTH_TIMEOUT,
        )
        profile_response.raise_for_status()
        profile = profile_response.json()
    except UserAccountError:
        raise
    except (requests.RequestException, ValueError, TypeError) as exc:
        raise UserAccountError("Auth OAuth Failed") from exc
    if profile.get("email_verified") not in {True, "true", "True"}:
        raise UserAccountError("Auth OAuth Failed")
    return {
        "provider": "google",
        "provider_id": str(profile.get("sub") or "").strip(),
        "email": str(profile.get("email") or "").strip(),
        "display_name": str(profile.get("name") or "").strip(),
        "avatar_url": str(profile.get("picture") or "").strip(),
    }


def complete_github_login(code, redirect_uri) -> dict:
    if not github_configured():
        raise UserAccountError("Auth GitHub Not Configured")
    try:
        token_response = requests.post(
            GITHUB_TOKEN_URL,
            data={
                "client_id": github_client_id(),
                "client_secret": github_client_secret(),
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
            timeout=OAUTH_TIMEOUT,
        )
        token_response.raise_for_status()
        access_token = str(token_response.json().get("access_token") or "").strip()
        if not access_token:
            raise UserAccountError("Auth OAuth Failed")
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        profile_response = requests.get(
            GITHUB_USER_URL, headers=headers, timeout=OAUTH_TIMEOUT
        )
        profile_response.raise_for_status()
        profile = profile_response.json()
        emails_response = requests.get(
            GITHUB_EMAILS_URL, headers=headers, timeout=OAUTH_TIMEOUT
        )
        emails_response.raise_for_status()
        emails = emails_response.json()
    except UserAccountError:
        raise
    except (requests.RequestException, ValueError, TypeError) as exc:
        raise UserAccountError("Auth OAuth Failed") from exc
    email = ""
    if isinstance(emails, list):
        verified = [
            item
            for item in emails
            if isinstance(item, dict)
            and item.get("verified")
            and str(item.get("email") or "").strip()
        ]
        primary = next((item for item in verified if item.get("primary")), None)
        chosen = primary or (verified[0] if verified else None)
        if chosen:
            email = str(chosen.get("email") or "").strip()
    if not email:
        email = str(profile.get("email") or "").strip()
    return {
        "provider": "github",
        "provider_id": str(profile.get("id") or "").strip(),
        "email": email,
        "display_name": str(profile.get("name") or profile.get("login") or "").strip(),
        "avatar_url": str(profile.get("avatar_url") or "").strip(),
        "github_login": str(profile.get("login") or "").strip(),
    }
