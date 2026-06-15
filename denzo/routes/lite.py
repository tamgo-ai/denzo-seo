"""
Denzo SEO Lite — simplified self-serve UI for small businesses.
Same DB + agents as Enterprise, different frontend at /lite/<tenant_id>/
"""
import json
import logging
from datetime import datetime, timedelta  # timedelta kept for next_run calc, timezone
from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify

logger = logging.getLogger(__name__)
from denzo.auth import tenant_access_required
from denzo.db import get_db

bp = Blueprint("lite", __name__, url_prefix="/lite")


def _get_client_and_ctx(tenant_id):
    db = get_db()
    client = db.execute("SELECT * FROM clients WHERE tenant_id=?", (tenant_id,)).fetchone()
    ctx    = db.execute("SELECT * FROM client_context WHERE tenant_id=?", (tenant_id,)).fetchone()
    return db, client, ctx


def _pending_review_count(db, tenant_id):
    row = db.execute(
        "SELECT COUNT(*) FROM pages WHERE tenant_id=? AND status='draft' AND content IS NOT NULL AND content != ''",
        (tenant_id,)
    ).fetchone()
    return row[0] if row else 0


def _stats(db, tenant_id):
    counts = db.execute("""
        SELECT
            SUM(CASE WHEN status='published' THEN 1 ELSE 0 END) AS published,
            SUM(CASE WHEN status='ready'     THEN 1 ELSE 0 END) AS ready,
            SUM(CASE WHEN status='draft' AND content IS NOT NULL AND content != '' THEN 1 ELSE 0 END) AS draft,
            SUM(CASE WHEN status='pending'   THEN 1 ELSE 0 END) AS pending
        FROM pages WHERE tenant_id=?
    """, (tenant_id,)).fetchone()

    kw_count = db.execute(
        "SELECT COUNT(*) FROM keywords WHERE tenant_id=?", (tenant_id,)
    ).fetchone()[0]

    # GEO citation rate
    geo_total = db.execute(
        "SELECT COUNT(*) FROM geo_queries WHERE tenant_id=?", (tenant_id,)
    ).fetchone()[0]
    geo_mentioned = db.execute(
        "SELECT COUNT(*) FROM geo_queries WHERE tenant_id=? AND client_mentioned=1", (tenant_id,)
    ).fetchone()[0]
    geo_rate = round((geo_mentioned / geo_total * 100)) if geo_total > 0 else 0

    return {
        "published":    counts["published"] or 0,
        "ready":        counts["ready"] or 0,
        "draft":        counts["draft"] or 0,
        "pending":      counts["pending"] or 0,
        "keywords":     kw_count,
        "geo_citations": geo_rate,
    }



def _autopilot_on(db, tenant_id):
    row = db.execute(
        "SELECT value FROM settings WHERE tenant_id=? AND key='autopilot'", (tenant_id,)
    ).fetchone()
    if row:
        try:
            return json.loads(row["value"]).get("enabled", False)
        except Exception as e:
            logger.warning("Error: %s", e)
    return False


# ── Index: redirect to first client or onboarding ────────────────────────────
@bp.route("/")
@tenant_access_required
def index():
    from flask import session as _sess
    db = get_db()
    if _sess.get("role") == "admin":
        client = db.execute("SELECT tenant_id FROM clients ORDER BY name LIMIT 1").fetchone()
    else:
        client = db.execute(
            "SELECT tenant_id FROM clients WHERE owner_user_id=? ORDER BY name LIMIT 1",
            (_sess.get("user_id"),)
        ).fetchone()
    db.close()
    if client:
        return redirect(url_for("lite.dashboard", tenant_id=client["tenant_id"]))
    return redirect(url_for("clients.new_client"))


