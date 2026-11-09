"""WebUI sign-up and remembered sign-in.

Email/password always works. Google and GitHub are offered when OAuth client
credentials are configured. A session cookie plus the device vault keep the
user signed in so opening the app on a phone does not ask again.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

import streamlit as st
from loguru import logger

from app.services import oauth_login, user_accounts
from app.services.user_accounts import SESSION_COOKIE_NAME, UserAccountError


def auth_gate_enabled() -> bool:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    flag = os.environ.get("MPT_DISABLE_AUTH", "").strip().lower()
    return flag not in {"1", "true", "yes", "on"}


def public_base_url() -> str:
    explicit = (
        os.environ.get("MPT_PUBLIC_URL", "").strip()
        or os.environ.get("OAUTH_REDIRECT_URI", "").strip()
    )
    # Never derive OAuth redirects from untrusted Host/Forwarded headers.
    try:
        parsed = urlsplit(explicit)
        if (not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
            return ""
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        ):
            return ""
    except ValueError:
        return ""
    return explicit.rstrip("/")



def oauth_redirect_uri() -> str:
    base = public_base_url()
    return f"{base}/" if base else ""


def read_session_cookie() -> str:
    try:
        cookies = getattr(st.context, "cookies", None) or {}
        return str(cookies.get(SESSION_COOKIE_NAME) or "").strip()
    except Exception:
        return ""


def current_user():
    user = st.session_state.get("current_user")
    token = st.session_state.get("session_token")
    if not isinstance(user, dict) or not user.get("id") or not token:
        return None
    # A live Streamlit session must not outlive server revocation or expiration.
    verified = user_accounts.user_from_session(token)
    if not verified or verified.get("id") != user.get("id"):
        st.session_state.pop("current_user", None)
        st.session_state.pop("session_token", None)
        return None
    return verified


def remember_session(public_user, token, device_account=None):
    st.session_state["current_user"] = public_user
    st.session_state["session_token"] = token
    if device_account:
        st.session_state["device_account"] = device_account
    st.session_state.pop("signed_out", None)
    st.session_state.pop("auth_error", None)


def sign_out():
    token = str(st.session_state.get("session_token") or "").strip()
    if token:
        try:
            user_accounts.revoke_session(token)
        except Exception as exc:
            # Do not report a successful logout if the session is still valid.
            logger.error("session revocation failed: {}", type(exc).__name__)
            st.session_state["auth_logout_error"] = True
            return False
    st.session_state.pop("current_user", None)
    st.session_state.pop("device_account", None)
    st.session_state.pop("device_key_values_user", None)
    st.session_state.pop("user_keys_applied", None)
    st.session_state["session_token"] = ""
    st.session_state["signed_out"] = True
    st.session_state["clear_session_cookie"] = True
    st.session_state.pop("auth_logout_error", None)
    return True


def _set_auth_error(exc):
    if isinstance(exc, UserAccountError):
        st.session_state["auth_error"] = exc.code
    else:
        logger.warning("sign-in failed: {}", type(exc).__name__)
        st.session_state["auth_error"] = "Auth OAuth Failed"


def _begin_oauth(provider, redirect_uri):
    state = f"{provider}.{oauth_login.new_oauth_state()}"
    verifier, challenge = oauth_login.new_pkce_pair()
    st.session_state[f"oauth_{provider}_state"] = state
    st.session_state[f"oauth_{provider}_verifier"] = verifier
    st.session_state["oauth_redirect_uri"] = redirect_uri
    user_accounts.save_oauth_pending(state, provider, verifier, redirect_uri)
    if provider == "google":
        return oauth_login.google_authorize_url(redirect_uri, state, challenge)
    return oauth_login.github_authorize_url(redirect_uri, state)


def consume_oauth_callback():
    """Complete a Google/GitHub redirect if this page load carries a code."""
    try:
        params = st.query_params
    except Exception:
        return False
    error = str(params.get("error") or "").strip()
    code = str(params.get("code") or "").strip()
    state = str(params.get("state") or "").strip()
    if not error and not code:
        return False
    try:
        st.query_params.clear()
    except Exception:
        pass
    if error:
        st.session_state["auth_error"] = "Auth OAuth Failed"
        return False
    # The state must belong to this browser's initiating Streamlit session,
    # not merely exist in the shared server store (login-CSRF protection).
    provider = next((name for name in ("google", "github")
                     if state and state == st.session_state.get(f"oauth_{name}_state")), "")
    if not provider:
        st.session_state["auth_error"] = "Auth OAuth Failed"
        return False
    pending = user_accounts.pop_oauth_pending(state) or {}
    st.session_state.pop(f"{provider}_auth_url", None)
    if pending.get("provider") != provider:
        st.session_state.pop(f"oauth_{provider}_state", None)
        st.session_state.pop(f"oauth_{provider}_verifier", None)
        st.session_state["auth_error"] = "Auth OAuth Failed"
        return False
    redirect_uri = (
        str(pending.get("redirect_uri") or "")
        or str(st.session_state.get("oauth_redirect_uri") or "")
        or oauth_redirect_uri()
    )
    verifier = str(pending.get("verifier") or "") or str(
        st.session_state.get(f"oauth_{provider}_verifier") or ""
    )
    st.session_state.pop("oauth_google_state", None)
    st.session_state.pop("oauth_github_state", None)
    st.session_state.pop("oauth_google_verifier", None)
    st.session_state.pop("oauth_github_verifier", None)
    st.session_state.pop("oauth_redirect_uri", None)
    if not code or not provider:
        st.session_state["auth_error"] = "Auth OAuth Failed"
        return False
    try:
        if provider == "google":
            profile = oauth_login.complete_google_login(code, redirect_uri, verifier)
        else:
            profile = oauth_login.complete_github_login(code, redirect_uri)
        public, token, device_account = user_accounts.upsert_oauth_user(
            profile["provider"],
            profile["provider_id"],
            profile["email"],
            display_name=profile.get("display_name") or "",
            avatar_url=profile.get("avatar_url") or "",
            github_login=profile.get("github_login") or "",
        )
    except Exception as exc:
        _set_auth_error(exc)
        return False
    remember_session(public, token, device_account)
    return True


def restore_session_from_device(vault):
    """Sign the user in from a cookie or this device's remembered account."""
    if st.session_state.get("signed_out"):
        return False
    if current_user():
        return True
    token = (
        str(st.session_state.get("session_token") or "").strip()
        or read_session_cookie()
        or str((vault or {}).get("session_token") or "").strip()
    )
    if token:
        public = user_accounts.user_from_session(token)
        if public:
            device_account = user_accounts.get_device_account(public["id"])
            remember_session(public, token, device_account)
            return True
    # Browser storage is attacker-controlled. An account object is not proof
    # of identity, even if it contains a password hash or names an existing
    # user. Only a server-verified session may restore a signed-in session.
    return False


