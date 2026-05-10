"""
Google OAuth 2.0 helper — authorization, token refresh, and authed requests.

Used by GBP Optimizer (Business Profile API), GSC Client (Search Console API),
and any future Google-API agent.

Stateless wrt sessions — credentials are persisted to the oauth_tokens table
keyed by (tenant_id, provider). Each tenant connects their own Google account.
"""
import os
import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta

from denzo.db import get_db


GOOGLE_AUTH_URL  = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Map provider key → list of OAuth scopes requested.
PROVIDER_SCOPES = {
    "gbp": [
        "https://www.googleapis.com/auth/business.manage",
        "openid", "email", "profile",
    ],
    "gsc": [
        "https://www.googleapis.com/auth/webmasters.readonly",
        "openid", "email", "profile",
    ],
}


class OAuthError(Exception):
    pass


# ── Credentials & redirect URI ─────────────────────────────────────────────────

def get_credentials() -> tuple[str, str]:
    cid = os.getenv("GOOGLE_CLIENT_ID")
    cs  = os.getenv("GOOGLE_CLIENT_SECRET")
    if not cid or not cs:
        raise OAuthError(
            "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set in .env "
            "before the OAuth flow can be used."
        )
    return cid, cs


def get_redirect_uri() -> str:
    uri = os.getenv("OAUTH_REDIRECT_URI")
    if uri:
        return uri
    # Fallback for local dev — Google rejects http callbacks for production apps,
    # but this works while the consent screen is in "Testing" mode.
    return "http://localhost:5055/oauth/google/callback"