# ── Dashboard ─────────────────────────────────────────────────────────────────
@bp.route("/<tenant_id>/")
@bp.route("/<tenant_id>/dashboard")
@tenant_access_required
def dashboard(tenant_id):
    db, client, ctx = _get_client_and_ctx(tenant_id)
    if not client:
        db.close()
        flash("Client not found.", "error")
        return redirect(url_for("clients.list_clients"))

    stats = _stats(db, tenant_id)
    autopilot_on = _autopilot_on(db, tenant_id)
    pending_review_count = _pending_review_count(db, tenant_id)

    active_agent = db.execute(
        "SELECT name, current_task FROM agents WHERE tenant_id=? AND status='working' LIMIT 1",
        (tenant_id,)
    ).fetchone()

    # Recent drafts with content — the review queue
    recent_drafts_rows = db.execute(
        "SELECT * FROM pages WHERE tenant_id=? AND status='draft' AND content IS NOT NULL AND content != '' ORDER BY created_at DESC LIMIT 3",
        (tenant_id,)
    ).fetchall()
    recent_drafts = [dict(p) for p in recent_drafts_rows]

    # Next run estimate
    if autopilot_on:
        now = datetime.now(timezone.utc)
        next_9am = now.replace(hour=9, minute=0, second=0, microsecond=0)
        if now >= next_9am:
            next_9am += timedelta(days=1)
        next_run = next_9am.strftime("%a %I:%M %p UTC")
    else:
        next_run = None

    db.close()
    return render_template(
        "lite/dashboard.html",
        client=dict(client),
        tenant_id=tenant_id,
        active_page="dashboard",
        stats=stats,
        recent_drafts=recent_drafts,
        autopilot_on=autopilot_on,
        next_run=next_run,
        pending_review_count=pending_review_count,
        active_agent=dict(active_agent) if active_agent else None,
    )


@bp.route("/<tenant_id>/toggle-autopilot", methods=["POST"])
@tenant_access_required
def toggle_autopilot(tenant_id):
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled", False))
    db = get_db()
    db.execute("""
        INSERT INTO settings (tenant_id, key, value, updated_at)
        VALUES (?, 'autopilot', ?, CURRENT_TIMESTAMP)
        ON CONFLICT(tenant_id, key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP
    """, (tenant_id, json.dumps({"enabled": enabled})))
    db.commit()
    db.close()
    return jsonify({"ok": True, "enabled": enabled})


# ── Content ───────────────────────────────────────────────────────────────────
PAGE_SIZE = 20

@bp.route("/<tenant_id>/content")
@tenant_access_required
def content(tenant_id):
    db, client, ctx = _get_client_and_ctx(tenant_id)
    if not client:
        db.close()
        flash("Client not found.", "error")
        return redirect(url_for("clients.list_clients"))

    current_status = request.args.get("status", "")
    page = max(1, int(request.args.get("page", 1)))
    offset = (page - 1) * PAGE_SIZE

    conditions = ["tenant_id=?"]
    params = [tenant_id]
    if current_status:
        conditions.append("status=?")
        params.append(current_status)

    where = " AND ".join(conditions)
    total = db.execute(f"SELECT COUNT(*) FROM pages WHERE {where}", params).fetchone()[0]
    pages_rows = db.execute(
        f"SELECT * FROM pages WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [PAGE_SIZE, offset]
    ).fetchall()

    counts = db.execute("""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status='draft' AND content IS NOT NULL AND content != '' THEN 1 ELSE 0 END) AS draft,
            SUM(CASE WHEN status='ready'     THEN 1 ELSE 0 END) AS ready,
            SUM(CASE WHEN status='published' THEN 1 ELSE 0 END) AS published
        FROM pages WHERE tenant_id=?
    """, (tenant_id,)).fetchone()

    pending_review_count = counts["draft"] or 0
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    db.close()

    return render_template(
        "lite/content.html",
        client=dict(client),
        tenant_id=tenant_id,
        active_page="content",
        pages=[dict(p) for p in pages_rows],
        counts=dict(counts) if counts else {"total": 0, "draft": 0, "ready": 0, "published": 0},
        current_status=current_status,
        page=page,
        total_pages=total_pages,
        pending_review_count=pending_review_count,
    )


# ── Rankings ──────────────────────────────────────────────────────────────────
@bp.route("/<tenant_id>/rankings")
@tenant_access_required
def rankings(tenant_id):
    db, client, ctx = _get_client_and_ctx(tenant_id)
    if not client:
        db.close()
        flash("Client not found.", "error")
        return redirect(url_for("clients.list_clients"))

    keywords = db.execute(
        "SELECT * FROM keywords WHERE tenant_id=? ORDER BY priority, volume DESC LIMIT 100",
        (tenant_id,)
    ).fetchall()
    pending_review_count = _pending_review_count(db, tenant_id)
    db.close()

    return render_template(
        "lite/rankings.html",
        client=dict(client),
        tenant_id=tenant_id,
        active_page="rankings",
        keywords=[dict(k) for k in keywords],
        pending_review_count=pending_review_count,
    )


