# Account system: implementation and security status

**Not production-ready for public or multi-user use.** The existing Streamlit
WebUI and FastAPI app were inspected. Account records live in a locked,
atomically replaced JSON file under `storage/accounts`, not a relational
database. Application configuration, API credentials, task history and media
are shared globally. A WebUI login is not an authorization boundary for all
FastAPI routes or the shared generation pipeline. Do not expose this as a
private multi-user SaaS service.

## Implemented in this iteration

- New passwords use Argon2id; successful login transparently upgrades the
  previous PBKDF2 format. New passwords require at least 12 characters;
  existing short passwords still authenticate until a password-change flow
  exists. There is no breach-password screening or strength meter.
- New sessions are unique, expire after 90 days, and are checked against a
  hashed server-side session record. Logout revokes the active token, without
  logging out other devices. Active WebUI sessions are checked on reruns.
  Existing stateless session tokens are rejected, so users must sign in again.
- Public/device account data no longer includes password hashes or salts; the
  unsafe unused account reconstruction from untrusted browser data is disabled.
- OAuth callbacks require state from the initiating Streamlit session and a
  non-expired, single-use server record. Google/GitHub identities are not
  automatically attached to an existing email account without a proper
  authenticated linking flow. GitHub sign-in requires a verified email.
- OAuth requires an explicit `MPT_PUBLIC_URL` (or `OAUTH_REDIRECT_URI`) origin;
  attacker-controlled request headers are not used to build redirects. HTTPS
  is required except for localhost. Configure the corresponding root callback
  URI with the provider. If the browser loses its Streamlit session during an
  OAuth redirect, sign-in fails closed; a robust server-side OAuth session
  mechanism is still needed. OAuth was not tested against live providers.
- A signed-in user can view account details and update their display name. The
  update resolves the owner from the verified session inside the account-store
  lock; it cannot accept an arbitrary target user ID.

## Verification

The local full regression suite passed: 3,231 tests, 162 skipped and 12,939
subtests (7 warnings). A subsequently added OAuth callback regression also
passed in the focused account suite: 36 tests. Ruff, compilation and
`git diff --check` passed. Provider calls in these tests are mocked; this is not
live verification of OAuth, mobile behavior or deployment security.

## Important remaining gaps

- Email verification, reset emails, email change, account deletion, 2FA,
  session/device management, complete profile management, password-strength
  feedback, enumeration protection and distributed brute-force/rate limiting
  are not implemented.
- Browser backup and its JavaScript-written cookie can contain bearer tokens
  and API keys and are **not HttpOnly**. Existing browser copies may contain
  password hashes or keys; this code does not remotely erase old copies. Use
  the browser's site-data controls to remove them. Moving to server-issued
  Secure, HttpOnly, SameSite cookies with CSRF protection requires an HTTP
  authentication backend, not a JavaScript cookie workaround.
- JSON account and global configuration storage can contain plaintext API
  credentials. It is not a secret manager or a multi-user isolation solution.
  Render free-tier storage is ephemeral; use persistent storage and backups.
- A relational database migration, transactions, durable workspace ownership,
  user-scoped task/history/asset storage and authorization on every protected
  API are required before privacy or cross-device persistence can be claimed.
  Existing user data must not be silently attributed to a new account.
- HTTPS termination, secure headers, CSRF protection, operational monitoring,
  email delivery, abuse prevention and independent security review are needed.
  No mobile browser, production deployment or external email/OAuth integration
  was verified in this environment.

Do not place credentials in source control, chat or browser backups. Rotate any
credentials previously exposed and verify host access protection before using
replacement keys. See `docs/production-audit.md` for wider application limits.
