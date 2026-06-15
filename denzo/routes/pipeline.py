from flask import Blueprint, render_template, abort
from denzo.auth import login_required, can_access_tenant
from denzo.db import get_db
from denzo.agents.registry import LAYER_LABELS
from denzo.routes.competitors import _table_exists

bp = Blueprint("pipeline", __name__, url_prefix="/clients/<tenant_id>")


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


@bp.route("/pipeline")
@login_required
def index(tenant_id):
    if not can_access_tenant(tenant_id):
        abort(403)
    db = get_db()

    client = db.execute(
        "SELECT * FROM clients WHERE tenant_id=?", (tenant_id,)
    ).fetchone()

    if not client:
        db.close()
        abort(404)

    # Agents grouped by layer
    agent_rows = db.execute(
        "SELECT * FROM agents WHERE tenant_id=? ORDER BY layer, name",
        (tenant_id,)
    ).fetchall()

    layers = {}
    for a in agent_rows:
        layer_num = a["layer"]
        if layer_num not in layers:
            layers[layer_num] = {
                "label": LAYER_LABELS.get(layer_num, f"Layer {layer_num}"),
                "agents": []
            }
        layers[layer_num]["agents"].append(dict(a))

    # Activity log — last 50 (convert UTC → Pacific)
    from datetime import datetime, timezone, timedelta
    PDT = timezone(timedelta(hours=-7))
    activity = db.execute(
        "SELECT * FROM activity WHERE tenant_id=? ORDER BY id DESC LIMIT 50",
        (tenant_id,)
    ).fetchall()
    # Convert timestamps to Pacific time for display
    for a in activity:
        try:
            dt = datetime.strptime(a["created_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            a["created_at"] = dt.astimezone(PDT).strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            pass

    # Stats
    kw_count = db.execute(
        "SELECT COUNT(*) FROM keywords WHERE tenant_id=?", (tenant_id,)
    ).fetchone()[0]
    page_counts = db.execute(
        """SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status='draft'     THEN 1 ELSE 0 END) AS draft,
            SUM(CASE WHEN status='ready'     THEN 1 ELSE 0 END) AS ready,
            SUM(CASE WHEN status='published' THEN 1 ELSE 0 END) AS published
           FROM pages WHERE tenant_id=?""",
        (tenant_id,)
    ).fetchone()

    # ── Keyword intelligence ───────────────────────────────────────────────
    kw_by_priority = {r["priority"]: r["cnt"] for r in db.execute(
        "SELECT priority, COUNT(*) as cnt FROM keywords WHERE tenant_id=? GROUP BY priority", (tenant_id,)
    ).fetchall()}

    kw_by_category = {r["category"]: r["cnt"] for r in db.execute(
        "SELECT category, COUNT(*) as cnt FROM keywords WHERE tenant_id=? GROUP BY category ORDER BY cnt DESC LIMIT 8", (tenant_id,)
    ).fetchall()}

    kw_by_difficulty = {r["difficulty"]: r["cnt"] for r in db.execute(
        "SELECT difficulty, COUNT(*) as cnt FROM keywords WHERE tenant_id=? GROUP BY difficulty", (tenant_id,)
    ).fetchall()}

    top_keywords = db.execute(
        """SELECT keyword, volume, priority, category, location
           FROM keywords WHERE tenant_id=? AND volume != '' AND volume IS NOT NULL
           ORDER BY CAST(REPLACE(volume, ',', '') AS INTEGER) DESC LIMIT 8""",
        (tenant_id,)
    ).fetchall()

    # ── Page intelligence ──────────────────────────────────────────────────
    pages_by_type = {r["type"]: r["cnt"] for r in db.execute(
        "SELECT type, COUNT(*) as cnt FROM pages WHERE tenant_id=? GROUP BY type ORDER BY cnt DESC", (tenant_id,)
    ).fetchall()}

    pages_by_status = {r["status"]: r["cnt"] for r in db.execute(
        "SELECT status, COUNT(*) as cnt FROM pages WHERE tenant_id=? GROUP BY status", (tenant_id,)
    ).fetchall()}

    avg_quality = db.execute(
        "SELECT ROUND(AVG(quality_score), 1) FROM pages WHERE tenant_id=? AND quality_score > 0", (tenant_id,)
    ).fetchone()[0] or 0

    # ── Competitor intelligence ────────────────────────────────────────────
    comp_stats = {r["tier"]: r["cnt"] for r in db.execute(
        "SELECT tier, COUNT(*) as cnt FROM competitors WHERE tenant_id=? AND (tier IS NULL OR tier != 0) GROUP BY tier", (tenant_id,)
    ).fetchall()}

    tier1_names = [r["name"] for r in db.execute(
        "SELECT name FROM competitors WHERE tenant_id=? AND tier=1 ORDER BY competitor_score DESC LIMIT 5", (tenant_id,)
    ).fetchall()]

    gap_kw_count = db.execute(
        "SELECT COUNT(*) FROM keywords WHERE tenant_id=? AND category='competitor_gap'", (tenant_id,)
    ).fetchone()[0]

    # ── Pipeline health ────────────────────────────────────────────────────
    last_runs = db.execute(
        "SELECT * FROM pipeline_runs WHERE tenant_id=? ORDER BY started_at DESC LIMIT 5", (tenant_id,)
    ).fetchall()

    total_runs = db.execute(
        "SELECT COUNT(*) FROM pipeline_runs WHERE tenant_id=?", (tenant_id,)
    ).fetchone()[0]

    agents_done = db.execute(
        "SELECT COUNT(*) FROM agents WHERE tenant_id=? AND status='done'", (tenant_id,)
    ).fetchone()[0]

    agents_error = db.execute(
        "SELECT COUNT(*) FROM agents WHERE tenant_id=? AND status='error'", (tenant_id,)
    ).fetchone()[0]

    # ── Director last decision ─────────────────────────────────────────────
    director_log = db.execute(
        """SELECT message, level, created_at FROM activity
           WHERE tenant_id=? AND agent='Pipeline Director'
           ORDER BY id DESC LIMIT 3""",
        (tenant_id,)
    ).fetchall()

    # ── Cannibalization count ──────────────────────────────────────────────
    cannibal_count = db.execute(
        "SELECT COUNT(*) FROM cannibalization_risks WHERE tenant_id=? AND resolved=0", (tenant_id,)
    ).fetchone()[0] if _table_exists(db, "cannibalization_risks") else 0

    clients = _get_sidebar_clients()
    db.close()

    # ── GEO citations ────────────────────────────────────────────────────
    geo_citations = 0
    geo_queries_total = 0
    try:
        geo_row = db.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(client_mentioned),0) AS cited FROM geo_queries WHERE tenant_id=?",
            (tenant_id,)
        ).fetchone()
        if geo_row:
            geo_citations = int(geo_row["cited"] or 0)
            geo_queries_total = int(geo_row["total"] or 0)
    except Exception:
        pass

    return render_template(
        "pipeline/professional.html",
        client=dict(client),
        layers=layers,
        activity=[dict(a) for a in activity],
        kw_count=kw_count,
        page_counts=dict(page_counts) if page_counts else {"total": 0, "draft": 0, "ready": 0, "published": 0},
        clients=clients,
        active_tenant=tenant_id,
        tenant_id=tenant_id,
        kw_by_priority=kw_by_priority,
        kw_by_category=kw_by_category,
        kw_by_difficulty=kw_by_difficulty,
        top_keywords=[dict(r) for r in top_keywords],
        pages_by_type=pages_by_type,
        pages_by_status=pages_by_status,
        avg_quality=avg_quality,
        comp_stats=comp_stats,
        tier1_names=tier1_names,
        gap_kw_count=gap_kw_count,
        last_runs=[dict(r) for r in last_runs],
        total_runs=total_runs,
        agents_done=agents_done,
        agents_error=agents_error,
        director_log=[dict(r) for r in director_log],
        cannibal_count=cannibal_count,
        geo_citations=geo_citations,
        geo_queries_total=geo_queries_total,
    )


