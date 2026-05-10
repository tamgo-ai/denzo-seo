"""
OAuth blueprint — Google sign-in flow for connecting tenant-owned GBP and GSC
accounts to a Denzo tenant. Reuses google_oauth helper for token exchange.

Routes:
  GET  /oauth/google/connect/<tenant_id>?provider=gbp|gsc&next=<url>
       Redirects the user to Google's consent screen.
  GET  /oauth/google/callback?code=...&state=...
       Google redirects here after consent. Exchanges code, persists token,
       fetches userinfo, fetches list of accessible properties/locations.
  POST /oauth/google/disconnect/<tenant_id>/<provider>
       Deletes the token row.
  POST /oauth/google/select/<tenant_id>/<provider>
       Bind the token to a specific GSC site_url or GBP location_id.

CSRF defense: every authorize URL embeds a per-session nonce in `state`.
The callback rejects any state whose nonce does not match the one stored
in the user's session. Combined with can_access_tenant() that's defense
in depth: even an admin can't be tricked into connecting their Google
account to a tenant via a forged URL.
"""
import logging
import secrets
from urllib.parse import urlparse

from flask import Blueprint, request, redirect, url_for, flash, jsonify, abort, session

from denzo.auth import login_required, can_access_tenant
from denzo.agents.utils import google_oauth
from denzo.agents.utils.google_oauth import OAuthError

logger = logging.getLogger(__name__)
bp = Blueprint("oauth", __name__, url_prefix="/oauth/google")


@bp.route("/connect/<tenant_id>")
@login_required
def connect(tenant_id):
    if not can_access_tenant(tenant_id):
        abort(403)

    provider = request.args.get("provider", "").strip().lower()
    if provider not in google_oauth.PROVIDER_SCOPES:
        flash(f"Unknown provider: {provider!r}", "error")
        return redirect(url_for("clients.edit_client", tenant_id=tenant_id))

    if not google_oauth.credentials_configured():
        flash(
            "Google OAuth is not configured on the server. "
            "An admin must set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.",
            "error",
        )
        return redirect(url_for("clients.edit_client", tenant_id=tenant_id))

    next_url = request.args.get("next") or url_for("clients.edit_client", tenant_id=tenant_id)

    # Generate a fresh per-flow nonce, stash in session, embed in state.
    # The callback compares state.nonce to session_oauth_nonce — mismatched
    # → reject. Stops CSRF where an attacker tricks a logged-in admin into
    # following a connect URL for a tenant the attacker controls.
    nonce = secrets.token_urlsafe(24)
    session["oauth_nonce"] = nonce

    try:
        auth_url = google_oauth.build_authorize_url(
            tenant_id, provider, next_url=next_url, nonce=nonce,
        )
    except OAuthError as e:
        flash(f"Could not build Google authorization URL: {e}", "error")
        return redirect(next_url)
    return redirect(auth_url)


