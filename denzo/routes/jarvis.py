"""
Jarvis — ambient operator HUD.

A single surface that replaces /pipeline, /agents, and /mission-control.
The user talks to "Denzo Operator" (a Claude-powered AI manager that
knows the state of the 26 agents and can answer questions, kick off
work, or explain what's happening). The orbital core in the center
reacts to chat and agent activity.

Routes:
  GET  /clients/<tenant_id>/jarvis/             HTML HUD
  GET  /clients/<tenant_id>/jarvis/state        JSON poll (every 2s)
  POST /clients/<tenant_id>/jarvis/chat         {"message": "..."} → operator reply
"""
import os
import json
import logging
from datetime import datetime, timedelta

from flask import Blueprint, render_template, jsonify, request, abort

from denzo.auth import login_required, can_access_tenant
from denzo.db import get_db
from denzo.agents.registry import AGENT_REGISTRY, LAYER_LABELS

logger = logging.getLogger(__name__)
bp = Blueprint("jarvis", __name__, url_prefix="/clients/<tenant_id>/jarvis")


# ── Page ──────────────────────────────────────────────────────────────────────

@bp.route("/")
@login_required
def index(tenant_id):
    if not can_access_tenant(tenant_id):
        abort(403)
    db = get_db()
    client = db.execute("SELECT * FROM clients WHERE tenant_id=?", (tenant_id,)).fetchone()
    db.close()
    if not client:
        abort(404)
    return render_template("clients/jarvis.html", client=client, active_tenant=tenant_id)


# ── State (reused by HUD poll) ────────────────────────────────────────────────

@bp.route("/state")
@login_required
def state(tenant_id):
    if not can_access_tenant(tenant_id):
        abort(403)

    db = get_db()

    # Compact agent state — only what HUD shows
    agents = [dict(r) for r in db.execute("""
        SELECT name, layer, color, status, current_task, last_message, last_run_at
        FROM agents WHERE tenant_id=?
    """, (tenant_id,)).fetchall()]

    # Activity stream — last 12 events with non-trivial messages
    activity = [dict(r) for r in db.execute("""
        SELECT type, agent, message, level, created_at
        FROM activity WHERE tenant_id=?
        ORDER BY id DESC LIMIT 12
    """, (tenant_id,)).fetchall()]

    # KPIs
    def n(sql, *args):
        row = db.execute(sql, args).fetchone()
        return int((row["n"] if row else 0) or 0)

    pages_total      = n("SELECT COUNT(*) AS n FROM pages WHERE tenant_id=?", tenant_id)
    pages_published  = n("SELECT COUNT(*) AS n FROM pages WHERE tenant_id=? AND status='published'", tenant_id)
    pages_ready      = n("SELECT COUNT(*) AS n FROM pages WHERE tenant_id=? AND status='ready'", tenant_id)
    pages_draft      = n("SELECT COUNT(*) AS n FROM pages WHERE tenant_id=? AND status='draft'", tenant_id)
    keywords_total   = n("SELECT COUNT(*) AS n FROM keywords WHERE tenant_id=?", tenant_id)
    keywords_high    = n("SELECT COUNT(*) AS n FROM keywords WHERE tenant_id=? AND priority='high'", tenant_id)
    competitors      = n("SELECT COUNT(*) AS n FROM competitors WHERE tenant_id=?", tenant_id)
    geo_total        = n("SELECT COUNT(*) AS n FROM geo_queries WHERE tenant_id=?", tenant_id)
    geo_cited_row    = db.execute("SELECT SUM(client_mentioned) AS c FROM geo_queries WHERE tenant_id=?", (tenant_id,)).fetchone()
    geo_citations    = int((geo_cited_row["c"] if geo_cited_row else 0) or 0)

    # Sparklines: count pages.created_at per day for last 7 days
    seven_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
    spark_pages = []
    spark_keywords = []
    for delta in range(6, -1, -1):  # 6 days ago → today
        start = (datetime.utcnow() - timedelta(days=delta+1)).strftime("%Y-%m-%d")
        end   = (datetime.utcnow() - timedelta(days=delta)).strftime("%Y-%m-%d")
        p = db.execute("SELECT COUNT(*) AS n FROM pages WHERE tenant_id=? AND date(created_at) BETWEEN ? AND ?",
                       (tenant_id, start, end)).fetchone()
        spark_pages.append(int((p["n"] if p else 0) or 0))
        k = db.execute("SELECT COUNT(*) AS n FROM keywords WHERE tenant_id=? AND date(created_at) BETWEEN ? AND ?",
                       (tenant_id, start, end)).fetchone()
        spark_keywords.append(int((k["n"] if k else 0) or 0))

    # Missions — derived from current state (no separate table needed)
    missions = _derive_missions(
        pages_total=pages_total, pages_published=pages_published,
        pages_ready=pages_ready, pages_draft=pages_draft,
        keywords_total=keywords_total, keywords_high=keywords_high,
        geo_total=geo_total, geo_citations=geo_citations,
        competitors=competitors, agents=agents,
    )

    # Activity-derived "agents talking" flows (any working pair where one
    # depends on the other being done)
    working = [a for a in agents if a["status"] == "working"]
    in_progress_count = len(working)

    db.close()

    return jsonify({
        "agents": agents,
        "activity": activity,
        "kpis": {
            "pages_total":      pages_total,
            "pages_published":  pages_published,
            "pages_ready":      pages_ready,
            "pages_draft":      pages_draft,
            "keywords_total":   keywords_total,
            "keywords_high":    keywords_high,
            "competitors":      competitors,
            "geo_citations":    geo_citations,
            "geo_queries_total": geo_total,
        },
        "sparklines": {
            "pages":    spark_pages,
            "keywords": spark_keywords,
        },
        "missions":           missions,
        "in_progress_count":  in_progress_count,
        "working_agents":     [w["name"] for w in working],
    })


