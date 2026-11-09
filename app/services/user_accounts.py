"""Persistent per-user accounts for the WebUI.

Each person signs up once with email, Google, or GitHub. Their profile, password
hash and API keys live in ``storage/accounts/accounts.json``. A signed session
token (cookie + this device) keeps them signed in so the app does not ask again.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from filelock import FileLock
import jwt
from loguru import logger

from app.utils import utils


SESSION_COOKIE_NAME = "mpt_session"
SESSION_DAYS = 90
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 256
EMAIL_MAX_LENGTH = 254
NAME_MAX_LENGTH = 80
PBKDF2_ITERATIONS = 210_000
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_lock = threading.RLock()
_password_hasher = PasswordHasher()  # Argon2id with argon2-cffi's recommended defaults.
# Used only to equalize the expensive path for unknown/passwordless accounts.
_dummy_password_hash = _password_hasher.hash(secrets.token_urlsafe(32))


class UserAccountError(ValueError):
    """Safe, user-facing account error. ``code`` is a translation key."""

    def __init__(self, code, **params):
        self.code = code
        self.params = params
        super().__init__(code)


def accounts_dir() -> Path:
    override = os.environ.get("MPT_ACCOUNTS_DIR", "").strip()
    if override:
        path = Path(override).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path
    return Path(utils.storage_dir("accounts", create=True))


def accounts_file() -> Path:
    return accounts_dir() / "accounts.json"


def normalize_email(email) -> str:
    return str(email or "").strip().lower()


def validate_email(email) -> str:
    value = normalize_email(email)
    if not value or len(value) > EMAIL_MAX_LENGTH or not _EMAIL_PATTERN.match(value):
        raise UserAccountError("Auth Invalid Email")
    return value


def validate_password(password) -> str:
    value = str(password or "")
    if len(value) < PASSWORD_MIN_LENGTH:
        raise UserAccountError("Auth Password Too Short")
    if len(value) > PASSWORD_MAX_LENGTH:
        raise UserAccountError("Auth Password Too Long")
    if "\x00" in value:
        raise UserAccountError("Auth Password Too Short")
    return value


def validate_display_name(name, email="") -> str:
    value = str(name or "").strip()
    if not value:
        local = normalize_email(email).split("@", 1)[0]
        value = local or "User"
    return value[:NAME_MAX_LENGTH]


def _utc_now():
    return datetime.now(timezone.utc)


def _iso(moment=None) -> str:
    return (moment or _utc_now()).replace(microsecond=0).isoformat()


def _hash_password(password: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt_hex),
        PBKDF2_ITERATIONS,
    ).hex()


def _new_password_record(password: str) -> tuple[str, str]:
    # Argon2's encoded hash includes its random salt and parameters.
    return "", _password_hasher.hash(password)


def _password_matches(password: str, salt_hex: str, expected_hex: str) -> bool:
    if expected_hex.startswith("$argon2id$"):
        try:
            return _password_hasher.verify(expected_hex, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
    if not salt_hex or not expected_hex:
        return False
    try:
        actual = _hash_password(password, salt_hex)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected_hex)


def _empty_store() -> dict:
    return {"secret": secrets.token_urlsafe(48), "users": []}


def _load_store_unlocked() -> dict:
    path = accounts_file()
    if not path.is_file():
        return _empty_store()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        logger.warning(f"failed to read accounts file: {exc}")
        raise UserAccountError("Auth Storage Error") from exc
    if not isinstance(payload, dict):
        raise UserAccountError("Auth Storage Error")
    users = payload.get("users")
    secret = payload.get("secret")
    # An existing malformed store is not an empty installation. Silently
    # replacing its users or signing secret on the next write loses accounts
    # and invalidates sessions. Stop before modifying the original file.
    if (not isinstance(users, list) or any(not isinstance(user, dict) for user in users)
            or not isinstance(secret, str) or not secret.strip()):
        logger.error("account store is invalid; refusing to overwrite it")
        raise UserAccountError("Auth Storage Error")
    store = {"secret": secret, "users": users}
    pending = payload.get("oauth_pending")
    if isinstance(pending, dict):
        store["oauth_pending"] = pending
    return store


def _save_store_unlocked(store: dict) -> None:
    path = accounts_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(store, ensure_ascii=False, indent=2)
    fd, temp_path = tempfile.mkstemp(
        prefix=".accounts-", suffix=".json.tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def _read_store() -> dict:
    lock_path = str(accounts_file()) + ".lock"
    with _lock, FileLock(lock_path, timeout=10):
        return _load_store_unlocked()


def _with_store(mutator):
    lock_path = str(accounts_file()) + ".lock"
    with _lock, FileLock(lock_path, timeout=10):
        store = _load_store_unlocked()
        result = mutator(store)
        _save_store_unlocked(store)
        return result


def _find_user(store, *, user_id=None, email=None, google_id=None, github_id=None):
    email = normalize_email(email) if email else ""
    google_id = str(google_id or "").strip()
    github_id = str(github_id or "").strip()
    user_id = str(user_id or "").strip()
    for user in store["users"]:
        if not isinstance(user, dict):
            continue
        if user_id and str(user.get("id") or "") == user_id:
            return user
        if email and normalize_email(user.get("email")) == email:
            return user
        if google_id and str(user.get("google_id") or "") == google_id:
            return user
        if github_id and str(user.get("github_id") or "") == github_id:
            return user
    return None


def public_user(record) -> dict:
    """Return the fields that may enter Streamlit session state or the UI."""
    if not isinstance(record, dict):
        return {}
    providers = []
    if record.get("password_hash"):
        providers.append("email")
    if record.get("google_id"):
        providers.append("google")
    if record.get("github_id"):
        providers.append("github")
    return {
        "id": str(record.get("id") or ""),
        "email": normalize_email(record.get("email")),
        "display_name": str(record.get("display_name") or "").strip(),
        "avatar_url": str(record.get("avatar_url") or "").strip(),
        "google_id": str(record.get("google_id") or "").strip(),
        "github_id": str(record.get("github_id") or "").strip(),
        "github_login": str(record.get("github_login") or "").strip(),
        "created_at": str(record.get("created_at") or ""),
        "last_login_at": str(record.get("last_login_at") or ""),
        "auth_providers": providers,
    }


def device_account_payload(record) -> dict:
    """Only public profile fields may leave the account store."""
    return public_user(record)


def _new_user_id() -> str:
    return "usr_" + secrets.token_hex(16)


def _touch(user: dict) -> None:
    user["last_login_at"] = _iso()


def _issue_token(store: dict, user_id: str) -> str:
    """Issue a unique, revocable session; only its digest is stored server-side."""
    record = _find_user(store, user_id=user_id)
    if record is None:
        raise UserAccountError("Auth Login Failed")
    now = _utc_now()
    expires = now + timedelta(days=SESSION_DAYS)
    token = jwt.encode(
        {"uid": user_id, "exp": expires, "iat": now, "jti": secrets.token_urlsafe(32)},
        store["secret"], algorithm="HS256",
    )
    sessions = record.setdefault("sessions", {})
    if not isinstance(sessions, dict):
        raise UserAccountError("Auth Storage Error")
    # Prune expired entries during issuance to avoid unbounded accumulation.
    now_timestamp = int(now.timestamp())
    for digest, expiry in list(sessions.items()):
        if not isinstance(expiry, int) or expiry <= now_timestamp:
            sessions.pop(digest, None)
    sessions[hashlib.sha256(token.encode("utf-8")).hexdigest()] = int(expires.timestamp())
    return token


def create_email_user(email, password, display_name="") -> tuple[dict, str]:
    """Create an email/password account and return (public user, session token)."""
    email = validate_email(email)
    password = validate_password(password)
    display_name = validate_display_name(display_name, email)

    def mutate(store):
        if _find_user(store, email=email):
            raise UserAccountError("Auth Email Taken")
        salt, password_hash = _new_password_record(password)
        record = {
            "id": _new_user_id(),
            "email": email,
            "display_name": display_name,
            "avatar_url": "",
            "google_id": "",
            "github_id": "",
            "github_login": "",
            "password_salt": salt,
            "password_hash": password_hash,
            "created_at": _iso(),
            "last_login_at": _iso(),
            "keys": {},
        }
        store["users"].append(record)
        token = _issue_token(store, record["id"])
        return public_user(record), token, device_account_payload(record)

    return _with_store(mutate)


def authenticate_email(email, password) -> tuple[dict, str]:
    """Verify email/password and return (public user, session token)."""
    email = validate_email(email)
    password = str(password or "")

    def mutate(store):
        record = _find_user(store, email=email)
        if record is None or not record.get("password_hash"):
            _password_matches(password, "", _dummy_password_hash)
            raise UserAccountError("Auth Login Failed")
        if not _password_matches(
            password,
            str(record.get("password_salt") or ""),
            str(record.get("password_hash") or ""),
        ):
            raise UserAccountError("Auth Login Failed")
        encoded = str(record.get("password_hash") or "")
        if not encoded.startswith("$argon2id$") or _password_hasher.check_needs_rehash(encoded):
            record["password_salt"], record["password_hash"] = _new_password_record(password)
        _touch(record)
        token = _issue_token(store, record["id"])
        return public_user(record), token, device_account_payload(record)

    return _with_store(mutate)


def upsert_oauth_user(
    provider,
    provider_id,
    email,
    display_name="",
    avatar_url="",
    github_login="",
) -> tuple[dict, str]:
    """Sign in a known provider identity or create a new, non-conflicting account.

    Linking an identity requires a separate authenticated consent flow; matching
    email addresses alone do not prove ownership of an existing local account.
    """
    provider = str(provider or "").strip().lower()
    provider_id = str(provider_id or "").strip()
    if provider not in {"google", "github"} or not provider_id:
        raise UserAccountError("Auth OAuth Failed")
    email = validate_email(email)
    display_name = validate_display_name(display_name, email)
    avatar_url = str(avatar_url or "").strip()
    github_login = str(github_login or "").strip()
    id_field = "google_id" if provider == "google" else "github_id"

    def mutate(store):
        record = _find_user(store, **{id_field: provider_id})
        email_owner = _find_user(store, email=email)
        if email_owner is not None and email_owner is not record:
            raise UserAccountError("Auth OAuth Failed")
        if record is None:
            record = {
                "id": _new_user_id(),
                "email": email,
                "display_name": display_name,
                "avatar_url": avatar_url,
                "google_id": "",
                "github_id": "",
                "github_login": "",
                "password_salt": "",
                "password_hash": "",
                "created_at": _iso(),
                "last_login_at": _iso(),
                "keys": {},
            }
            store["users"].append(record)
        record[id_field] = provider_id
        if provider == "github" and github_login:
            record["github_login"] = github_login
        if email and not record.get("email"):
            record["email"] = email
        elif email:
            record["email"] = email
        if display_name:
            record["display_name"] = display_name
        if avatar_url:
            record["avatar_url"] = avatar_url
        _touch(record)
        token = _issue_token(store, record["id"])
        return public_user(record), token, device_account_payload(record)

    return _with_store(mutate)


def restore_device_account(payload) -> None:
    """Untrusted browser data cannot create accounts or grant sessions."""
    return None


def _session_record(store: dict, token: str) -> dict | None:
    if not token:
        return None
    try:
        claims = jwt.decode(token, store["secret"], algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    user_id = str(claims.get("uid") or "").strip()
    record = _find_user(store, user_id=user_id)
    if record is None or not isinstance(claims.get("jti"), str):
        return None
    sessions = record.get("sessions")
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if not isinstance(sessions, dict) or digest not in sessions:
        return None
    return record


def user_from_session(token: str) -> dict | None:
    token = str(token or "").strip()
    if not token:
        return None
    record = _session_record(_read_store(), token)
    return public_user(record) if record else None


def update_display_name(token: str, name: str) -> dict:
    """Update only the profile belonging to this currently valid session."""
    token = str(token or "").strip()
    name = str(name or "").strip()
    if not name or len(name) > NAME_MAX_LENGTH:
        raise UserAccountError("Auth Invalid Name")

    def mutate(store):
        record = _session_record(store, token)
        if not record:
            raise UserAccountError("Auth Login Failed")
        record["display_name"] = name
        record["updated_at"] = _iso()
        return public_user(record)

    return _with_store(mutate)


def revoke_session(token: str) -> bool:
    """Revoke one session, leaving other devices signed in."""
    token = str(token or "").strip()
    if not token:
        return False

    def mutate(store):
        try:
            claims = jwt.decode(token, store["secret"], algorithms=["HS256"])
        except jwt.PyJWTError:
            return False
        record = _find_user(store, user_id=claims.get("uid"))
        sessions = record.get("sessions") if record else None
        if not isinstance(sessions, dict):
            return False
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return sessions.pop(digest, None) is not None

    return _with_store(mutate)


def get_user(user_id: str) -> dict | None:
    record = _find_user(_read_store(), user_id=user_id)
    return public_user(record) if record else None


def get_user_keys(user_id: str) -> dict:
    record = _find_user(_read_store(), user_id=user_id)
    keys = record.get("keys") if record else None
    return dict(keys) if isinstance(keys, dict) else {}


def update_user_keys(user_id: str, keys: dict) -> None:
    if not isinstance(keys, dict):
        return

    # The WebUI checks this on each rerun. Keep the read/compare/write under
    # the same inter-process lock, but do not fsync and replace the account
    # file when credentials are unchanged or the account is absent.
    lock_path = str(accounts_file()) + ".lock"
    with _lock, FileLock(lock_path, timeout=10):
        store = _load_store_unlocked()
        record = _find_user(store, user_id=user_id)
        if record is None or record.get("keys") == keys:
            return
        record["keys"] = keys
        _save_store_unlocked(store)


def get_device_account(user_id: str) -> dict | None:
    record = _find_user(_read_store(), user_id=user_id)
    return device_account_payload(record) if record else None


def save_oauth_pending(state, provider, verifier, redirect_uri) -> None:
    """Remember PKCE state so a phone returning from Google/GitHub can finish login."""
    state = str(state or "").strip()
    provider = str(provider or "").strip()
    if not state or provider not in {"google", "github"}:
        return

    def mutate(store):
        pending = store.setdefault("oauth_pending", {})
        if not isinstance(pending, dict):
            pending = {}
            store["oauth_pending"] = pending
        now = _utc_now()
        expired = []
        for key, item in list(pending.items()):
            created = str((item or {}).get("created_at") or "")
            try:
                created_at = datetime.fromisoformat(created)
            except ValueError:
                expired.append(key)
                continue
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            if now - created_at > timedelta(minutes=20):
                expired.append(key)
        for key in expired:
            pending.pop(key, None)
        pending[state] = {
            "provider": provider,
            "verifier": str(verifier or ""),
            "redirect_uri": str(redirect_uri or ""),
            "created_at": _iso(now),
        }
        return True

    _with_store(mutate)


def pop_oauth_pending(state) -> dict | None:
    state = str(state or "").strip()
    if not state:
        return None

    def mutate(store):
        pending = store.get("oauth_pending")
        if not isinstance(pending, dict) or state not in pending:
            return None
        item = pending.pop(state, None)
        if not isinstance(item, dict):
            return None
        try:
            created = datetime.fromisoformat(str(item.get("created_at") or ""))
            if created.tzinfo is None:
                return None
            age = _utc_now() - created
            if age < timedelta(0) or age > timedelta(minutes=20):
                return None
        except (ValueError, TypeError, OverflowError):
            return None
        return item

    return _with_store(mutate)


def issue_session(user_id: str) -> str | None:
    def mutate(store):
        record = _find_user(store, user_id=user_id)
        if record is None:
            return None
        _touch(record)
        return _issue_token(store, record["id"])

    return _with_store(mutate)