@bp.route("/callback")
def callback():
    """Google redirects here after consent. No login_required — Google may not
    carry our session cookie depending on browser/SameSite behavior — we trust
    the state parameter (signed via tenant access check below)."""
    error = request.args.get("error")
    if error:
        return _render_callback_error(f"Google rejected the connection: {error}")

    code  = request.args.get("code")
    state = request.args.get("state", "")
    if not code or not state:
        return _render_callback_error("Missing code or state from Google.")

    try:
        st = google_oauth.parse_state(state)
    except OAuthError as e:
        return _render_callback_error(str(e))

    tenant_id = st.get("tenant_id")
    provider  = st.get("provider")
    next_url  = st.get("next") or "/"
    nonce     = st.get("nonce") or ""

    if not tenant_id or not provider:
        return _render_callback_error("Invalid state payload.")

    # ── CSRF defense: state.nonce must match the one we stashed at /connect ──
    expected_nonce = session.pop("oauth_nonce", None)
    if not nonce or nonce != expected_nonce:
        return _render_callback_error(
            "Security check failed: this connection request didn't originate "
            "from your browser. Please start the connect flow again from Settings."
        )

    # Tenant access check — even though nonce already proves the flow started
    # in this session, verify the session user still has access to the tenant
    # they originally requested.
    if not can_access_tenant(tenant_id):
        return _render_callback_error(
            "You do not have access to this tenant. Log in with the correct account "
            "and reconnect."
        )

    # Exchange code → token
    try:
        token_payload = google_oauth.exchange_code_for_token(code)
    except OAuthError as e:
        return _render_callback_error(f"Token exchange failed: {e}")

    # Get the user's Google email so we can show "Connected as ..." in settings
    account_email = ""
    try:
        userinfo = google_oauth.fetch_userinfo(token_payload.get("access_token", ""))
        account_email = userinfo.get("email", "")
    except Exception:
        pass

    google_oauth.save_token(tenant_id, provider, token_payload, account_email=account_email)

    # If GSC and only one verified site, bind it automatically.
    if provider == "gsc":
        try:
            from denzo.agents.utils.gsc_client import list_sites
            sites = list_sites(tenant_id)
            verified = [s for s in sites if s.get("permissionLevel") in (
                "siteOwner", "siteFullUser", "siteRestrictedUser"
            )]
            if len(verified) == 1:
                google_oauth.update_token_metadata(
                    tenant_id, "gsc",
                    site_url=verified[0].get("siteUrl"),
                )
        except Exception as e:
            logger.warning("GSC auto-bind failed: %s", e)

    flash(f"Google {provider.upper()} connected as {account_email or 'unknown account'}.", "success")
    return redirect(next_url)


@bp.route("/disconnect/<tenant_id>/<provider>", methods=["POST"])
@login_required
def disconnect(tenant_id, provider):
    if not can_access_tenant(tenant_id):
        abort(403)
    if provider not in google_oauth.PROVIDER_SCOPES:
        return jsonify({"error": "unknown provider"}), 400
    google_oauth.delete_token(tenant_id, provider)
    return jsonify({"status": "disconnected", "provider": provider})


@bp.route("/select/<tenant_id>/<provider>", methods=["POST"])
@login_required
def select_property(tenant_id, provider):
    """Bind the connected token to a specific GSC site_url or GBP location_id."""
    if not can_access_tenant(tenant_id):
        abort(403)
    if provider not in google_oauth.PROVIDER_SCOPES:
        return jsonify({"error": "unknown provider"}), 400

    payload = request.get_json(silent=True) or {}
    site_url    = payload.get("site_url")
    location_id = payload.get("location_id")
    account_id  = payload.get("account_id")

    google_oauth.update_token_metadata(
        tenant_id, provider,
        site_url=site_url, location_id=location_id, account_id=account_id,
    )
    return jsonify({"status": "ok"})


@bp.route("/status/<tenant_id>")
@login_required
def status(tenant_id):
    """JSON endpoint used by the Settings UI to render connection state."""
    if not can_access_tenant(tenant_id):
        abort(403)
    out = {"configured": google_oauth.credentials_configured(), "providers": {}}
    for provider in google_oauth.PROVIDER_SCOPES.keys():
        row = google_oauth.get_token_row(tenant_id, provider)
        if row:
            out["providers"][provider] = {
                "connected":     True,
                "account_email": row.get("account_email") or "",
                "site_url":      row.get("site_url") or "",
                "location_id":   row.get("location_id") or "",
                "expires_at":    row.get("expires_at") or "",
                "updated_at":    row.get("updated_at") or "",
            }
        else:
            out["providers"][provider] = {"connected": False}
    return jsonify(out)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _render_callback_error(msg: str):
    """Minimal error page so a failing callback doesn't 500."""
    safe = (msg or "Unknown error.").replace("<", "&lt;").replace(">", "&gt;")
    return (
        "<!doctype html><meta charset='utf-8'>"
        "<title>OAuth Error · Denzo</title>"
        "<style>body{font-family:system-ui;background:#0a0a0a;color:#fff;"
        "padding:48px;max-width:640px;margin:0 auto}"
        "h1{color:#f87171}a{color:#818cf8}</style>"
        f"<h1>OAuth Connection Failed</h1>"
        f"<p>{safe}</p>"
        "<p><a href='/'>← Back to Denzo</a></p>",
        400,
    )
