import csv
import io
import json
from flask import Blueprint, render_template, request, abort, Response, jsonify
from denzo.auth import login_required, can_access_tenant
from denzo.db import get_db

bp = Blueprint("competitors", __name__, url_prefix="/clients/<tenant_id>")


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


def _parse_competitor(r: dict) -> dict:
    """Parse a competitor row — decode JSON fields and set defaults."""
    d = dict(r)

    def jlist(val):
        try:
            v = json.loads(val) if val else []
            return v if isinstance(v, list) else [v]
        except Exception:
            return [val] if val else []

    d["strengths_list"]       = jlist(d.get("strengths"))
    d["weaknesses_list"]      = jlist(d.get("weaknesses"))
    d["certified_brands_list"]= jlist(d.get("certified_brands"))
    d["gap_cities_list"]      = jlist(d.get("gap_cities"))
    d["gap_keywords_list"]    = jlist(d.get("gap_keywords_json"))
    d["tier"]                 = d.get("tier") or 2
    d["competitor_score"]     = round(d.get("competitor_score") or 0.0, 1)
    d["discovery_method"]     = d.get("discovery_method") or "manual"
    return d


@bp.route("/competitors")
@login_required
def index(tenant_id):
    if not can_access_tenant(tenant_id):
        abort(403)
    # Pagination query params
    comp_page = request.args.get('cp', 1, type=int)    # competitor (tier2) page
    can_page  = request.args.get('canp', 1, type=int)  # cannibalization page
    PER_PAGE_COMP = 10
    PER_PAGE_CAN  = 20

    db = get_db()
    client = db.execute(
        "SELECT name FROM clients WHERE tenant_id=?", (tenant_id,)
    ).fetchone()
    if not client:
        db.close()
        abort(404)

    rows = db.execute(
        # tier=0 means 'different industry' — exclude from display
        "SELECT * FROM competitors WHERE tenant_id=? AND (tier IS NULL OR tier != 0) ORDER BY tier ASC, competitor_score DESC, created_at DESC",
        (tenant_id,)
    ).fetchall()

    # Parse JSON fields
    competitors = [_parse_competitor(dict(r)) for r in rows]
    tier1 = [c for c in competitors if c["tier"] == 1]
    tier2_all = [c for c in competitors if c["tier"] == 2]

    # Paginate tier2 (tier1 is usually small — don't paginate)
    tier2_total = len(tier2_all)
    tier2_pages = (tier2_total + PER_PAGE_COMP - 1) // PER_PAGE_COMP if tier2_total else 1
    tier2_page  = max(1, min(comp_page, tier2_pages))
    tier2_start = (tier2_page - 1) * PER_PAGE_COMP
    tier2       = tier2_all[tier2_start : tier2_start + PER_PAGE_COMP]

    # Count filtered-out (different-industry) competitors for transparency
    filtered_count = db.execute(
        "SELECT COUNT(*) FROM competitors WHERE tenant_id=? AND tier=0", (tenant_id,)
    ).fetchone()[0]

    # Gap keywords count
    gap_keywords = db.execute(
        "SELECT COUNT(*) FROM keywords WHERE tenant_id=? AND category='competitor_gap'",
        (tenant_id,)
    ).fetchone()[0]

    # Cannibalization risks (all, then paginate)
    cannibal_rows = db.execute(
        """SELECT * FROM cannibalization_risks
           WHERE tenant_id=? AND resolved=0
           ORDER BY CASE risk_level WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END""",
        (tenant_id,)
    ).fetchall() if _table_exists(db, "cannibalization_risks") else []
    cannibal_risks_all = [dict(r) for r in cannibal_rows]

    # Paginate cannibalization risks
    can_total  = len(cannibal_risks_all)
    can_pages  = (can_total + PER_PAGE_CAN - 1) // PER_PAGE_CAN if can_total else 1
    can_page   = max(1, min(can_page, can_pages))
    can_start  = (can_page - 1) * PER_PAGE_CAN
    can_paged  = cannibal_risks_all[can_start : can_start + PER_PAGE_CAN]

    # Geo discovery stats
    geo_discovered = sum(1 for c in competitors if c.get("discovery_method") == "geo_radius")

    clients = _get_sidebar_clients()
    db.close()

    return render_template(
        "competitors/index.html",
        competitors=competitors,
        tier1=tier1,
        tier2=tier2,
        tier2_total=tier2_total,
        tier2_pages=tier2_pages,
        tier2_page=tier2_page,
        client=dict(client),
        tenant_id=tenant_id,
        active_tenant=tenant_id,
        clients=clients,
        gap_keywords=gap_keywords,
        cannibal_risks=cannibal_risks_all,   # full list kept for stats bar
        can_paged=can_paged,
        can_total=can_total,
        can_pages=can_pages,
        can_page=can_page,
        geo_discovered=geo_discovered,
        filtered_count=filtered_count,
    )


@bp.route("/competitors/<int:competitor_id>/resolve-cannibalization", methods=["POST"])
@login_required
def resolve_cannibalization(tenant_id, competitor_id):
    """Mark a cannibalization risk as resolved."""
    db = get_db()
    db.execute(
        "UPDATE cannibalization_risks SET resolved=1 WHERE id=? AND tenant_id=?",
        (competitor_id, tenant_id)
    )
    db.commit()
    db.close()
    return jsonify({"ok": True})


@bp.route("/competitors/export.csv")
@login_required
def export_competitors_csv(tenant_id):
    db = get_db()
    client = db.execute("SELECT name FROM clients WHERE tenant_id=?", (tenant_id,)).fetchone()
    if not client:
        db.close()
        abort(404)

    rows = db.execute(
        """SELECT name, url, location, tier, competitor_score, certified_brands,
                  strengths, weaknesses, notes, gap_cities, discovery_method
           FROM competitors
           WHERE tenant_id=? ORDER BY tier ASC, competitor_score DESC""",
        (tenant_id,)
    ).fetchall()
    db.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["name", "url", "location", "tier", "score",
                     "certified_brands", "strengths", "weaknesses",
                     "notes", "gap_cities", "discovery_method"])
    for r in rows:
        writer.writerow([
            r["name"], r["url"], r["location"],
            r["tier"], r["competitor_score"],
            r["certified_brands"], r["strengths"],
            r["weaknesses"], r["notes"],
            r["gap_cities"], r["discovery_method"],
        ])

    filename = f"{tenant_id}-competitors.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


def _table_exists(db, table_name: str) -> bool:
    """Check if a table exists in the SQLite database."""
    row = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
    ).fetchone()
    return row is not None
