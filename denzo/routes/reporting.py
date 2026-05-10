"""
Reporting blueprint — Executive dashboard and analytics views.
"""
from flask import Blueprint, render_template, abort
from denzo.auth import login_required, can_access_tenant
from denzo.db import get_db

bp = Blueprint("reporting", __name__, url_prefix="/clients/<tenant_id>")


@bp.route("/executive-dashboard")
@login_required
def executive_dashboard(tenant_id):
    if not can_access_tenant(tenant_id):
        abort(403)

    db = get_db()

    # Client info
    client = db.execute("SELECT * FROM clients WHERE tenant_id=?", (tenant_id,)).fetchone()
    if not client:
        db.close()
        abort(404)

    # KPI: pages
    page_stats = db.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status='published' THEN 1 ELSE 0 END) as published,
            SUM(CASE WHEN status='ready'     THEN 1 ELSE 0 END) as ready,
            SUM(CASE WHEN status='draft'     THEN 1 ELSE 0 END) as draft,
            ROUND(AVG(CASE WHEN quality_score IS NOT NULL AND quality_score > 0
                          THEN quality_score END), 1) as avg_score
        FROM pages WHERE tenant_id=?
    """, (tenant_id,)).fetchone()

    # KPI: keywords
    kw_stats = db.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN priority IN ('high','alta') THEN 1 ELSE 0 END) as high_priority
        FROM keywords WHERE tenant_id=?
    """, (tenant_id,)).fetchone()

    # Page distribution by type (published only)
    type_rows = db.execute("""
        SELECT type, COUNT(*) as cnt FROM pages
        WHERE tenant_id=? AND status='published'
        GROUP BY type ORDER BY cnt DESC
    """, (tenant_id,)).fetchall()
    page_distribution = {(r["type"] or "page"): r["cnt"] for r in type_rows}

    # GEO citation rate — graceful fallback if table missing
    try:
        geo_stats = db.execute("""
            SELECT COUNT(*) as total_queries,
                   SUM(CASE WHEN client_mentioned=1 THEN 1 ELSE 0 END) as cited_count
            FROM geo_queries WHERE tenant_id=?
        """, (tenant_id,)).fetchone()
        geo_total = geo_stats["total_queries"] or 0
        geo_cited = geo_stats["cited_count"]   or 0
    except Exception:
        geo_total = 0
        geo_cited = 0

    geo_rate = round(geo_cited / geo_total * 100) if geo_total > 0 else 0

    # Top pages by quality score
    top_pages = db.execute("""
        SELECT title, slug, type, target_keyword, quality_score, status, publish_url
        FROM pages WHERE tenant_id=? AND quality_score IS NOT NULL AND quality_score > 0
        ORDER BY quality_score DESC LIMIT 10
    """, (tenant_id,)).fetchall()

    # Recent published pages
    recent_pages = db.execute("""
        SELECT title, slug, type, updated_at, publish_url
        FROM pages WHERE tenant_id=? AND status='published'
        ORDER BY updated_at DESC LIMIT 5
    """, (tenant_id,)).fetchall()

    # Pipeline status from agents table
    agent_rows = db.execute("""
        SELECT name, status, current_task, last_run_at FROM agents
        WHERE tenant_id=? ORDER BY layer, name
    """, (tenant_id,)).fetchall()

    working_agents = [a["name"] for a in agent_rows if a["status"] == "working"]
    error_agents   = [a["name"] for a in agent_rows if a["status"] == "error"]

    if working_agents:
        pipeline_status = "running"
    elif error_agents:
        pipeline_status = "needs_attention"
    elif (page_stats["published"] or 0) > 0:
        pipeline_status = "complete"
    else:
        pipeline_status = "setup"

    # Action items
    action_items = []
    avg_score = page_stats["avg_score"] or 0
    if avg_score and avg_score < 70 and (page_stats["published"] or 0) > 0:
        action_items.append(
            f"Content quality below target ({avg_score}/100) — run Content Optimizer"
        )
    if geo_total == 0:
        action_items.append(
            "GEO monitoring not started — configure API keys and run GEO Monitor"
        )
    elif geo_rate < 30:
        action_items.append(
            f"Low AI citation rate ({geo_rate}%) — run GEO Optimizer to improve"
        )
    if error_agents:
        action_items.append(
            f"Agents in error state: {', '.join(error_agents)} — check pipeline logs"
        )
    if (kw_stats["total"] or 0) < 20:
        action_items.append(
            "Low keyword count — run Keyword Strategist to discover more opportunities"
        )

    db.close()

    return render_template(
        "clients/executive_dashboard.html",
        client=dict(client),
        tenant_id=tenant_id,
        page_stats={
            "total":     page_stats["total"]     or 0,
            "published": page_stats["published"] or 0,
            "ready":     page_stats["ready"]     or 0,
            "draft":     page_stats["draft"]     or 0,
            "avg_score": avg_score,
        },
        kw_stats={
            "total":        kw_stats["total"]        or 0,
            "high_priority": kw_stats["high_priority"] or 0,
        },
        page_distribution=page_distribution,
        total_pages=page_stats["total"] or 0,
        avg_score=avg_score,
        geo_citation_rate=geo_rate,
        geo_citations_count=geo_cited,
        geo_total_queries=geo_total,
        top_pages=[dict(p) for p in top_pages],
        recent_pages=[dict(p) for p in recent_pages],
        pipeline_status=pipeline_status,
        working_agents=working_agents,
        action_items=action_items,
        active_nav="executive_dashboard",
    )
