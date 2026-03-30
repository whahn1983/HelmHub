# HelmHub Security Review (March 30, 2026)

## Scope and method

This review covered the Flask application code paths for authentication, session handling, CSRF, authorization checks, API routes, settings/2FA workflows, and baseline operational hardening.

Reviewed areas include:
- `app/config.py`
- `app/__init__.py`
- `app/extensions.py`
- `app/routes/auth.py`
- `app/routes/api.py`
- `app/routes/tasks.py`, `notes.py`, `reminders.py`, `events.py`, `bookmarks.py`, `focus.py`, `settings.py`
- `app/models/user.py`
- `requirements.txt`

---

## Executive summary

The project has strong fundamentals (bcrypt password hashing, broad `login_required` usage, per-object ownership checks in CRUD routes, and 2FA support). The highest-priority improvements are:

1. **Eliminate weak default secret usage** (`SECRET_KEY` fallback) in non-test environments.
2. **Fix open redirect handling** for `next` parameters in login/TOTP flow.
3. **Reconsider CSRF exemption on the full API blueprint** while the app still uses cookie/session auth.
4. **Add baseline response security headers** (CSP/HSTS/X-Frame-Options/etc.).

---

## Detailed findings

### 1) Insecure default `SECRET_KEY` fallback (High)

**What I found**
- `BaseConfig.SECRET_KEY` falls back to a predictable hard-coded value when no env var is provided.

**Why it matters**
- Flask session integrity relies on `SECRET_KEY`; a known key can enable cookie/session tampering and token forgery.

**Where**
- `app/config.py` (`SECRET_KEY` default).

**Recommendation**
- In production (and ideally development), fail fast at startup when `SECRET_KEY` is absent.
- Keep permissive defaults only in `TestingConfig`.

---

### 2) Open redirect via `next` parameter (High)

**What I found**
- Post-login redirects accept `next` if it starts with `/`.
- Values like `//evil.example` also start with `/` and are interpreted by browsers as protocol-relative external URLs.

**Why it matters**
- Enables phishing/open-redirect chains after valid authentication.

**Where**
- `app/routes/auth.py` in both `login()` and `totp()` flows.

**Recommendation**
- Validate redirect targets with a strict allowlist (e.g., relative paths that start with exactly one `/`, not `//`, and optionally resolved with `urlparse` against the app host).
- Prefer a shared helper like `is_safe_redirect_target()`.

---

### 3) API blueprint is CSRF-exempt while using cookie auth (Medium–High)

**What I found**
- Entire `/api` blueprint is exempted from CSRF protection.
- API endpoints perform state changes (`POST /api/quick-capture`) and are authenticated via session cookies (`login_required`).

**Why it matters**
- Cookie-authenticated endpoints without CSRF protections are vulnerable in some cross-site request scenarios.
- SameSite=Lax reduces risk but is not a complete replacement for CSRF protections.

**Where**
- `app/routes/api.py` (`csrf.exempt(api_bp)`).

**Recommendation**
- Split API into:
  - session/browser routes protected by CSRF, and
  - token-based API routes exempt from CSRF.
- If keeping cookie auth for API, require CSRF tokens (header or form token) for unsafe methods.

---

### 4) Missing explicit secure response headers (Medium)

**What I found**
- No centralized hardening headers in the app factory (`after_request`), e.g. CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, HSTS.

**Why it matters**
- Increases exposure to clickjacking, MIME-sniffing issues, and broader browser-side attack surface.

**Where**
- `app/__init__.py` (no secure-header middleware observed).

**Recommendation**
- Add `after_request` header policy. Suggested baseline:
  - `X-Frame-Options: DENY` (or CSP `frame-ancestors 'none'`)
  - `X-Content-Type-Options: nosniff`
  - `Referrer-Policy: strict-origin-when-cross-origin`
  - `Content-Security-Policy` tailored to current inline scripts
  - `Strict-Transport-Security` in HTTPS deployments

---

### 5) Rate limit keying may be inaccurate behind proxies (Medium)

**What I found**
- Rate limiting uses `get_remote_address`, but there is no visible `ProxyFix` or trusted proxy configuration.

**Why it matters**
- In reverse-proxy deployments, limits may collapse to the proxy IP or become easier to evade depending on network topology.

**Where**
- `app/extensions.py` limiter setup.
- `app/__init__.py` has no proxy middleware configuration.

**Recommendation**
- Configure trusted proxy handling (`werkzeug.middleware.proxy_fix.ProxyFix`) with explicit hop counts.
- Align limiter keying with trusted client IP extraction strategy.

---

### 6) 2FA secret stored plaintext at rest (Medium)

**What I found**
- `totp_secret` is stored directly in DB text column; code comments acknowledge encryption should be used in production.

**Why it matters**
- DB read access compromises all enrolled 2FA secrets.

**Where**
- `app/models/user.py` (`totp_secret` column + comments).

**Recommendation**
- Encrypt `totp_secret` with application-managed encryption keys (KMS/Vault-backed envelope encryption or an encrypted SQLAlchemy field type).
- Document key rotation and incident response process.

---

### 7) Session cookie secure flag defaults to false (Medium)

**What I found**
- Base config defaults `SESSION_COOKIE_SECURE` to `False` and enables it only in `ProductionConfig`.

**Why it matters**
- Misconfiguration risk: accidental deployment with non-production config can leak session cookies over plaintext HTTP.

**Where**
- `app/config.py`.

**Recommendation**
- Default to secure cookies unless explicitly in local/dev contexts.
- Add startup warning/error if running non-local host with insecure cookies.

---

## Positive controls observed

- Passwords hashed with bcrypt at cost factor 12.
- 2FA implemented with TOTP and one-time recovery codes (hashed and consumed on use).
- Consistent per-resource ownership checks in CRUD routes (task/note/reminder/event/bookmark).
- Most state-changing web forms include CSRF protections via Flask-WTF (outside the exempt API blueprint).
- Login endpoint has explicit rate limiting.

---

## Prioritized remediation plan

### Immediate (this sprint)
1. Remove insecure `SECRET_KEY` fallback and fail fast in non-test environments.
2. Patch open redirect handling for `next` in auth/TOTP.
3. Re-enable CSRF for cookie-authenticated API state-changing routes.

### Near-term (next 1–2 sprints)
4. Add centralized security headers and validate with browser tests.
5. Correct proxy/trusted-IP handling for limiter accuracy.
6. Add tests for redirect validation and CSRF behavior on API POST routes.

### Medium-term
7. Encrypt TOTP secrets at rest with managed keys.
8. Consider API auth separation (session endpoints vs token endpoints).
9. Add dependency vulnerability scanning in CI (`pip-audit`/SCA) with fail thresholds.

---

## Suggested test additions

- `test_auth_rejects_protocol_relative_next_redirect()`
- `test_auth_allows_only_internal_redirect_targets()`
- `test_api_quick_capture_rejects_missing_csrf_for_session_auth()`
- `test_security_headers_present_on_html_and_api_responses()`
- `test_rate_limit_key_respects_proxy_configuration()`