# ── GEO ───────────────────────────────────────────────────────────────────────
@bp.route("/<tenant_id>/geo")
@tenant_access_required
def geo(tenant_id):
    db, client, ctx = _get_client_and_ctx(tenant_id)
    if not client:
        db.close()
        flash("Client not found.", "error")
        return redirect(url_for("clients.list_clients"))

    geo_queries = db.execute(
        "SELECT * FROM geo_queries WHERE tenant_id=? ORDER BY checked_at DESC LIMIT 50",
        (tenant_id,)
    ).fetchall()

    total_queries = len(geo_queries)
    cited = sum(1 for q in geo_queries if q["client_mentioned"])
    citation_rate = round(cited / total_queries * 100) if total_queries > 0 else 0
    pending_review_count = _pending_review_count(db, tenant_id)
    db.close()

    return render_template(
        "lite/geo.html",
        client=dict(client),
        tenant_id=tenant_id,
        active_page="geo",
        geo_queries=[dict(q) for q in geo_queries],
        total_queries=total_queries,
        citation_rate=citation_rate,
        pending_review_count=pending_review_count,
    )


# ── Settings ──────────────────────────────────────────────────────────────────
@bp.route("/<tenant_id>/settings")
@tenant_access_required
def settings_view(tenant_id):
    db, client, ctx = _get_client_and_ctx(tenant_id)
    if not client:
        db.close()
        flash("Client not found.", "error")
        return redirect(url_for("clients.list_clients"))

    pending_review_count = _pending_review_count(db, tenant_id)
    db.close()

    return render_template(
        "lite/settings.html",
        client=dict(client),
        tenant_id=tenant_id,
        active_page="settings",
        ctx=dict(ctx) if ctx else {},
        pending_review_count=pending_review_count,
        publisher_type=client["publisher_type"] if client and client["publisher_type"] else "wordpress",
    )


@bp.route("/<tenant_id>/settings/update", methods=["POST"])
@tenant_access_required
def update_settings(tenant_id):
    f = request.form
    section = f.get("section", "")
    db = get_db()

    if section == "business":
        db.execute("""
            UPDATE clients SET name=?, phone=?, city=?, state=?, website_url=?, updated_at=CURRENT_TIMESTAMP
            WHERE tenant_id=?
        """, (
            f.get("name", "").strip(),
            f.get("phone", "").strip(),
            f.get("city", "").strip(),
            f.get("state", "CA").strip(),
            f.get("website_url", "").strip(),
            tenant_id
        ))
        db.commit()
        flash("Business info saved.", "success")

    elif section == "publishing":
        publisher_type = f.get("publisher_type", "wordpress")
        github_token    = f.get("github_token", "").strip()
        wp_app_password = f.get("wp_app_password", "").strip()

        # Preserve existing tokens if blank — encrypt new values
        from denzo.crypto import encrypt_token
        existing = db.execute(
            "SELECT github_token, wp_app_password, encrypted FROM client_context WHERE tenant_id=?", (tenant_id,)
        ).fetchone()
        if github_token:
            if not github_token.startswith("gAAAAAB"):
                github_token = encrypt_token(github_token)
        elif existing:
            github_token = existing["github_token"] or ""
        if wp_app_password:
            if not wp_app_password.startswith("gAAAAAB"):
                wp_app_password = encrypt_token(wp_app_password)
        elif existing:
            wp_app_password = existing["wp_app_password"] or ""

        db.execute("""
            UPDATE client_context SET
                github_repo=?, github_branch=?, github_token=?, github_format=?,
                wp_url=?, wp_user=?, wp_app_password=?,
                pages_domain=?, encrypted=1
            WHERE tenant_id=?
        """, (
            f.get("github_repo", "").strip(),
            f.get("github_branch", "main").strip(),
            github_token,
            f.get("github_format", "html").strip(),
            f.get("wp_url", "").strip(),
            f.get("wp_user", "").strip(),
            wp_app_password,
            f.get("pages_domain", "").strip(),
            tenant_id
        ))
        db.execute(
            "UPDATE clients SET publisher_type=?, updated_at=CURRENT_TIMESTAMP WHERE tenant_id=?",
            (publisher_type, tenant_id)
        )
        db.commit()
        flash("Publishing settings saved.", "success")

    db.close()
    return redirect(url_for("lite.settings_view", tenant_id=tenant_id))
