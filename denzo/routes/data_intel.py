import json
from flask import Blueprint, render_template, flash, redirect, url_for
from denzo.auth import login_required
from denzo.db import get_db

bp = Blueprint("data_intel", __name__, url_prefix="/clients")


@bp.route("/<tenant_id>/data-intel")
@login_required
def index(tenant_id):
    db = get_db()
    client = db.execute("SELECT * FROM clients WHERE tenant_id=?", (tenant_id,)).fetchone()
    if not client:
        db.close()
        flash("Client not found.", "error")
        return redirect(url_for("clients.list_clients"))

    # Load Data Intelligence report
    row = db.execute(
        "SELECT value, updated_at FROM settings WHERE tenant_id=? AND key='data_intelligence_report'",
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

    data_stories     = report.get("data_stories", [])
    pain_points      = report.get("pain_points", [])
    citation_bait    = report.get("citation_bait_paragraphs", [])
    suggested_titles = report.get("suggested_titles", [])

    # Sidebar clients
    clients = db.execute(
        "SELECT c.tenant_id, c.name, ag.name AS active_agent "
        "FROM clients c "
        "LEFT JOIN agents ag ON ag.tenant_id = c.tenant_id AND ag.status = 'working' "
        "GROUP BY c.tenant_id ORDER BY c.name"
    ).fetchall()
    db.close()

    return render_template(
        "data_intel/index.html",
        client=dict(client),
        tenant_id=tenant_id,
        has_report=bool(report),
        report_date=report_date,
        data_stories=data_stories,
        pain_points=pain_points,
        citation_bait=citation_bait,
        suggested_titles=suggested_titles,
        clients=clients,
        active_tenant=tenant_id,
    )
