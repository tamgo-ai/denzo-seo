"""
Brand Voice DNA — per-client personality system.
Stores brand voice configuration as JSON in the settings table.
"""
import json
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from denzo.auth import tenant_access_required
from denzo.db import get_db

bp = Blueprint("brand_voice", __name__, url_prefix="/clients/<tenant_id>/brand-voice")


@bp.route("/", methods=["GET", "POST"])
@tenant_access_required
def index(tenant_id):
    db = get_db()

    # Verify client exists
    client = db.execute(
        "SELECT * FROM clients WHERE tenant_id=?", (tenant_id,)
    ).fetchone()
    if not client:
        db.close()
        flash("Client not found.", "error")
        return redirect(url_for("clients.list_clients"))

    saved = False

    if request.method == "POST":
        brand_voice = {
            "brand_name":           request.form.get("brand_name", "").strip(),
            "founder_name":         request.form.get("founder_name", "").strip(),
            "years_experience":     request.form.get("years_experience", "").strip(),
            "clients_served":       request.form.get("clients_served", "").strip(),
            "key_insight_1":        request.form.get("key_insight_1", "").strip(),
            "key_insight_2":        request.form.get("key_insight_2", "").strip(),
            "key_insight_3":        request.form.get("key_insight_3", "").strip(),
            "contrarian_position":  request.form.get("contrarian_position", "").strip(),
            "writing_style":        request.form.get("writing_style", "professional").strip(),
            "phrases_to_use":       request.form.get("phrases_to_use", "").strip(),
            "phrases_to_avoid":     request.form.get("phrases_to_avoid", "").strip(),
            "example_intro":        request.form.get("example_intro", "").strip(),
        }

        db.execute(
            "INSERT OR REPLACE INTO settings (tenant_id, key, value, updated_at) "
            "VALUES (?, 'brand_voice', ?, CURRENT_TIMESTAMP)",
            (tenant_id, json.dumps(brand_voice))
        )
        db.commit()
        saved = True
        flash("Brand Voice DNA saved successfully.", "success")

    # Load current values
    row = db.execute(
        "SELECT value FROM settings WHERE tenant_id=? AND key='brand_voice'",
        (tenant_id,)
    ).fetchone()

    brand_voice = {}
    if row:
        try:
            brand_voice = json.loads(row["value"])
        except Exception:
            pass

    # Load all clients for sidebar
    clients = db.execute(
        "SELECT c.tenant_id, c.name, ag.name AS active_agent "
        "FROM clients c "
        "LEFT JOIN agents ag ON ag.tenant_id = c.tenant_id AND ag.status = 'working' "
        "GROUP BY c.tenant_id ORDER BY c.name"
    ).fetchall()

    db.close()

    return render_template(
        "brand_voice/index.html",
        client=client,
        tenant_id=tenant_id,
        brand_voice=brand_voice,
        saved=saved,
        clients=clients,
        active_tenant=tenant_id,
    )
