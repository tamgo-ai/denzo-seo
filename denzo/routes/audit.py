import json
from flask import Blueprint, render_template, flash, redirect, url_for
from denzo.auth import tenant_access_required
from denzo.db import get_db

bp = Blueprint("audit", __name__, url_prefix="/clients")


def _get_all_clients_slim():
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


@bp.route("/<tenant_id>/audit")
@tenant_access_required
def audit(tenant_id):
    db = get_db()
    client = db.execute("SELECT * FROM clients WHERE tenant_id=?", (tenant_id,)).fetchone()
    if not client:
        db.close()
        flash("Client not found.", "error")
        return redirect(url_for("clients.list_clients"))

    setting = db.execute(
        "SELECT value, updated_at FROM settings WHERE tenant_id=? AND key='technical_audit'",
        (tenant_id,)
    ).fetchone()
    clients = _get_all_clients_slim()
    db.close()

    audit_data = None
    updated_at = None
    if setting:
        updated_at = setting["updated_at"]
        try:
            audit_data = json.loads(setting["value"])
        except Exception:
            audit_data = None

    return render_template(
        "audit/index.html",
        client=client,
        tenant_id=tenant_id,
        audit=audit_data,
        updated_at=updated_at,
        clients=clients,
        active_tenant=tenant_id,
    )