def _derive_missions(*, pages_total, pages_published, pages_ready, pages_draft,
                     keywords_total, keywords_high, geo_total, geo_citations,
                     competitors, agents) -> list[dict]:
    """Turn raw state into 3-5 'business missions' the user actually cares about.

    Each mission has: id, label, status (green/yellow/red/idle), progress 0-1,
    detail (one-line), next_action (what's coming).
    """
    missions = []

    # M1 — research foundation
    research_target = 100
    if keywords_total < research_target:
        progress = keywords_total / research_target
        ka = next((a for a in agents if a["name"] == "Keyword Strategist"), None)
        next_action = (ka["current_task"] if ka and ka["status"] == "working" else "Run Keyword Strategist") if ka else ""
        missions.append({
            "id": "m1-research", "label": "Build keyword foundation",
            "status": "yellow" if keywords_total else "red",
            "progress": progress,
            "detail": f"{keywords_total}/{research_target} keywords researched",
            "next_action": next_action,
        })
    else:
        missions.append({
            "id": "m1-research", "label": "Build keyword foundation",
            "status": "green", "progress": 1.0,
            "detail": f"{keywords_total} keywords · {keywords_high} high priority",
            "next_action": "Clustering and strategy phase",
        })

    # M2 — generate content
    publish_target = max(pages_total, 50)
    if pages_published < publish_target:
        progress = pages_published / publish_target if publish_target else 0
        # Detect blockers
        pso = next((a for a in agents if a["name"] == "Programmatic SEO"), None)
        co  = next((a for a in agents if a["name"] == "Content Optimizer"), None)
        if pages_ready > 5 and (not co or co["status"] != "working"):
            next_action = f"Content Optimizer ready: {pages_ready} pages waiting"
        elif pso and pso["status"] == "working":
            next_action = pso["current_task"] or "Generating drafts"
        elif pages_draft > 0:
            next_action = f"{pages_draft} drafts queued"
        else:
            next_action = "Run Vertical Matrix + Programmatic SEO"
        missions.append({
            "id": "m2-publish", "label": f"Publish {publish_target} pages",
            "status": "green" if pages_published > publish_target * 0.5 else ("yellow" if pages_total else "red"),
            "progress": progress,
            "detail": f"{pages_published} published · {pages_ready} ready · {pages_draft} draft",
            "next_action": next_action,
        })

    # M3 — GEO citations
    geo_target = max(geo_total, 20) or 20
    geo_pct = geo_citations / geo_target if geo_target else 0
    missions.append({
        "id": "m3-geo", "label": "Get cited in AI answers",
        "status": "green" if geo_pct > 0.5 else ("yellow" if geo_citations else "red"),
        "progress": geo_pct,
        "detail": f"{geo_citations}/{geo_target} AI queries cite this site",
        "next_action": "Run GEO Optimizer + GEO Monitor" if geo_citations < 5 else "Monitor weekly",
    })

    # M4 — competitive intel
    if competitors == 0:
        missions.append({
            "id": "m4-comp", "label": "Map competitive landscape",
            "status": "red", "progress": 0,
            "detail": "No competitors analyzed yet",
            "next_action": "Run Competitor Intel",
        })

    return missions


