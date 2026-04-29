"""
API blueprint — agent control, WebSocket log streaming, stats.
"""
import json
import time
import threading
from flask import Blueprint, jsonify, request, session, redirect, url_for
from denzo import sock
from denzo.auth import login_required
from denzo.db import get_db
from denzo.agents.registry import get_agent, AGENT_REGISTRY
from denzo.context.builder import build_client_context

bp = Blueprint("api", __name__, url_prefix="/api")

# Module-level thread tracker: tenant_id -> {agent_name -> Thread}
_agent_threads: dict[str, dict[str, threading.Thread]] = {}
# Stop signals: tenant_id -> {agent_name -> threading.Event}
_stop_events: dict[str, dict[str, threading.Event]] = {}

_lock = threading.Lock()


def _is_logged_in():
    return "user_id" in session


# ── Agent helpers ──────────────────────────────────────────────────────────────

def _mark_agent(tenant_id: str, name: str, status: str, task: str = ""):
    db = get_db()
    db.execute(
        """UPDATE agents SET status=?, current_task=?, updated_at=CURRENT_TIMESTAMP
           WHERE tenant_id=? AND name=?""",
        (status, task, tenant_id, name)
    )
    if status == "working":
        db.execute(
            "UPDATE agents SET last_run_at=CURRENT_TIMESTAMP, run_count=run_count+1 WHERE tenant_id=? AND name=?",
            (tenant_id, name)
        )
    db.commit()
    db.close()


def _log(tenant_id: str, agent: str, message: str, level: str = "info"):
    db = get_db()
    db.execute(
        "INSERT INTO activity (tenant_id, type, message, agent, level) VALUES (?,?,?,?,?)",
        (tenant_id, "agent", message, agent, level)
    )
    db.commit()
    db.close()


def _run_agent_thread(tenant_id: str, agent_name: str, stop_event: threading.Event):
    """Target function for agent threads."""
    run_id = None
    try:
        _mark_agent(tenant_id, agent_name, "working", "Initializing…")
        _log(tenant_id, agent_name, f"Starting {agent_name}…", "info")

        # Record pipeline run start
        db = get_db()
        cur = db.execute(
            "INSERT INTO pipeline_runs (tenant_id, triggered_by, agents_run, status) VALUES (?,?,?,?)",
            (tenant_id, "manual", json.dumps([agent_name]), "running")
        )
        run_id = cur.lastrowid
        db.commit()
        db.close()

        ctx = build_client_context(tenant_id)
        agent = get_agent(agent_name, ctx)

        # Wire the stop event directly to the agent's internal _stop Event
        # so that should_stop() correctly detects stop requests.
        agent._stop = stop_event

        agent.run()

        if stop_event.is_set():
            _mark_agent(tenant_id, agent_name, "idle", "Stopped by user")
            _log(tenant_id, agent_name, f"{agent_name} stopped by user.", "warning")
            _finish_run(run_id, "stopped")
        else:
            # Respect status the agent set for itself; only override if still "working"
            db = get_db()
            row = db.execute(
                "SELECT status FROM agents WHERE tenant_id=? AND name=?",
                (tenant_id, agent_name)
            ).fetchone()
            db.close()
            final_status = row["status"] if row else "working"

            if final_status == "working":
                _mark_agent(tenant_id, agent_name, "done", "Completed")
                _log(tenant_id, agent_name, f"{agent_name} completed.", "success")

            _finish_run(run_id, "completed")

    except Exception as e:
        _mark_agent(tenant_id, agent_name, "error", str(e)[:200])
        _log(tenant_id, agent_name, f"{agent_name} error: {e}", "error")
        _finish_run(run_id, "error")
    finally:
        with _lock:
            if tenant_id in _agent_threads:
                _agent_threads[tenant_id].pop(agent_name, None)
            if tenant_id in _stop_events:
                _stop_events[tenant_id].pop(agent_name, None)


def _finish_run(run_id, status: str):
    if not run_id:
        return
    try:
        db = get_db()
        db.execute(
            "UPDATE pipeline_runs SET status=?, completed_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, run_id)
        )
        db.commit()
        db.close()
    except Exception:
        pass


# ── Pipeline Director endpoint ─────────────────────────────────────────────────

@bp.route("/<tenant_id>/pipeline/run", methods=["POST"])
@login_required
def run_pipeline(tenant_id):
    """Start the autonomous Director which orchestrates the full pipeline."""
    agent_name = "Pipeline Director"
    with _lock:
        threads = _agent_threads.setdefault(tenant_id, {})
        if agent_name in threads and threads[agent_name].is_alive():
            return jsonify({"error": "Director already running"}), 409

        stop_event = threading.Event()
        _stop_events.setdefault(tenant_id, {})[agent_name] = stop_event

        t = threading.Thread(
            target=_run_agent_thread,
            args=(tenant_id, agent_name, stop_event),
            daemon=True,
            name=f"{tenant_id}:director",
        )
        threads[agent_name] = t
        t.start()

    return jsonify({"status": "director_started"})


