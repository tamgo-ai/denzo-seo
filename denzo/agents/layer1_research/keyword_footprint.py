"""
KeywordFootprintAgent — Discovery layer. Maps what keywords the client
already ranks for, so DENZO doesn't cannibalize them.

Uses Google Search Console if connected, falls back to SERP scraping.
Part of Capa 0.5 (Discovery & Reconciliation).
"""

import json
import time
from datetime import datetime, timezone

from denzo.agents.base_agent import TenantAwareBaseAgent


class KeywordFootprintAgent(TenantAwareBaseAgent):
    """Map existing keyword rankings to avoid self-cannibalization."""

    PREREQUISITES = []  # Depends on SiteInventory having run, but enforced by Director
    MIN_KEYWORDS = 0

    def __init__(self, ctx):
        super().__init__(name="Keyword Footprint", ctx=ctx, layer=1, color="sand")

    def run(self):
        from denzo.agents.utils.gsc_client import is_gsc_connected,sync_last_n_days
        from denzo.db import get_db
        self.set_status('working','Mapping observed query/page pairs')
        keyword_map, sources = {}, {}
        if is_gsc_connected(self.tenant_id):
            try:
                sync_last_n_days(self.tenant_id,90)
                db = get_db()
                try:
                    rows = db.execute("""SELECT query,page,SUM(impressions) impressions FROM gsc_queries
                         WHERE tenant_id=? AND date>=date('now','-92 days')
                         GROUP BY query,page ORDER BY impressions DESC""",(self.tenant_id,)).fetchall()
                finally:
                    db.close()
                for row in rows:
                    kw = row['query'].strip().lower()
                    if kw and kw not in keyword_map:
                        keyword_map[kw],sources[kw] = row['page'],'gsc'
            except Exception as exc:
                self.log(f'GSC unavailable: {exc}','warning')
        # Inventory is evidence of targeting, not proof of ranking.
        for kw,url in self._footprint_from_inventory().items():
            if kw not in keyword_map:
                keyword_map[kw],sources[kw] = url,'inventory_target'
        self.save_output('existing_keyword_map',{'keywords':keyword_map,'sources':sources,
                         'total':len(keyword_map),'source':'mixed' if len(set(sources.values()))>1 else next(iter(sources.values()),'unavailable'),
                         'completed_at':datetime.now(timezone.utc).isoformat()})
        self.set_status('done',f'Mapped {len(keyword_map)} observed keyword/page pairs')

    def _footprint_from_seed_keywords(self) -> dict:
        """Without GSC, use the tenant's seed keywords and check if domain appears in SERP."""
        from denzo.agents.base_agent import db_execute

        # Load tenant's existing keywords (from Keyword Strategist)
        rows = db_execute(
            "SELECT DISTINCT keyword FROM keywords WHERE tenant_id=? LIMIT 50",
            (self.tenant_id,)
        )
        keywords = [r["keyword"] for r in rows] if rows else []

        # Also add common branded queries
        keywords.append(self.ctx.client_name)
        domain_short = self.ctx.domain.replace("https://", "").replace("http://", "").rstrip("/")
        keywords.append(domain_short)

        keyword_map = {}
        domain = self.ctx.domain or ""

        for kw in keywords[:30]:  # limit API calls
            if self.should_stop():
                break
            try:
                # Use a simple SERP check — search for keyword + domain
                import requests as _req
                from urllib.parse import quote
                search_url = f"https://www.google.com/search?q={quote(kw)}+site:{domain_short}&hl=en"
                r = _req.get(search_url, headers={
                    "User-Agent": "Mozilla/5.0 (compatible; DENZO-SEO/1.0)",
                    "Accept": "text/html",
                }, timeout=10)
                if domain_short in r.text:
                    keyword_map[kw] = domain
                time.sleep(1.5)  # rate limit
            except Exception:
                pass

        return keyword_map

    def _footprint_from_inventory(self) -> dict:
        """Extract keyword ownership from pages already in the inventory."""
        from denzo.agents.base_agent import db_execute

        rows = db_execute(
            """SELECT target_keyword, source_url
               FROM pages
               WHERE tenant_id=? AND origin='existing' AND target_keyword IS NOT NULL
               AND target_keyword != ''""",
            (self.tenant_id,)
        )

        keyword_map = {}
        for r in (rows or []):
            kw = r["target_keyword"].strip().lower()
            url = r["source_url"]
            if kw and kw not in keyword_map:
                keyword_map[kw] = url

        return keyword_map