def render_auth_screen(tr):
    """Full-page sign-in / create-account form for phones and desktops."""
    redirect_uri = oauth_redirect_uri()
    error_code = st.session_state.pop("auth_error", None)

    st.markdown(
        f"""
        <div class="mpt-auth-hero">
            <h1>MoneyPrinterTurbo</h1>
            <p>{tr("Auth Welcome Help")}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.container(key="auth_screen"):
        if error_code:
            st.error(tr(error_code))

        google_ready = oauth_login.google_configured() and bool(redirect_uri)
        github_ready = oauth_login.github_configured() and bool(redirect_uri)

        google_col, github_col = st.columns(2)
        with google_col:
            if google_ready:
                if "google_auth_url" not in st.session_state:
                    st.session_state["google_auth_url"] = _begin_oauth(
                        "google", redirect_uri
                    )
                st.link_button(
                    tr("Continue with Google"),
                    st.session_state["google_auth_url"],
                    use_container_width=True,
                    type="secondary",
                    icon=":material/account_circle:",
                )
            else:
                if st.button(
                    tr("Continue with Google"),
                    use_container_width=True,
                    key="google_signin_unconfigured",
                    icon=":material/account_circle:",
                ):
                    st.session_state["auth_error"] = (
                        "Auth Google Not Configured"
                        if not oauth_login.google_configured()
                        else "Auth Public Url Required"
                    )
                    st.rerun()
        with github_col:
            if github_ready:
                if "github_auth_url" not in st.session_state:
                    st.session_state["github_auth_url"] = _begin_oauth(
                        "github", redirect_uri
                    )
                st.link_button(
                    tr("Continue with GitHub"),
                    st.session_state["github_auth_url"],
                    use_container_width=True,
                    type="secondary",
                    icon=":material/code:",
                )
            else:
                if st.button(
                    tr("Continue with GitHub"),
                    use_container_width=True,
                    key="github_signin_unconfigured",
                    icon=":material/code:",
                ):
                    st.session_state["auth_error"] = (
                        "Auth GitHub Not Configured"
                        if not oauth_login.github_configured()
                        else "Auth Public Url Required"
                    )
                    st.rerun()

        st.caption(tr("Auth Or Email"))
        mode = st.radio(
            tr("Auth Mode"),
            options=["signin", "signup"],
            format_func=lambda value: tr("Sign In")
            if value == "signin"
            else tr("Create Account"),
            horizontal=True,
            label_visibility="collapsed",
            key="auth_mode",
        )
        signup = mode == "signup"
        with st.form("auth_email_form"):
            email = st.text_input(
                tr("Email Address"),
                type="email",
                key="auth_email",
                autocomplete="email",
            )
            display_name = ""
            if signup:
                display_name = st.text_input(
                    tr("Display Name"),
                    key="auth_display_name",
                    autocomplete="name",
                )
            password = st.text_input(
                tr("Password"),
                type="password",
                key="auth_password",
                autocomplete="new-password" if signup else "current-password",
            )
            confirm = ""
            if signup:
                confirm = st.text_input(
                    tr("Confirm Password"),
                    type="password",
                    key="auth_confirm",
                    autocomplete="new-password",
                )
            submitted = st.form_submit_button(
                tr("Create Account") if signup else tr("Sign In"),
                use_container_width=True,
                type="primary",
            )
        if submitted:
            try:
                if signup:
                    if str(password or "") != str(confirm or ""):
                        raise UserAccountError("Auth Password Mismatch")
                    public, token, device_account = user_accounts.create_email_user(
                        email, password, display_name
                    )
                else:
                    public, token, device_account = user_accounts.authenticate_email(
                        email, password
                    )
            except Exception as exc:
                _set_auth_error(exc)
                st.rerun()
            remember_session(public, token, device_account)
            st.rerun()

        st.caption(tr("Auth Stay Signed In"))
