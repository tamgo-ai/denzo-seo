import csv
import io
from flask import Blueprint, render_template, request, abort, Response
from denzo.auth import login_required
from denzo.db import get_db

bp = Blueprint("keywords", __name__, url_prefix="/clients/<tenant_id>")

PAGE_SIZE = 50


def _get_sidebar_clients():
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


@bp.route("/keywords")
@login_required
def index(tenant_id):
    db = get_db()

    client = db.execute(
        "SELECT name FROM clients WHERE tenant_id=?", (tenant_id,)
    ).fetchone()
    if not client:
        db.close()
        abort(404)

    # Filters
    category = request.args.get("category", "").strip()
    location = request.args.get("location", "").strip()
    priority = request.args.get("priority", "").strip()
    q        = request.args.get("q", "").strip()
    page     = max(1, int(request.args.get("page", 1)))

    conditions = ["tenant_id = ?"]
    params = [tenant_id]

    if category:
        conditions.append("category = ?")
        params.append(category)
    if location:
        conditions.append("location LIKE ?")
        params.append(f"%{location}%")
    if priority:
        conditions.append("priority = ?")
        params.append(priority)
    if q:
        conditions.append("keyword LIKE ?")
        params.append(f"%{q}%")

    where = " AND ".join(conditions)

    total = db.execute(
        f"SELECT COUNT(*) FROM keywords WHERE {where}", params
    ).fetchone()[0]

    offset = (page - 1) * PAGE_SIZE
    keywords = db.execute(
        f"SELECT * FROM keywords WHERE {where} ORDER BY priority DESC, volume DESC LIMIT ? OFFSET ?",
        params + [PAGE_SIZE, offset]
    ).fetchall()

    # Distinct categories for filter dropdown
    categories = db.execute(
        "SELECT DISTINCT category FROM keywords WHERE tenant_id=? AND category IS NOT NULL ORDER BY category",
        (tenant_id,)
    ).fetchall()

    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    clients = _get_sidebar_clients()
    db.close()

    return render_template(
        "keywords/index.html",
        keywords=[dict(k) for k in keywords],
        client=dict(client),
        tenant_id=tenant_id,
        active_tenant=tenant_id,
        clients=clients,
        total=total,
        page=page,
        total_pages=total_pages,
        categories=[r["category"] for r in categories],
        filters={"category": category, "location": location, "priority": priority, "q": q},
    )


@bp.route("/keywords/export.csv")
@login_required
def export_keywords_csv(tenant_id):
    db = get_db()
    client = db.execute("SELECT name FROM clients WHERE tenant_id=?", (tenant_id,)).fetchone()
    if not client:
        db.close()
        abort(404)

    category = request.args.get("category", "").strip()
    location = request.args.get("location", "").strip()
    priority = request.args.get("priority", "").strip()
    q        = request.args.get("q", "").strip()

    conditions = ["tenant_id = ?"]
    params = [tenant_id]
    if category:
        conditions.append("category = ?"); params.append(category)
    if location:
        conditions.append("location LIKE ?"); params.append(f"%{location}%")
    if priority:
        conditions.append("priority = ?"); params.append(priority)
    if q:
        conditions.append("keyword LIKE ?"); params.append(f"%{q}%")

    rows = db.execute(
        f"SELECT keyword, volume, difficulty, intent, location, category, priority "
        f"FROM keywords WHERE {' AND '.join(conditions)} ORDER BY priority DESC, volume DESC",
        params
    ).fetchall()
    db.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["keyword", "volume", "difficulty", "intent", "location", "category", "priority"])
    for r in rows:
        writer.writerow([r["keyword"], r["volume"], r["difficulty"], r["intent"],
                         r["location"], r["category"], r["priority"]])

    filename = f"{tenant_id}-keywords.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
