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
        self.log("KeywordFootprintAgent: mapping existing keyword footprint...")
        self.set_status("working", "Checking what keywords the client already ranks for")

        keyword_map = {}  # keyword → url (the page that already owns it)

        # ── Phase 1: Google Search Console (preferred) ─────────────────────
        try:
            from denzo.agents.utils.gsc_client import (
                is_gsc_connected, top_queries, top_pages, query_search_analytics
            )

            if is_gsc_connected(self.tenant_id):
                self.log("GSC connected — fetching real ranking data")
                queries = top_queries(self.tenant_id, days=90, limit=200)
                if queries:
                    for q in queries:
                        keyword = q.get("query", "").strip().lower()
                        if keyword and keyword not in keyword_map:
                            # Get the URL that ranks highest for this query
                            pages = query_search_analytics(
                                self.tenant_id,
                                site_url=None,  # uses bound site
                                start_date=(datetime.now(timezone.utc).strftime("%Y-%m-%d")),
                                end_date=(datetime.now(timezone.utc).strftime("%Y-%m-%d")),
                                dimensions=["page"],
                                row_limit=1
                            )
                            top_url = pages[0]["keys"][0] if pages else ""
                            keyword_map[keyword] = top_url
                    self.log(f"Found {len(keyword_map)} keywords via GSC")
        except Exception as e:
            self.log(f"GSC lookup skipped: {e}", "info")

        # ── Phase 2: Fallback — SEM/sERP from seed keywords ─────────────────
        if not keyword_map:
            self.log("No GSC data — falling back to seed keyword analysis")
            keyword_map = self._footprint_from_seed_keywords()

        # ── Phase 3: Cross-reference with site inventory ────────────────────
        inventory_keywords = self._footprint_from_inventory()
        for kw, url in inventory_keywords.items():
            if kw not in keyword_map:
                keyword_map[kw] = url

        # Persist
        self.save_output("existing_keyword_map", {
            "keywords": keyword_map,
            "total": len(keyword_map),
            "source": "gsc" if len(keyword_map) > 50 else "seed+inventory",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })

        self.set_status("done", f"Mapped {len(keyword_map)} existing keyword→URL pairs")

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
