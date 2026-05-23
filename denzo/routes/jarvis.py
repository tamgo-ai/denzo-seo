"""
Jarvis 2.0 — AI Command Center for Denzo SEO.

A single surface that replaces /pipeline, /agents, and /mission-control.
The user talks to "Denzo Operator" — a Claude-powered AI that can EXECUTE
commands (start/stop agents, run pipeline), not just describe them.

Routes:
  GET  /clients/<tenant_id>/jarvis/             HTML HUD
  GET  /clients/<tenant_id>/jarvis/state        JSON poll (every 2.5s)
  POST /clients/<tenant_id>/jarvis/chat         {"message": "..."} → operator reply (streaming)
  POST /clients/<tenant_id>/jarvis/command      {"action": "start_agent", "agent": "..."} → execute
  GET  /clients/<tenant_id>/jarvis/agent/<name> Agent detail + recent output
"""
import os, json, logging, time, uuid
from datetime import datetime, timedelta
from collections import defaultdict

from flask import Blueprint, render_template, jsonify, request, abort, Response, stream_with_context

from denzo.auth import login_required, can_access_tenant
from denzo.db import get_db
from denzo.agents.registry import AGENT_REGISTRY, LAYER_LABELS

logger = logging.getLogger(__name__)
bp = Blueprint("jarvis", __name__, url_prefix="/clients/<tenant_id>/jarvis")

# ── Conversation memory (in-process, per-tenant, last 10 messages) ──────────
_chat_memory: dict[str, list[dict]] = defaultdict(list)
_MAX_MEMORY = 10


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


# ── State (polled by HUD every 2.5s) ─────────────────────────────────────────

@bp.route("/state")
@login_required
def state(tenant_id):
    if not can_access_tenant(tenant_id):
        abort(403)

    db = get_db()

    agents = [dict(r) for r in db.execute("""
        SELECT name, layer, color, status, current_task, last_message, last_run_at, run_count
        FROM agents WHERE tenant_id=?
    """, (tenant_id,)).fetchall()]

    raw_activity = db.execute("""
        SELECT type, agent, message, level, created_at
        FROM activity WHERE tenant_id=?
        ORDER BY id DESC LIMIT 60
    """, (tenant_id,)).fetchall()

    activity = []
    seen = set()
    for r in raw_activity:
        msg = (r["message"] or "").strip()
        key = (r["agent"] or "", msg[:70].lower(), r["level"] or "")
        if key in seen:
            continue
        seen.add(key)
        activity.append(dict(r))
        if len(activity) >= 14:
            break

    def n(sql, *args):
        row = db.execute(sql, args).fetchone()
        return int((row["n"] if row else 0) or 0)

    pages_total     = n("SELECT COUNT(*) AS n FROM pages WHERE tenant_id=?", tenant_id)
    pages_published = n("SELECT COUNT(*) AS n FROM pages WHERE tenant_id=? AND status='published'", tenant_id)
    pages_ready     = n("SELECT COUNT(*) AS n FROM pages WHERE tenant_id=? AND status='ready'", tenant_id)
    pages_draft     = n("SELECT COUNT(*) AS n FROM pages WHERE tenant_id=? AND status='draft'", tenant_id)
    keywords_total  = n("SELECT COUNT(*) AS n FROM keywords WHERE tenant_id=?", tenant_id)
    keywords_high   = n("SELECT COUNT(*) AS n FROM keywords WHERE tenant_id=? AND priority='high'", tenant_id)
    competitors     = n("SELECT COUNT(*) AS n FROM competitors WHERE tenant_id=?", tenant_id)
    geo_total       = n("SELECT COUNT(*) AS n FROM geo_queries WHERE tenant_id=?", tenant_id)
    geo_cited_row   = db.execute("SELECT COALESCE(SUM(client_mentioned),0) AS c FROM geo_queries WHERE tenant_id=?", (tenant_id,)).fetchone()
    geo_citations   = int(geo_cited_row["c"] or 0) if geo_cited_row else 0

    # Agent status counts
    agents_working = sum(1 for a in agents if a["status"] == "working")
    agents_done    = sum(1 for a in agents if a["status"] == "done")
    agents_error   = sum(1 for a in agents if a["status"] == "error")
    total_agents   = len(agents)

    # Pipeline progress %
    if pages_total > 0:
        pipeline_pct = round((pages_published / max(pages_total, 1)) * 100)
    else:
        pipeline_pct = 0

    # ETA: rough estimate based on current state
    if pages_published >= pages_total and pages_total > 0:
        eta = "Complete"
    elif agents_working > 0:
        eta = f"~{max(5, (pages_total - pages_published) // max(agents_working, 1))}min"
    elif pages_draft > 0:
        eta = "Waiting for Programmatic SEO"
    elif keywords_total < 20:
        eta = "Run Keyword Strategist"
    else:
        eta = "Idle"

    # Sparklines (7 days)
    spark_pages = []
    spark_keywords = []
    for delta in range(6, -1, -1):
        start = (datetime.utcnow() - timedelta(days=delta+1)).strftime("%Y-%m-%d")
        end   = (datetime.utcnow() - timedelta(days=delta)).strftime("%Y-%m-%d")
        p = db.execute("SELECT COUNT(*) AS n FROM pages WHERE tenant_id=? AND date(created_at) BETWEEN ? AND ?",
                       (tenant_id, start, end)).fetchone()
        spark_pages.append(int((p["n"] if p else 0) or 0))
        k = db.execute("SELECT COUNT(*) AS n FROM keywords WHERE tenant_id=? AND date(created_at) BETWEEN ? AND ?",
                       (tenant_id, start, end)).fetchone()
        spark_keywords.append(int((k["n"] if k else 0) or 0))

    missions = _derive_missions(
        pages_total=pages_total, pages_published=pages_published,
        pages_ready=pages_ready, pages_draft=pages_draft,
        keywords_total=keywords_total, keywords_high=keywords_high,
        geo_total=geo_total, geo_citations=geo_citations,
        competitors=competitors, agents=agents,
    )

    db.close()

    return jsonify({
        "agents": agents,
        "activity": activity,
        "kpis": {
            "pages_total": pages_total, "pages_published": pages_published,
            "pages_ready": pages_ready, "pages_draft": pages_draft,
            "keywords_total": keywords_total, "keywords_high": keywords_high,
            "competitors": competitors, "geo_citations": geo_citations,
            "geo_queries_total": geo_total,
        },
        "sparklines": {"pages": spark_pages, "keywords": spark_keywords},
        "missions": missions,
        "pipeline": {
            "progress_pct": pipeline_pct,
            "eta": eta,
            "agents_working": agents_working,
            "agents_done": agents_done,
            "agents_error": agents_error,
            "agents_total": total_agents,
        },
    })


