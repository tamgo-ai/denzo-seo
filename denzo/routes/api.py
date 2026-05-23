"""
API blueprint — agent control, WebSocket log streaming, stats.
"""
import json
import time
from flask import Blueprint, jsonify, request
from denzo import sock
from denzo.auth import login_required, can_access_tenant
from denzo.db import get_db
from denzo.agents.registry import AGENT_REGISTRY
from denzo.agents.runner import AgentRunner

bp = Blueprint("api", __name__, url_prefix="/api")
_can_access_tenant = can_access_tenant


# ── Pipeline Director endpoints ─────────────────────────────────────────────────

@bp.route("/<tenant_id>/pipeline/run", methods=["POST"])
@login_required
def run_pipeline(tenant_id):
    """Start the autonomous Director which orchestrates the full pipeline."""
    if not _can_access_tenant(tenant_id):
        return jsonify({"error": "Access denied"}), 403

    result = AgentRunner.start(tenant_id, "Pipeline Director")
    if result["status"] == "already_running":
        return jsonify({"error": "Director already running"}), 409
    if result["status"] == "error":
        return jsonify({"error": result.get("message", "Unknown error")}), 500
    return jsonify({"status": "director_started"})


@bp.route("/<tenant_id>/pipeline/stop", methods=["POST"])
@login_required
def stop_pipeline(tenant_id):
    """Stop the autonomous Director and all child agents."""
    if not _can_access_tenant(tenant_id):
        return jsonify({"error": "Access denied"}), 403

    # Stop child agents first, then Director
    AgentRunner.stop_all(tenant_id)
    return jsonify({"status": "stop_requested"})


@bp.route("/<tenant_id>/pipeline/reset", methods=["POST"])
@login_required
def reset_pipeline(tenant_id):
    """Reset ALL agents to idle. Emergency recovery button."""
    if not _can_access_tenant(tenant_id):
        return jsonify({"error": "Access denied"}), 403

    count = AgentRunner.stop_all(tenant_id)["count"]

    db = get_db()
    db.execute(
        "INSERT INTO activity (tenant_id, type, message, agent, level) VALUES (?,?,?,?,?)",
        (tenant_id, "system", "Pipeline reset by user — all agents set to idle.", "System", "warning")
    )
    db.commit()
    db.close()

    return jsonify({"status": "reset", "stopped": count, "message": "All agents reset to idle"})


# ── Agent control endpoints ────────────────────────────────────────────────────

@bp.route("/<tenant_id>/agents/start/<agent_name>", methods=["POST"])
@login_required
def start_agent(tenant_id, agent_name):
    if not _can_access_tenant(tenant_id):
        return jsonify({"error": "Access denied"}), 403
    if agent_name not in AGENT_REGISTRY:
        return jsonify({"error": f"Unknown agent: {agent_name}"}), 400

    result = AgentRunner.start(tenant_id, agent_name)
    if result["status"] == "already_running":
        return jsonify({"error": "Agent already running"}), 409
    if result["status"] == "prereq_failed":
        return jsonify({"error": result.get("message", "Prerequisites not met")}), 409
    if result["status"] == "error":
        return jsonify({"error": result.get("message", "Unknown error")}), 500
    return jsonify(result)


@bp.route("/<tenant_id>/agents/stop/<agent_name>", methods=["POST"])
@login_required
def stop_agent(tenant_id, agent_name):
    if not _can_access_tenant(tenant_id):
        return jsonify({"error": "Access denied"}), 403

    result = AgentRunner.stop(tenant_id, agent_name)
    return jsonify(result)


@bp.route("/<tenant_id>/agents/status")
@login_required
def agents_status(tenant_id):
    if not _can_access_tenant(tenant_id):
        return jsonify({"error": "Access denied"}), 403
    db = get_db()
    rows = db.execute(
        "SELECT name, status, current_task, last_run_at, run_count, layer, color FROM agents WHERE tenant_id=? ORDER BY layer, name",
        (tenant_id,)
    ).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])