@bp.route("/<tenant_id>/pipeline/stop", methods=["POST"])
@login_required
def stop_pipeline(tenant_id):
    """Stop the autonomous Director (and let it wind down agents gracefully)."""
    agent_name = "Pipeline Director"
    with _lock:
        events = _stop_events.get(tenant_id, {})
        event = events.get(agent_name)

    if event:
        event.set()
        return jsonify({"status": "stop_requested"})
    else:
        _mark_agent(tenant_id, agent_name, "idle", "")
        return jsonify({"status": "idle"})


# ── Agent control endpoints ────────────────────────────────────────────────────

@bp.route("/<tenant_id>/agents/start/<agent_name>", methods=["POST"])
@login_required
def start_agent(tenant_id, agent_name):
    if agent_name not in AGENT_REGISTRY:
        return jsonify({"error": f"Unknown agent: {agent_name}"}), 400

    with _lock:
        threads = _agent_threads.setdefault(tenant_id, {})
        if agent_name in threads and threads[agent_name].is_alive():
            return jsonify({"error": "Agent already running"}), 409

        stop_event = threading.Event()
        _stop_events.setdefault(tenant_id, {})[agent_name] = stop_event

        t = threading.Thread(
            target=_run_agent_thread,
            args=(tenant_id, agent_name, stop_event),
            daemon=True,
            name=f"{tenant_id}:{agent_name}"
        )
        threads[agent_name] = t
        t.start()

    return jsonify({"status": "started", "agent": agent_name})


@bp.route("/<tenant_id>/agents/stop/<agent_name>", methods=["POST"])
@login_required
def stop_agent(tenant_id, agent_name):
    with _lock:
        events = _stop_events.get(tenant_id, {})
        event = events.get(agent_name)

    if event:
        event.set()
        return jsonify({"status": "stop_requested", "agent": agent_name})
    else:
        # Agent not running — just mark as idle
        _mark_agent(tenant_id, agent_name, "idle", "")
        return jsonify({"status": "idle", "agent": agent_name})


@bp.route("/<tenant_id>/agents/status")
@login_required
def agents_status(tenant_id):
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
    Sends JSON every 800ms: {"logs": [...], "agents": [...], "stats": {...}}
    """
    if not _is_logged_in():
        ws.close()
        return

    last_id = 0

    # Seed last_id so we don't flood with old data on connect
    db = get_db()
    seed = db.execute(
        "SELECT MAX(id) FROM activity WHERE tenant_id=?", (tenant_id,)
    ).fetchone()[0]
    db.close()
    if seed:
        last_id = seed

    while True:
        try:
            db = get_db()

            # New log rows since last_id
            new_logs = db.execute(
                "SELECT id, agent, message, level, created_at FROM activity WHERE tenant_id=? AND id > ? ORDER BY id LIMIT 50",
                (tenant_id, last_id)
            ).fetchall()

            if new_logs:
                last_id = new_logs[-1]["id"]

            # Agent statuses
            agents = db.execute(
                "SELECT name, status, current_task, layer, color, last_run_at FROM agents WHERE tenant_id=? ORDER BY layer, name",
                (tenant_id,)
            ).fetchall()

            # Quick stats
            kw = db.execute(
                "SELECT COUNT(*) FROM keywords WHERE tenant_id=?", (tenant_id,)
            ).fetchone()[0]
            pg = db.execute(
                """SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status='published' THEN 1 ELSE 0 END) AS published
                   FROM pages WHERE tenant_id=?""",
                (tenant_id,)
            ).fetchone()
            db.close()

            payload = {
                "logs": [
                    {
                        "id":         r["id"],
                        "agent":      r["agent"],
                        "message":    r["message"],
                        "level":      r["level"],
                        "created_at": r["created_at"],
                    }
                    for r in new_logs
                ],
                "agents": [
                    {
                        "name":         a["name"],
                        "status":       a["status"],
                        "current_task": a["current_task"],
                        "layer":        a["layer"],
                        "color":        a["color"],
                        "last_run_at":  a["last_run_at"],
                    }
                    for a in agents
                ],
                "stats": {
                    "keywords":  kw,
                    "pages":     pg["total"]     or 0,
                    "published": pg["published"] or 0,
                },
            }

            ws.send(json.dumps(payload))
            time.sleep(0.8)

        except Exception:
            # Client disconnected or DB error — exit cleanly
            break