# ── Command execution ────────────────────────────────────────────────────────

@bp.route("/command", methods=["POST"])
@login_required
def command(tenant_id):
    """Execute a real action: start/stop agent, run pipeline, reset."""
    if not can_access_tenant(tenant_id):
        abort(403)

    payload = request.get_json(silent=True) or {}
    action = (payload.get("action") or "").strip()
    agent_name = (payload.get("agent") or "").strip()

    if not action:
        return jsonify({"error": "no action specified"}), 400

    try:
        from denzo.agents.runner import AgentRunner
        from denzo.agents.base_agent import db_write
        from denzo.context.builder import build_client_context

        if action == "start_agent":
            if agent_name not in AGENT_REGISTRY:
                return jsonify({"error": f"Unknown agent: {agent_name}"}), 400
            result = AgentRunner.start(tenant_id, agent_name)
            if result["status"] == "prereq_failed":
                return jsonify({"ok": True, "status": "blocked", "reason": result.get("message")})
            if result["status"] == "already_running":
                return jsonify({"ok": True, "status": "already_running"})
            return jsonify({"ok": True, "status": "started", "agent": agent_name})

        elif action == "stop_agent":
            result = AgentRunner.stop(tenant_id, agent_name)
            return jsonify({"ok": True, "status": result.get("status"), "agent": agent_name})

        elif action == "run_pipeline":
            result = AgentRunner.start(tenant_id, "Pipeline Director")
            if result["status"] == "already_running":
                return jsonify({"ok": True, "status": "already_running"})
            return jsonify({"ok": True, "status": "started", "message": "Pipeline Director activated"})

        elif action == "stop_pipeline":
            AgentRunner.stop_all(tenant_id)
            return jsonify({"ok": True, "status": "stopped"})

        elif action == "reset_pipeline":
            count = AgentRunner.stop_all(tenant_id).get("count", 0)
            db = get_db()
            db.execute("UPDATE agents SET status='idle', current_task='Reset by Jarvis', next_task='' WHERE tenant_id=?", (tenant_id,))
            db.execute("INSERT INTO activity (tenant_id, type, message, agent, level) VALUES (?,?,?,?,?)",
                       (tenant_id, "system", "Pipeline reset by Jarvis command.", "Denzo Operator", "warning"))
            db.commit()
            db.close()
            return jsonify({"ok": True, "status": "reset", "stopped": count})

        else:
            return jsonify({"error": f"Unknown action: {action}"}), 400

    except Exception as e:
        logger.exception("Jarvis command failed")
        return jsonify({"error": str(e)[:200]}), 500