# ── Stats ──────────────────────────────────────────────────────────────────────

@bp.route("/<tenant_id>/stats")
@login_required
def stats(tenant_id):
    if not _can_access_tenant(tenant_id):
        return jsonify({"error": "Access denied"}), 403
    db = get_db()
    kw = db.execute(
        "SELECT COUNT(*) FROM keywords WHERE tenant_id=?", (tenant_id,)
    ).fetchone()[0]
    pg = db.execute(
        """SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status='draft'     THEN 1 ELSE 0 END) AS draft,
            SUM(CASE WHEN status='ready'     THEN 1 ELSE 0 END) AS ready,
            SUM(CASE WHEN status='published' THEN 1 ELSE 0 END) AS published
           FROM pages WHERE tenant_id=?""",
        (tenant_id,)
    ).fetchone()
    comp = db.execute(
        "SELECT COUNT(*) FROM competitors WHERE tenant_id=?", (tenant_id,)
    ).fetchone()[0]
    db.close()

    return jsonify({
        "keywords": kw,
        "pages": {
            "total":     pg["total"]     or 0,
            "draft":     pg["draft"]     or 0,
            "ready":     pg["ready"]     or 0,
            "published": pg["published"] or 0,
        },
        "competitors": comp,
    })


# ── WebSocket ──────────────────────────────────────────────────────────────────

@sock.route("/ws/<tenant_id>/logs")
def ws_logs(ws, tenant_id):
    """
    Push real-time activity logs + agent statuses to the pipeline UI.
    Polling interval adapts: 2s when idle, 1s when agents are working.
    """
    if not _can_access_tenant(tenant_id):
        ws.close()
        return

    last_id = 0
    db = get_db()
    try:
        seed = db.execute(
            "SELECT MAX(id) FROM activity WHERE tenant_id=?", (tenant_id,)
        ).fetchone()[0]
        if seed:
            last_id = seed
    except Exception:
        pass

    while True:
        try:
            new_logs = db.execute(
                "SELECT id, agent, message, level, created_at FROM activity "
                "WHERE tenant_id=? AND id > ? ORDER BY id LIMIT 50",
                (tenant_id, last_id)
            ).fetchall()

            if new_logs:
                last_id = new_logs[-1]["id"]

            agents = db.execute(
                "SELECT name, status, current_task, layer, color, last_run_at "
                "FROM agents WHERE tenant_id=? ORDER BY layer, name",
                (tenant_id,)
            ).fetchall()

            pg = db.execute(
                """SELECT COUNT(*) AS total,
                          SUM(CASE WHEN status='published' THEN 1 ELSE 0 END) AS published,
                          (SELECT COUNT(*) FROM keywords WHERE tenant_id=?) AS kw
                   FROM pages WHERE tenant_id=?""",
                (tenant_id, tenant_id)
            ).fetchone()

            payload = {
                "logs": [
                    {"id": r["id"], "agent": r["agent"], "message": r["message"],
                     "level": r["level"], "created_at": r["created_at"]}
                    for r in new_logs
                ],
                "agents": [
                    {"name": a["name"], "status": a["status"], "current_task": a["current_task"],
                     "layer": a["layer"], "color": a["color"], "last_run_at": a["last_run_at"]}
                    for a in agents
                ],
                "stats": {
                    "keywords":  pg["kw"]        or 0,
                    "pages":     pg["total"]     or 0,
                    "published": pg["published"] or 0,
                },
            }

            ws.send(json.dumps(payload))

            # Adaptive polling: faster when agents are working, slower when idle
            has_working = any(a["status"] == "working" for a in agents)
            time.sleep(1.0 if has_working else 2.0)

        except (BrokenPipeError, OSError):
            break
        except Exception:
            try:
                db.close()
            except Exception:
                pass
            try:
                db = get_db()
            except Exception:
                break

    try:
        db.close()
    except Exception:
        pass