@bp.route("/agents")
@login_required
def agents_page(tenant_id):
    if not can_access_tenant(tenant_id):
        abort(403)
    db = get_db()
    client = db.execute("SELECT * FROM clients WHERE tenant_id=?", (tenant_id,)).fetchone()
    if not client:
        db.close()
        abort(404)

    agent_rows = db.execute(
        "SELECT * FROM agents WHERE tenant_id=? ORDER BY layer, name", (tenant_id,)
    ).fetchall()
    layers = {}
    for a in agent_rows:
        layer_num = a["layer"]
        if layer_num not in layers:
            layers[layer_num] = {"label": LAYER_LABELS.get(layer_num, f"Layer {layer_num}"), "agents": []}
        layers[layer_num]["agents"].append(dict(a))

    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    _PDT = _tz(_td(hours=-7))
    activity = db.execute(
        "SELECT * FROM activity WHERE tenant_id=? ORDER BY id DESC LIMIT 50", (tenant_id,)
    ).fetchall()
    for a in activity:
        try:
            dt = _dt.strptime(a["created_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=_tz.utc)
            a["created_at"] = dt.astimezone(_PDT).strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            pass

    kw_count = db.execute("SELECT COUNT(*) FROM keywords WHERE tenant_id=?", (tenant_id,)).fetchone()[0]
    page_counts = db.execute(
        "SELECT COUNT(*) AS total, SUM(CASE WHEN status='published' THEN 1 ELSE 0 END) AS published FROM pages WHERE tenant_id=?",
        (tenant_id,)
    ).fetchone()

    # Additional stats for the professional dashboard
    page_counts_full = db.execute(
        """SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status='draft'     THEN 1 ELSE 0 END) AS draft,
            SUM(CASE WHEN status='ready'     THEN 1 ELSE 0 END) AS ready,
            SUM(CASE WHEN status='published' THEN 1 ELSE 0 END) AS published
           FROM pages WHERE tenant_id=?""",
        (tenant_id,)
    ).fetchone()

    comp_stats = {r["tier"]: r["cnt"] for r in db.execute(
        "SELECT tier, COUNT(*) as cnt FROM competitors WHERE tenant_id=? AND (tier IS NULL OR tier != 0) GROUP BY tier", (tenant_id,)
    ).fetchall()}

    geo_citations = 0
    try:
        geo_row = db.execute(
            "SELECT COALESCE(SUM(client_mentioned),0) AS cited FROM geo_queries WHERE tenant_id=?",
            (tenant_id,)
        ).fetchone()
        if geo_row:
            geo_citations = int(geo_row["cited"] or 0)
    except Exception:
        pass

    clients = _get_sidebar_clients()
    db.close()

    return render_template(
        "pipeline/professional.html",
        client=dict(client),
        layers=layers,
        activity=[dict(a) for a in activity],
        kw_count=kw_count,
        page_counts=dict(page_counts_full) if page_counts_full else {"total": 0, "draft": 0, "ready": 0, "published": 0},
        clients=clients,
        active_tenant=tenant_id,
        tenant_id=tenant_id,
        kw_by_priority={},
        kw_by_category={},
        kw_by_difficulty={},
        top_keywords=[],
        pages_by_type={},
        pages_by_status={},
        avg_quality=0,
        comp_stats=comp_stats,
        tier1_names=[],
        gap_kw_count=0,
        last_runs=[],
        total_runs=0,
        agents_done=0,
        agents_error=0,
        director_log=[],
        cannibal_count=0,
        geo_citations=geo_citations,
        geo_queries_total=0,
    )