def credentials_configured() -> bool:
    """Cheap check used by UI to decide whether to show 'Connect' buttons."""
    return bool(os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"))


# ── Token persistence ──────────────────────────────────────────────────────────

def get_token_row(tenant_id: str, provider: str) -> dict | None:
    db = get_db()
    row = db.execute(
        "SELECT * FROM oauth_tokens WHERE tenant_id=? AND provider=?",
        (tenant_id, provider),
    ).fetchone()
    db.close()
    return dict(row) if row else None


def save_token(
    tenant_id: str,
    provider: str,
    token_payload: dict,
    account_email: str | None = None,
    site_url: str | None = None,
    location_id: str | None = None,
    account_id: str | None = None,
) -> None:
    """Persist or update an OAuth token. token_payload is whatever Google returned."""
    access_token  = token_payload.get("access_token")
    if not access_token:
        raise OAuthError(f"Token payload missing access_token: {token_payload}")
    refresh_token = token_payload.get("refresh_token")
    expires_in    = int(token_payload.get("expires_in", 3600))
    scopes        = token_payload.get("scope", "")
    expires_at    = (datetime.utcnow() + timedelta(seconds=expires_in)).isoformat()

    db = get_db()
    existing = db.execute(
        "SELECT refresh_token, account_email, site_url, location_id, account_id "
        "FROM oauth_tokens WHERE tenant_id=? AND provider=?",
        (tenant_id, provider),
    ).fetchone()

    # Google returns refresh_token only on FIRST consent (or with prompt=consent).
    # If absent on subsequent refreshes, preserve the existing one.
    if existing and not refresh_token:
        refresh_token = existing["refresh_token"]
    if existing and not account_email:
        account_email = existing["account_email"]
    if existing and not site_url:
        site_url = existing["site_url"]
    if existing and not location_id:
        location_id = existing["location_id"]
    if existing and not account_id:
        account_id = existing["account_id"]

    db.execute("""
        INSERT INTO oauth_tokens (tenant_id, provider, access_token, refresh_token,
                                  expires_at, scopes, account_email, account_id,
                                  location_id, site_url, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(tenant_id, provider) DO UPDATE SET
            access_token  = excluded.access_token,
            refresh_token = COALESCE(excluded.refresh_token, oauth_tokens.refresh_token),
            expires_at    = excluded.expires_at,
            scopes        = excluded.scopes,
            account_email = COALESCE(excluded.account_email, oauth_tokens.account_email),
            account_id    = COALESCE(excluded.account_id,    oauth_tokens.account_id),
            location_id   = COALESCE(excluded.location_id,   oauth_tokens.location_id),
            site_url      = COALESCE(excluded.site_url,      oauth_tokens.site_url),
            updated_at    = CURRENT_TIMESTAMP
    """, (tenant_id, provider, access_token, refresh_token, expires_at, scopes,
          account_email, account_id, location_id, site_url))
    db.commit()
    db.close()


def update_token_metadata(
    tenant_id: str,
    provider: str,
    site_url: str | None = None,
    location_id: str | None = None,
    account_id: str | None = None,
) -> None:
    """Update which property/location a connected token is bound to (after user picks one)."""
    db = get_db()
    fields = []
    values: list = []
    if site_url is not None:
        fields.append("site_url=?")
        values.append(site_url)
    if location_id is not None:
        fields.append("location_id=?")
        values.append(location_id)
    if account_id is not None:
        fields.append("account_id=?")
        values.append(account_id)
    if fields:
        fields.append("updated_at=CURRENT_TIMESTAMP")
        values.extend([tenant_id, provider])
        db.execute(
            f"UPDATE oauth_tokens SET {', '.join(fields)} WHERE tenant_id=? AND provider=?",
            tuple(values),
        )
        db.commit()
    db.close()


def delete_token(tenant_id: str, provider: str) -> None:
    db = get_db()
    db.execute(
        "DELETE FROM oauth_tokens WHERE tenant_id=? AND provider=?",
        (tenant_id, provider),
    )
    db.commit()
    db.close()


# ── Token lifecycle (refresh, retrieve) ────────────────────────────────────────

def _refresh_access_token(tenant_id: str, provider: str, refresh_token: str) -> str:
    cid, cs = get_credentials()
    body = urllib.parse.urlencode({
        "client_id":     cid,
        "client_secret": cs,
        "refresh_token": refresh_token,
        "grant_type":    "refresh_token",
    }).encode()
    req = urllib.request.Request(
        GOOGLE_TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise OAuthError(f"Token refresh failed for {provider}: {e.code} {err_body}")

    save_token(tenant_id, provider, payload)
    return payload["access_token"]


def get_access_token(tenant_id: str, provider: str) -> str:
    """Return a valid access_token, refreshing transparently if expired."""
    row = get_token_row(tenant_id, provider)
    if not row:
        raise OAuthError(
            f"No OAuth token for tenant={tenant_id} provider={provider}. "
            "User must connect first via /oauth/google/connect."
        )

    expires_at_str = row.get("expires_at")
    if expires_at_str:
        try:
            expires_at = datetime.fromisoformat(expires_at_str)
            if expires_at - datetime.utcnow() < timedelta(seconds=60):
                if not row.get("refresh_token"):
                    raise OAuthError(
                        f"Token expired and no refresh_token for {provider}. "
                        "User must re-connect."
                    )
                return _refresh_access_token(tenant_id, provider, row["refresh_token"])
        except ValueError:
            pass  # bad date format — fall through and try the access_token as-is

    return row["access_token"]


def is_connected(tenant_id: str, provider: str) -> bool:
    return get_token_row(tenant_id, provider) is not None


# ── Authed API requests ────────────────────────────────────────────────────────

def authed_request(
    tenant_id: str,
    provider: str,
    url: str,
    method: str = "GET",
    body: dict | None = None,
    params: dict | None = None,
    timeout: int = 15,
) -> dict:
    """Make an authenticated Google API request. Returns parsed JSON.

    On 401 (token revoked or scope changed) we attempt one refresh + retry.
    """
    if params:
        sep = "&" if "?" in url else "?"
        url = url + sep + urllib.parse.urlencode(params)

    def _do_request(token: str) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if data:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if not raw:
                return {}
            return json.loads(raw)

    token = get_access_token(tenant_id, provider)
    try:
        return _do_request(token)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            row = get_token_row(tenant_id, provider)
            if row and row.get("refresh_token"):
                fresh_token = _refresh_access_token(tenant_id, provider, row["refresh_token"])
                try:
                    return _do_request(fresh_token)
                except urllib.error.HTTPError as e2:
                    err_body = e2.read().decode("utf-8", errors="replace")
                    raise OAuthError(
                        f"{method} {url} failed after refresh: {e2.code} {err_body}"
                    )
        err_body = e.read().decode("utf-8", errors="replace")
        raise OAuthError(f"{method} {url} failed: {e.code} {err_body}")


# ── Authorization URL & code exchange ──────────────────────────────────────────

def build_authorize_url(tenant_id: str, provider: str, redirect_uri: str | None = None,
                        next_url: str | None = None, nonce: str | None = None) -> str:
    cid, _ = get_credentials()
    redirect = redirect_uri or get_redirect_uri()
    if provider not in PROVIDER_SCOPES:
        raise OAuthError(f"Unknown provider: {provider}")

    state = json.dumps({
        "tenant_id": tenant_id,
        "provider":  provider,
        "next":      next_url or "",
        "nonce":     nonce or "",
    })
    params = {
        "client_id":     cid,
        "redirect_uri":  redirect,
        "response_type": "code",
        "scope":         " ".join(PROVIDER_SCOPES[provider]),
        "access_type":   "offline",   # required for refresh_token
        "prompt":        "consent",   # force re-consent so refresh_token is always returned
        "include_granted_scopes": "true",
        "state":         state,
    }
    return GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode(params)


def parse_state(state: str) -> dict:
    try:
        return json.loads(state)
    except Exception:
        raise OAuthError(f"Invalid state parameter: {state[:64]!r}")


def exchange_code_for_token(code: str, redirect_uri: str | None = None) -> dict:
    cid, cs = get_credentials()
    redirect = redirect_uri or get_redirect_uri()
    body = urllib.parse.urlencode({
        "code":          code,
        "client_id":     cid,
        "client_secret": cs,
        "redirect_uri":  redirect,
        "grant_type":    "authorization_code",
    }).encode()
    req = urllib.request.Request(
        GOOGLE_TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise OAuthError(f"Code exchange failed: {e.code} {err_body}")


def fetch_userinfo(access_token: str) -> dict:
    """Get the user's email/name from Google. Used right after token exchange."""
    req = urllib.request.Request(
        "https://openidconnect.googleapis.com/v1/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError:
        return {}
