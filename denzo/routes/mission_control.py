"""
Mission Control — premium real-time view of the 26-agent SEO pipeline.

Shows the constellation of agents organized by layer, live activity feed,
inter-agent data flow, and KPI strip. Polls /api/<tenant>/mission-control/state
every 2 seconds for fresh state.
"""
import json

from flask import Blueprint, render_template, jsonify, abort

from denzo.auth import login_required, can_access_tenant
from denzo.db import get_db
from denzo.agents.registry import AGENT_REGISTRY, LAYER_LABELS


bp = Blueprint("mission_control", __name__, url_prefix="/clients/<tenant_id>/mission-control")


# Agents grouped by layer, in their canonical render order.
def _agents_by_layer() -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {i: [] for i in LAYER_LABELS.keys()}
    for name, (mod, cls, layer, color) in AGENT_REGISTRY.items():
        out[layer].append({"name": name, "color": color})
    return out


# Edges: which agent feeds which next agent. Drawn as SVG paths in the UI.
# Director (Layer 0) connects to ALL agents. Within layers, we draw simple
# layer-to-layer aggregate links.
DEPENDENCY_EDGES = [
    # Layer 1 → Layer 2 (Intelligence feeds Strategy)
    ("Keyword Strategist",        "E-E-A-T Architect"),
    ("Keyword Strategist",        "Vertical Matrix Generator"),
    ("Competitor Intel",          "E-E-A-T Architect"),
    ("Keyword Clusterer",         "Vertical Matrix Generator"),
    # Layer 2 → Layer 3
    ("E-E-A-T Architect",         "Programmatic SEO"),
    ("Schema Engineer",           "Programmatic SEO"),
    ("Vertical Matrix Generator", "Programmatic SEO"),
    # Layer 3 → Layer 4
    ("Programmatic SEO",          "Content Optimizer"),
    ("Programmatic SEO",          "GEO Optimizer"),
    ("Programmatic SEO",          "Internal Linker"),
    ("Programmatic SEO",          "Visual Content Optimizer"),
    # Layer 4 → Layer 5
    ("Content Optimizer",         "GitHub Publisher"),
    ("Content Optimizer",         "WordPress Publisher"),
    ("Internal Linker",           "GitHub Publisher"),
    ("Internal Linker",           "WordPress Publisher"),
    ("GEO Optimizer",             "GitHub Publisher"),
    # Layer 5 → Layer 6
    ("GitHub Publisher",          "Rank Tracker"),
    ("WordPress Publisher",       "Rank Tracker"),
    ("GitHub Publisher",          "GEO Monitor"),
    ("GBP Optimizer",             "Reviews Intelligence"),
    ("Rank Tracker",              "ROI Attribution"),
    ("GEO Monitor",               "ROI Attribution"),
    ("SERP Intelligence",         "ROI Attribution"),
]


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

    agent_colors = {name: color for name, (_, _, _, color) in AGENT_REGISTRY.items()}

    return render_template(
        "clients/mission_control.html",
        client=client,
        layer_labels=LAYER_LABELS,
        agents_by_layer=_agents_by_layer(),
        dependency_edges=DEPENDENCY_EDGES,
        agent_colors_json=json.dumps(agent_colors),
        active_tenant=tenant_id,
    )


@bp.route("/state")
@login_required
def state(tenant_id):
    """JSON state poll — agents + recent activity + KPIs + active links."""
    if not can_access_tenant(tenant_id):
        abort(403)

    db = get_db()

    # Agents — pick the columns that always exist; layer/color may be null on legacy rows
    agent_rows = db.execute("""
        SELECT name, layer, color, status, current_task, last_message, last_run_at
        FROM agents WHERE tenant_id=?
    """, (tenant_id,)).fetchall()
    agents = [dict(r) for r in agent_rows]

    # Activity (last 60 entries)
    activity_rows = db.execute("""
        SELECT type, agent, message, level, created_at
        FROM activity WHERE tenant_id=?
        ORDER BY id DESC LIMIT 60
    """, (tenant_id,)).fetchall()
    activity = [dict(r) for r in activity_rows]

    # KPIs
    kpis = {}
    kpis["pages_total"]      = (db.execute("SELECT COUNT(*) AS n FROM pages WHERE tenant_id=?", (tenant_id,)).fetchone() or {})["n"] or 0
    kpis["pages_published"]  = (db.execute("SELECT COUNT(*) AS n FROM pages WHERE tenant_id=? AND status='published'", (tenant_id,)).fetchone() or {})["n"] or 0
    kpis["pages_ready"]      = (db.execute("SELECT COUNT(*) AS n FROM pages WHERE tenant_id=? AND status='ready'", (tenant_id,)).fetchone() or {})["n"] or 0
    kpis["keywords_total"]   = (db.execute("SELECT COUNT(*) AS n FROM keywords WHERE tenant_id=?", (tenant_id,)).fetchone() or {})["n"] or 0
    kpis["keywords_high"]    = (db.execute("SELECT COUNT(*) AS n FROM keywords WHERE tenant_id=? AND priority='high'", (tenant_id,)).fetchone() or {})["n"] or 0
    kpis["competitors"]      = (db.execute("SELECT COUNT(*) AS n FROM competitors WHERE tenant_id=?", (tenant_id,)).fetchone() or {})["n"] or 0
    geo_row = db.execute(
        "SELECT COUNT(*) AS total, SUM(client_mentioned) AS cited FROM geo_queries WHERE tenant_id=?",
        (tenant_id,),
    ).fetchone()
    kpis["geo_citations"]     = int((geo_row["cited"] if geo_row else 0) or 0)
    kpis["geo_queries_total"] = int((geo_row["total"] if geo_row else 0) or 0)

    # Active inter-agent flows: show edges where SOURCE is "done" and TARGET is "working".
    # That's the moment data is flowing from one to the other.
    status_by_name = {a["name"]: a["status"] for a in agents}
    active_flows = []
    for src, dst in DEPENDENCY_EDGES:
        s_status = status_by_name.get(src)
        d_status = status_by_name.get(dst)
        if s_status == "done" and d_status == "working":
            active_flows.append({"from": src, "to": dst})

    # Working agents — for the "currently in flight" count
    in_progress = [a for a in agents if a["status"] == "working"]

    db.close()

    return jsonify({
        "agents": agents,
        "activity": activity,
        "kpis": kpis,
        "active_flows": active_flows,
        "in_progress_count": len(in_progress),
    })
