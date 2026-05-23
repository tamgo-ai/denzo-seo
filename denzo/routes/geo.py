"""
GEO Monitoring dashboard — query bank management + citation tracking.
"""
import json
from flask import Blueprint, render_template, request, abort, redirect, url_for
from denzo.auth import tenant_access_required
from denzo.db import get_db

bp = Blueprint("geo", __name__, url_prefix="/clients/<tenant_id>")


def _sidebar_clients():
    db = get_db()
    rows = db.execute("""
        SELECT c.tenant_id, c.name, ag.name AS active_agent_name
        FROM clients c
        LEFT JOIN agents ag ON ag.tenant_id = c.tenant_id AND ag.status = 'working'
        GROUP BY c.tenant_id
        ORDER BY c.name
    """).fetchall()
    clients = [
        {"tenant_id": r["tenant_id"], "name": r["name"], "active_agent": r["active_agent_name"]}
        for r in rows
    ]
    db.close()
    return clients


@bp.route("/geo")
@tenant_access_required
def index(tenant_id):
    db = get_db()
    client = db.execute("SELECT name FROM clients WHERE tenant_id=?", (tenant_id,)).fetchone()
    if not client:
        db.close(); abort(404)

    # Query bank
    queries = db.execute(
        "SELECT id, query, category, active FROM geo_query_bank WHERE tenant_id=? ORDER BY category, id",
        (tenant_id,)
    ).fetchall()

    # Latest result per query per engine
    latest = db.execute(
        """SELECT query, ai_model,
              MAX(checked_at) as last_checked,
              client_mentioned,
              response,
              competitors_mentioned
           FROM geo_queries
           WHERE tenant_id=?
           GROUP BY query, ai_model
           ORDER BY last_checked DESC""",
        (tenant_id,)
    ).fetchall()

    # Citation rate trend (last 14 days, by day)
    trend = db.execute(
        """SELECT DATE(checked_at) as day,
              COUNT(*) as total,
              SUM(client_mentioned) as cited
           FROM geo_queries
           WHERE tenant_id=?
             AND checked_at >= DATE('now', '-14 days')
           GROUP BY DATE(checked_at)
           ORDER BY day""",
        (tenant_id,)
    ).fetchall()

    # Summary stats
    stats = db.execute(
        """SELECT
              COUNT(DISTINCT query) as unique_queries,
              SUM(client_mentioned) as total_cited,
              COUNT(*) as total_checks,
              COUNT(DISTINCT ai_model) as engines_checked
           FROM geo_queries WHERE tenant_id=?""",
        (tenant_id,)
    ).fetchone()

    # Per-query summary (latest result only)
    query_summary = db.execute(
        """SELECT query,
              MAX(checked_at) as last_checked,
              SUM(client_mentioned) as cited_count,
              COUNT(*) as check_count
           FROM geo_queries WHERE tenant_id=?
           GROUP BY query ORDER BY cited_count DESC""",
        (tenant_id,)
    ).fetchall()

    db.close()

    trend_data = [{"day": r["day"], "rate": round(r["cited"] / r["total"] * 100) if r["total"] else 0}
                  for r in trend]

    return render_template(
        "geo/index.html",
        client=dict(client),
        tenant_id=tenant_id,
        active_tenant=tenant_id,
        clients=_sidebar_clients(),
        queries=[dict(q) for q in queries],
        latest=[dict(r) for r in latest],
        trend_data=trend_data,
        stats=dict(stats) if stats else {},
        query_summary=[dict(r) for r in query_summary],
    )


@bp.route("/geo/queries/add", methods=["POST"])
@tenant_access_required
def add_query(tenant_id):
    db = get_db()
    query    = request.form.get("query", "").strip()
    category = request.form.get("category", "general").strip()
    if query:
        try:
            db.execute(
                "INSERT OR IGNORE INTO geo_query_bank (tenant_id, query, category) VALUES (?,?,?)",
                (tenant_id, query, category)
            )
            db.commit()
        except Exception:
            pass
    db.close()
    return redirect(url_for("geo.index", tenant_id=tenant_id))


@bp.route("/geo/queries/<int:query_id>/toggle", methods=["POST"])
@tenant_access_required
def toggle_query(tenant_id, query_id):
    db = get_db()
    db.execute(
        "UPDATE geo_query_bank SET active = CASE WHEN active=1 THEN 0 ELSE 1 END WHERE id=? AND tenant_id=?",
        (query_id, tenant_id)
    )
    db.commit()
    db.close()
    return redirect(url_for("geo.index", tenant_id=tenant_id))


@bp.route("/geo/queries/<int:query_id>/delete", methods=["POST"])
@tenant_access_required
def delete_query(tenant_id, query_id):
    db = get_db()
    db.execute("DELETE FROM geo_query_bank WHERE id=? AND tenant_id=?", (query_id, tenant_id))
    db.commit()
    db.close()
    return redirect(url_for("geo.index", tenant_id=tenant_id))