# ── Chat with Denzo Operator (lightweight Claude wrapper) ─────────────────────

@bp.route("/chat", methods=["POST"])
@login_required
def chat(tenant_id):
    if not can_access_tenant(tenant_id):
        abort(403)

    payload = request.get_json(silent=True) or {}
    user_msg = (payload.get("message") or "").strip()
    if not user_msg:
        return jsonify({"error": "empty message"}), 400
    if len(user_msg) > 1500:
        return jsonify({"error": "message too long"}), 400

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return jsonify({
            "reply": ("AI key not configured on this server. "
                      "Admin must set ANTHROPIC_API_KEY in .env."),
            "source": "fallback",
        })

    # Build context block from current state
    context = _build_chat_context(tenant_id)

    system_prompt = (
        "You are Denzo Operator, the AI manager for a multi-vertical SEO + GEO "
        "platform with 26 specialist agents. You speak with the platform owner "
        "in a short, direct, Silicon-Valley-engineer tone — like Jarvis from "
        "Iron Man. Spanish or English, match the user's language. Never invent "
        "numbers — only quote what's in the state context below. If the user "
        "asks for an action you can describe but cannot execute (like 'start "
        "the pipeline'), explain exactly which button or page they'd click. "
        "Keep replies under 4 sentences unless the user asks for detail."
        "\n\nCURRENT STATE:\n" + context
    )

    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
        return jsonify({"reply": text, "source": "claude-haiku-4-5"})
    except Exception as e:
        logger.exception("Jarvis chat call failed")
        return jsonify({
            "reply": f"Operator AI is offline ({type(e).__name__}). Try again in a moment.",
            "source": "error",
        }), 200


def _build_chat_context(tenant_id: str) -> str:
    """Compact textual state for the system prompt."""
    db = get_db()
    client = dict(db.execute("SELECT name, business_type, website_url FROM clients WHERE tenant_id=?", (tenant_id,)).fetchone() or {})
    ctx_row = db.execute("SELECT primary_city, industry_vertical, service_cities FROM client_context WHERE tenant_id=?", (tenant_id,)).fetchone()
    ctx = dict(ctx_row) if ctx_row else {}

    def n(sql, *args):
        row = db.execute(sql, args).fetchone()
        return int((row["n"] if row else 0) or 0)

    pages_total      = n("SELECT COUNT(*) AS n FROM pages WHERE tenant_id=?", tenant_id)
    pages_published  = n("SELECT COUNT(*) AS n FROM pages WHERE tenant_id=? AND status='published'", tenant_id)
    pages_ready      = n("SELECT COUNT(*) AS n FROM pages WHERE tenant_id=? AND status='ready'", tenant_id)
    keywords         = n("SELECT COUNT(*) AS n FROM keywords WHERE tenant_id=?", tenant_id)
    competitors      = n("SELECT COUNT(*) AS n FROM competitors WHERE tenant_id=?", tenant_id)

    working = db.execute("""
        SELECT name, current_task FROM agents WHERE tenant_id=? AND status='working' LIMIT 10
    """, (tenant_id,)).fetchall()
    recent_done = db.execute("""
        SELECT name, last_message FROM agents WHERE tenant_id=? AND status='done' AND last_run_at IS NOT NULL
        ORDER BY last_run_at DESC LIMIT 5
    """, (tenant_id,)).fetchall()
    db.close()

    parts = [
        f"Client: {client.get('name', '?')} ({client.get('business_type', '?')})",
        f"Vertical: {ctx.get('industry_vertical', 'general')} · Primary city: {ctx.get('primary_city', '?')}",
        f"Pipeline: {keywords} keywords · {pages_total} pages ({pages_published} published, {pages_ready} ready)",
        f"Competitors mapped: {competitors}",
    ]
    if working:
        parts.append("Currently working:")
        for w in working:
            parts.append(f"  - {w['name']}: {w['current_task'] or 'no task description'}")
    if recent_done:
        parts.append("Recently completed:")
        for r in recent_done:
            parts.append(f"  - {r['name']}: {r['last_message'] or 'completed'}")
    return "\n".join(parts)
