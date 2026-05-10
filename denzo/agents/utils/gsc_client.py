"""
Google Search Console API client — thin wrapper used by Rank Tracker, ROI
Attribution, and the executive dashboard.

All methods take a tenant_id and pull the OAuth token from oauth_tokens.
Persists daily query/page rows into gsc_queries on each pull so dashboards
can render fast without hitting Google on every page load.
"""
from datetime import date, timedelta

from denzo.db import get_db
from denzo.agents.utils.google_oauth import authed_request, OAuthError, is_connected


SC_BASE = "https://searchconsole.googleapis.com/webmasters/v3"
SC_V1   = "https://searchconsole.googleapis.com/v1"


def is_gsc_connected(tenant_id: str) -> bool:
    return is_connected(tenant_id, "gsc")


def list_sites(tenant_id: str) -> list[dict]:
    """Return verified sites the connected Google account has access to."""
    data = authed_request(tenant_id, "gsc", f"{SC_BASE}/sites")
    return data.get("siteEntry", []) or []


def get_bound_site(tenant_id: str) -> str | None:
    """The site_url the user picked for this tenant after connecting."""
    db = get_db()
    row = db.execute(
        "SELECT site_url FROM oauth_tokens WHERE tenant_id=? AND provider='gsc'",
        (tenant_id,),
    ).fetchone()
    db.close()
    return row["site_url"] if row else None


def query_search_analytics(
    tenant_id: str,
    site_url: str,
    start_date: str,
    end_date: str,
    dimensions: list[str] = None,
    row_limit: int = 1000,
    start_row: int = 0,
) -> list[dict]:
    """Raw Search Analytics query.

    Default dims = ['date','query','page']. Returns a list of rows in the form:
    {'keys': ['2026-05-01', 'auto body whittier', 'https://...'],
     'clicks': 12, 'impressions': 340, 'ctr': 0.035, 'position': 7.2}
    """
    dims = dimensions or ["date", "query", "page"]
    body = {
        "startDate":  start_date,
        "endDate":    end_date,
        "dimensions": dims,
        "rowLimit":   row_limit,
        "startRow":   start_row,
        "dataState":  "all",   # include fresh data, not just final
    }
    url = f"{SC_BASE}/sites/{_quote_site(site_url)}/searchAnalytics/query"
    data = authed_request(tenant_id, "gsc", url, method="POST", body=body)
    return data.get("rows", []) or []


def sync_last_n_days(tenant_id: str, n_days: int = 28, log=None) -> dict:
    """Pull last N days of (date, query, page) and upsert into gsc_queries.

    Returns a small summary dict for the agent log.
    """
    site_url = get_bound_site(tenant_id)
    if not site_url:
        raise OAuthError("No GSC site bound to this tenant — user must pick one.")

    end_date   = date.today() - timedelta(days=2)   # GSC has ~2-day lag
    start_date = end_date - timedelta(days=n_days)
    start_str  = start_date.isoformat()
    end_str    = end_date.isoformat()

    if log:
        log(f"GSC sync: pulling {start_str} → {end_str} for {site_url}")

    total_rows = 0
    inserted   = 0
    updated    = 0

    db = get_db()

    # Page through results — GSC caps at 25k rows per page.
    start_row = 0
    page_size = 5000
    while True:
        rows = query_search_analytics(
            tenant_id, site_url,
            start_date=start_str, end_date=end_str,
            dimensions=["date", "query", "page"],
            row_limit=page_size,
            start_row=start_row,
        )
        if not rows:
            break

        for r in rows:
            keys = r.get("keys") or []
            if len(keys) != 3:
                continue
            d, q, p = keys
            clicks      = int(r.get("clicks", 0) or 0)
            impressions = int(r.get("impressions", 0) or 0)
            ctr         = float(r.get("ctr", 0) or 0.0)
            position    = float(r.get("position", 0) or 0.0)
            cur = db.execute(
                """INSERT INTO gsc_queries (tenant_id, date, query, page,
                                            clicks, impressions, ctr, position)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(tenant_id, date, query, page) DO UPDATE SET
                       clicks      = excluded.clicks,
                       impressions = excluded.impressions,
                       ctr         = excluded.ctr,
                       position    = excluded.position,
                       fetched_at  = CURRENT_TIMESTAMP""",
                (tenant_id, d, q, p, clicks, impressions, ctr, position),
            )
            if cur.rowcount == 1:
                inserted += 1
            else:
                updated += 1
            total_rows += 1
        db.commit()

        if len(rows) < page_size:
            break
        start_row += page_size

    db.close()

    summary = {
        "site":        site_url,
        "from":        start_str,
        "to":          end_str,
        "rows":        total_rows,
        "inserted":    inserted,
        "updated":     updated,
    }
    if log:
        log(f"GSC sync done: {total_rows} rows ({inserted} new, {updated} updated)")
    return summary


def top_queries(tenant_id: str, days: int = 28, limit: int = 50) -> list[dict]:
    """Top-clicked queries from gsc_queries over the last N days."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    db = get_db()
    rows = db.execute("""
        SELECT query,
               SUM(clicks)                              AS clicks,
               SUM(impressions)                         AS impressions,
               CASE WHEN SUM(impressions) > 0
                    THEN 1.0 * SUM(clicks) / SUM(impressions) ELSE 0 END AS ctr,
               AVG(position)                            AS position
        FROM gsc_queries
        WHERE tenant_id=? AND date >= ?
        GROUP BY query
        ORDER BY clicks DESC, impressions DESC
        LIMIT ?
    """, (tenant_id, cutoff, limit)).fetchall()
    db.close()
    return [dict(r) for r in rows]


def top_pages(tenant_id: str, days: int = 28, limit: int = 50) -> list[dict]:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    db = get_db()
    rows = db.execute("""
        SELECT page,
               SUM(clicks)                              AS clicks,
               SUM(impressions)                         AS impressions,
               CASE WHEN SUM(impressions) > 0
                    THEN 1.0 * SUM(clicks) / SUM(impressions) ELSE 0 END AS ctr,
               AVG(position)                            AS position
        FROM gsc_queries
        WHERE tenant_id=? AND date >= ?
        GROUP BY page
        ORDER BY clicks DESC, impressions DESC
        LIMIT ?
    """, (tenant_id, cutoff, limit)).fetchall()
    db.close()
    return [dict(r) for r in rows]


def position_for_query(tenant_id: str, query: str, days: int = 28) -> dict | None:
    """Average position + clicks/impressions for a single query — used by Rank Tracker."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    db = get_db()
    row = db.execute("""
        SELECT SUM(clicks)      AS clicks,
               SUM(impressions) AS impressions,
               AVG(position)    AS position
        FROM gsc_queries
        WHERE tenant_id=? AND date >= ? AND query = ?
    """, (tenant_id, cutoff, query)).fetchone()
    db.close()
    if not row or not row["impressions"]:
        return None
    return dict(row)


def _quote_site(site_url: str) -> str:
    """sites/{siteUrl} segment must be percent-encoded for sc-domain: prefix."""
    import urllib.parse
    return urllib.parse.quote(site_url, safe="")
