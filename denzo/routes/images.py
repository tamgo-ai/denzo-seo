import json
from flask import Blueprint, render_template, flash, redirect, url_for
from denzo.auth import login_required
from denzo.db import get_db

bp = Blueprint("images", __name__, url_prefix="/clients")


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


@bp.route("/<tenant_id>/images")
@login_required
def images(tenant_id):
    db = get_db()
    client = db.execute("SELECT * FROM clients WHERE tenant_id=?", (tenant_id,)).fetchone()
    if not client:
        db.close()
        flash("Client not found.", "error")
        return redirect(url_for("clients.list_clients"))

    # Load from site_images table
    rows = db.execute(
        """SELECT id, url, alt, width, height, context, description, tags, suitable_for, analyzed, created_at
           FROM site_images WHERE tenant_id=? ORDER BY analyzed DESC, context, id""",
        (tenant_id,)
    ).fetchall()

    total    = len(rows)
    analyzed = sum(1 for r in rows if r["analyzed"])

    # Parse JSON fields and build image list
    site_images = []
    for r in rows:
        img = dict(r)
        try:
            img["tags_list"]        = json.loads(img["tags"] or "[]")
        except Exception:
            img["tags_list"]        = []
        try:
            img["suitable_for_list"] = json.loads(img["suitable_for"] or "[]")
        except Exception:
            img["suitable_for_list"] = []
        site_images.append(img)

    # Collect unique contexts for filter tabs
    contexts = sorted(set(r["context"] for r in rows))

    clients = _get_all_clients_slim()
    db.close()

    return render_template(
        "images/index.html",
        client=dict(client),
        tenant_id=tenant_id,
        site_images=site_images,
        total=total,
        analyzed=analyzed,
        contexts=contexts,
        clients=clients,
        active_tenant=tenant_id,
    )