# ── Agent detail ─────────────────────────────────────────────────────────────

@bp.route("/agent/<agent_name>")
@login_required
def agent_detail(tenant_id, agent_name):
    if not can_access_tenant(tenant_id):
        abort(403)

    db = get_db()
    agent = db.execute(
        "SELECT * FROM agents WHERE tenant_id=? AND name=?",
        (tenant_id, agent_name)
    ).fetchone()
    if not agent:
        db.close()
        return jsonify({"error": "Agent not found"}), 404

    recent_logs = db.execute(
        "SELECT message, level, created_at FROM activity WHERE tenant_id=? AND agent=? ORDER BY id DESC LIMIT 10",
        (tenant_id, agent_name)
    ).fetchall()

    db.close()

    return jsonify({
        "agent": dict(agent),
        "recent_logs": [dict(r) for r in recent_logs],
    })


# ── Chat with Denzo Operator (streaming + memory) ────────────────────────────

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
        return jsonify({"reply": "AI key not configured on this server.", "source": "fallback"})

    context = _build_chat_context(tenant_id)
    memory = _chat_memory.get(tenant_id, [])

    system_prompt = (
        "You are Denzo Operator, the AI manager for Denzo SEO — a platform with 26 "
        "specialist agents that automate the entire SEO pipeline. You speak in a short, "
        "direct, confident tone like J.A.R.V.I.S. from Iron Man. You can EXECUTE commands "
        "— the user can ask you to start agents, stop them, or run the pipeline, "
        "and the system will do it. When they ask for an action, tell them you're doing it "
        "and instruct them to use the buttons that appear, or tell them to type "
        "'/start [agent]' or '/pipeline' to trigger actions. "
        "Always respond in English. Never invent numbers — use only the state below. "
        "Keep replies under 4 sentences unless the user asks for detail. "
        "Be proactive: if you see a problem, suggest a solution.\n\n"
        "CURRENT STATE:\n" + context
    )

    # Build messages with conversation memory
    messages = []
    for m in memory[-_MAX_MEMORY:]:
        messages.append(m)
    messages.append({"role": "user", "content": user_msg})

    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)

        # Streaming response
        def generate():
            full_reply = ""
            try:
                with client.messages.stream(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=500,
                    system=system_prompt,
                    messages=messages,
                ) as stream:
                    for event in stream:
                        if event.type == "content_block_delta" and event.delta.type == "text_delta":
                            chunk = event.delta.text
                            full_reply += chunk
                            yield f"data: {json.dumps({'chunk': chunk})}\n\n"
                # Store in memory
                _chat_memory[tenant_id].append({"role": "user", "content": user_msg})
                _chat_memory[tenant_id].append({"role": "assistant", "content": full_reply})
                if len(_chat_memory[tenant_id]) > _MAX_MEMORY * 2:
                    _chat_memory[tenant_id] = _chat_memory[tenant_id][-_MAX_MEMORY * 2:]
                yield f"data: {json.dumps({'done': True, 'full': full_reply})}\n\n"
            except Exception as e:
                logger.exception("Jarvis stream error")
                yield f"data: {json.dumps({'error': str(e)[:100]})}\n\n"

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            }
        )

    except Exception as e:
        logger.exception("Jarvis chat init failed")
        return jsonify({
            "reply": f"Operator offline ({type(e).__name__}). Try again.",
            "source": "error",
        }), 200


# ── Helpers ──────────────────────────────────────────────────────────────────

