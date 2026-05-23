import json
from flask import Blueprint, render_template, flash, redirect, url_for
from denzo.auth import tenant_access_required
from denzo.db import get_db

bp = Blueprint("reviews", __name__, url_prefix="/clients")


@bp.route("/<tenant_id>/reviews")
@tenant_access_required
def index(tenant_id):
    db = get_db()
    client = db.execute("SELECT * FROM clients WHERE tenant_id=?", (tenant_id,)).fetchone()
    if not client:
        db.close()
        flash("Client not found.", "error")
        return redirect(url_for("clients.list_clients"))

    row = db.execute(
        "SELECT value, updated_at FROM settings WHERE tenant_id=? AND key='reviews_intelligence'",
        (tenant_id,)
    ).fetchone()

    report = {}
    report_date = None
    if row:
        try:
            report = json.loads(row["value"])
            report_date = row["updated_at"][:16] if row["updated_at"] else None
        except Exception:
            pass

    # Agent status
    agent_row = db.execute(
        "SELECT status, current_task FROM agents WHERE tenant_id=? AND name='Reviews Intelligence'",
        (tenant_id,)
    ).fetchone()

    clients = db.execute(
        "SELECT c.tenant_id, c.name, ag.name AS active_agent "
        "FROM clients c "
        "LEFT JOIN agents ag ON ag.tenant_id = c.tenant_id AND ag.status = 'working' "
        "GROUP BY c.tenant_id ORDER BY c.name"
    ).fetchall()
    db.close()

    return render_template(
        "reviews/index.html",
        client=dict(client),
        tenant_id=tenant_id,
        has_report=bool(report),
        report_date=report_date,
        report=report,
        pain_points=report.get("competitor_pain_points", []),
        strengths=report.get("competitor_strengths", []),
        content_opps=report.get("content_opportunities", []),
        emotional_triggers=report.get("emotional_triggers", []),
        citation_paragraphs=report.get("citation_paragraphs", []),
        review_themes=report.get("review_themes", []),
        total_reviews=report.get("total_reviews_analyzed", 0),
        competitors_analyzed=report.get("competitors_analyzed", 0),
        source=report.get("source", ""),
        agent_status=dict(agent_row) if agent_row else None,
        clients=clients,
        active_tenant=tenant_id,
    )
