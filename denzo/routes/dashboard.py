from flask import Blueprint, render_template
from denzo.auth import login_required
from denzo.db import get_db

bp = Blueprint("dashboard", __name__, url_prefix="/app")


def _get_all_clients():
    db = get_db()
    rows = db.execute("""
        SELECT c.tenant_id, c.name, c.business_type, c.website_url, c.status,
               c.created_at,
               COALESCE(k.keyword_count, 0)    AS keyword_count,
               COALESCE(p.page_count, 0)       AS page_count,
               COALESCE(p.published_count, 0)  AS published_count,
               a.last_activity,
               ag.name                         AS active_agent_name,
               ag.current_task                 AS active_agent_task
        FROM clients c
        LEFT JOIN (
            SELECT tenant_id, COUNT(*) AS keyword_count
            FROM keywords GROUP BY tenant_id
        ) k  ON k.tenant_id = c.tenant_id
        LEFT JOIN (
            SELECT tenant_id,
                   COUNT(*) AS page_count,
                   SUM(CASE WHEN status='published' THEN 1 ELSE 0 END) AS published_count
            FROM pages GROUP BY tenant_id
        ) p  ON p.tenant_id = c.tenant_id
        LEFT JOIN (
            SELECT tenant_id, MAX(created_at) AS last_activity
            FROM activity GROUP BY tenant_id
        ) a  ON a.tenant_id = c.tenant_id
        LEFT JOIN agents ag ON ag.tenant_id = c.tenant_id AND ag.status = 'working'
        ORDER BY c.name
    """).fetchall()

    clients = []
    for r in rows:
        row = dict(r)
        row["active_agent"] = (
            {"name": r["active_agent_name"], "current_task": r["active_agent_task"]}
            if r["active_agent_name"] else None
        )
        clients.append(row)

    db.close()
    return clients


@bp.route("/")
@login_required
def index():
    clients = _get_all_clients()
    return render_template("dashboard/index.html", clients=clients)
