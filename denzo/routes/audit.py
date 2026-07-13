import json
from flask import Blueprint, render_template, flash, redirect, url_for, Response
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

    # Try deep audit first, fall back to legacy technical_audit
    setting = db.execute(
        "SELECT value, updated_at FROM settings WHERE tenant_id=? AND key='audit_deep'",
        (tenant_id,)
    ).fetchone()
    if not setting:
        setting = db.execute(
            "SELECT value, updated_at FROM settings WHERE tenant_id=? AND key='technical_audit'",
            (tenant_id,)
        ).fetchone()

    # Also load Lighthouse data if available
    lighthouse_setting = db.execute(
        "SELECT value FROM settings WHERE tenant_id=? AND key='lighthouse_report'",
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

    lighthouse_data = None
    if lighthouse_setting:
        try:
            lighthouse_data = json.loads(lighthouse_setting["value"])
        except Exception:
            pass

    return render_template(
        "audit/index.html",
        client=client,
        tenant_id=tenant_id,
        audit=audit_data,
        updated_at=updated_at,
        lighthouse=lighthouse_data,
        clients=clients,
        active_tenant=tenant_id,
    )


@bp.route("/<tenant_id>/audit/llms.txt")
@tenant_access_required
def download_llms(tenant_id):
    """Download generated llms.txt from the latest audit."""
    db = get_db()
    setting = db.execute(
        "SELECT value FROM settings WHERE tenant_id=? AND key='audit_deep'",
        (tenant_id,)
    ).fetchone()
    db.close()

    if not setting:
        return "No audit found", 404

    try:
        audit = json.loads(setting["value"])
        llms_gen = audit.get("llms_generated", {})
        llms_txt = llms_gen.get("llms_txt", "")
        if not llms_txt:
            return "No llms.txt generated for this audit", 404
        domain = audit.get("url", "site").split("://")[1].split("/")[0].replace("www.", "")
        return Response(llms_txt, mimetype="text/plain",
                        headers={"Content-Disposition": f'attachment; filename="llms-{domain}.txt"'})
    except Exception:
        return "Error reading audit data", 500