def _build_chat_context(tenant_id: str) -> str:
    db = get_db()
    client = dict(db.execute("SELECT name, business_type, website_url FROM clients WHERE tenant_id=?", (tenant_id,)).fetchone() or {})
    ctx_row = db.execute("SELECT primary_city, industry_vertical, service_cities FROM client_context WHERE tenant_id=?", (tenant_id,)).fetchone()
    ctx = dict(ctx_row) if ctx_row else {}

    def n(sql, *args):
        row = db.execute(sql, args).fetchone()
        return int((row["n"] if row else 0) or 0)

    pages_total     = n("SELECT COUNT(*) AS n FROM pages WHERE tenant_id=?", tenant_id)
    pages_published = n("SELECT COUNT(*) AS n FROM pages WHERE tenant_id=? AND status='published'", tenant_id)
    pages_ready     = n("SELECT COUNT(*) AS n FROM pages WHERE tenant_id=? AND status='ready'", tenant_id)
    keywords        = n("SELECT COUNT(*) AS n FROM keywords WHERE tenant_id=?", tenant_id)
    competitors     = n("SELECT COUNT(*) AS n FROM competitors WHERE tenant_id=?", tenant_id)

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
        f"Vertical: {ctx.get('industry_vertical', 'general')} · City: {ctx.get('primary_city', '?')}",
        f"Keywords: {keywords} · Pages: {pages_total} ({pages_published} published, {pages_ready} ready)",
        f"Competitors mapped: {competitors}",
    ]
    if working:
        parts.append("Working:")
        for w in working:
            parts.append(f"  - {w['name']}: {w['current_task'] or 'active'}")
    if recent_done:
        parts.append("Recently completed:")
        for r in recent_done:
            parts.append(f"  - {r['name']}: {r['last_message'] or 'completed'}")
    return "\n".join(parts)


def _derive_missions(*, pages_total, pages_published, pages_ready, pages_draft,
                     keywords_total, keywords_high, geo_total, geo_citations,
                     competitors, agents) -> list[dict]:
    missions = []

    # M1 — Research
    if keywords_total < 100:
        progress = keywords_total / 100
        ka = next((a for a in agents if a["name"] == "Keyword Strategist"), None)
        missions.append({
            "id": "m1", "label": "Build keyword foundation",
            "status": "yellow" if keywords_total else "red",
            "progress": progress,
            "detail": f"{keywords_total}/100 keywords researched",
            "action": "start_agent", "action_label": "▶ Run Keyword Strategist",
            "action_target": "Keyword Strategist",
        })
    else:
        missions.append({
            "id": "m1", "label": "Build keyword foundation",
            "status": "green", "progress": 1.0,
            "detail": f"{keywords_total} keywords · {keywords_high} high priority",
            "action": None, "action_label": "", "action_target": "",
        })

    # M2 — Content generation
    publish_target = max(pages_total, 50)
    if pages_published < publish_target:
        progress = pages_published / publish_target if publish_target else 0
        pso = next((a for a in agents if a["name"] == "Programmatic SEO"), None)
        if pages_draft > 0 and (not pso or pso["status"] != "working"):
            action, action_label, action_target = "start_agent", f"▶ Generate {pages_draft} drafts", "Programmatic SEO"
        elif pages_ready > 0:
            action, action_label, action_target = "start_agent", "▶ Publish ready pages", "GitHub Publisher"
        else:
            action, action_label, action_target = "run_pipeline", "▶ Run Pipeline Director", ""
        missions.append({
            "id": "m2", "label": f"Publish content",
            "status": "green" if pages_published > publish_target * 0.5 else "yellow",
            "progress": progress,
            "detail": f"{pages_published} published · {pages_ready} ready · {pages_draft} draft",
            "action": action, "action_label": action_label, "action_target": action_target,
        })

    # M3 — GEO citations
    geo_target = max(geo_total, 20) or 20
    geo_pct = geo_citations / geo_target if geo_target else 0
    missions.append({
        "id": "m3", "label": "Get cited in AI answers",
        "status": "green" if geo_pct > 0.3 else ("yellow" if geo_citations else "red"),
        "progress": min(1.0, geo_pct),
        "detail": f"{geo_citations} citations across {geo_total} queries",
        "action": "start_agent" if geo_citations < 3 else None,
        "action_label": "▶ Run GEO Monitor" if geo_citations < 3 else "",
        "action_target": "GEO Monitor" if geo_citations < 3 else "",
    })

    # M4 — Competitive landscape
    if competitors == 0:
        missions.append({
            "id": "m4", "label": "Map competitive landscape",
            "status": "red", "progress": 0,
            "detail": "No competitors analyzed yet",
            "action": "start_agent", "action_label": "▶ Run Competitor Intel",
            "action_target": "Competitor Intel",
        })
    else:
        missions.append({
            "id": "m4", "label": "Monitor competition",
            "status": "green", "progress": 1.0,
            "detail": f"{competitors} competitors mapped",
            "action": None, "action_label": "", "action_target": "",
        })

    return missions
