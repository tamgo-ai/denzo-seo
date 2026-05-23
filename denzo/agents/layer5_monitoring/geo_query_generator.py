"""
GEO Query Generator — Layer 6
Feeds the GEO Monitor's query bank intelligently.

Runs BEFORE GEO Monitor. Pulls from 3 sources:
  1. Keyword bank (top keywords → conversational queries)
  2. Past GEO results (NOT_CITED queries → priority gap queries)
  3. Competitors (queries where competitors appear but business doesn't)

Run weekly. GEO Monitor runs daily using the bank this agent builds.
"""
import json
import re
from denzo.agents.base_agent import TenantAwareBaseAgent, ClientContext, db_execute, db_write, strip_json_fences


class GEOQueryGenerator(TenantAwareBaseAgent):

    def __init__(self, ctx: ClientContext):
        super().__init__("GEO Query Generator", ctx, layer=6, color="violet")

    # ── Source 1: top keywords → conversational queries ───────────────────────

    def _queries_from_keywords(self) -> list:
        """Convert top-priority keywords into natural AI search queries."""
        rows = db_execute(
            """SELECT keyword, location, category FROM keywords
               WHERE tenant_id=? AND priority = 'high'
               ORDER BY CAST(volume AS INTEGER) DESC LIMIT 60""",
            (self.ctx.tenant_id,)
        )
        if not rows:
            rows = db_execute(
                "SELECT keyword, location, category FROM keywords WHERE tenant_id=? LIMIT 60",
                (self.ctx.tenant_id,)
            )
        if not rows:
            return []

        kw_list = [
            {"keyword": r["keyword"], "location": r["location"] or "", "category": r["category"] or "service"}
            for r in rows
        ]

        # Build one industry-specific example to guide Claude without contaminating outputs
        industry = self.ctx.industry_vertical or "general"
        primary_svc = (self.ctx.services[0] if self.ctx.services else "our service").lower()
        city = self.ctx.primary_city or "our city"
        kw_example = f"{primary_svc} {city}"
        query_example = f"best {primary_svc} near {city}"

        prompt = f"""{self.ctx.to_prompt_block()}

I have these high-priority SEO keywords for this business (industry: {industry}):
{json.dumps(kw_list[:40], ensure_ascii=False)}

Convert them into natural conversational queries — exactly how a person would type into Perplexity, ChatGPT, or Google AI.

Rules:
- Make them sound like real questions or natural searches, NOT like SEO keyword strings
- Example: "{kw_example}" → "{query_example}"
- Include a mix of question format ("where", "what", "who", "best") and statement format
- Keep location when present
- Group by category: branded, service, location, problem, comparison, certification
- CRITICAL: Every query must be relevant to the "{industry}" industry — never reference other industries

Return JSON array:
[{{"query": "{query_example}", "category": "service", "source": "keyword"}}]

Return ONLY valid JSON. Max 30 queries.
"""
        raw = self.call_claude(prompt, max_tokens=2000, model="claude-sonnet-4-6")
        if not raw:
            return []
        try:
            return json.loads(strip_json_fences(raw, "["))
        except Exception:
            return []

    # ── Source 2: NOT_CITED gaps from past results ────────────────────────────

    def _queries_from_gaps(self) -> list:
        """Find queries where business was NOT cited → prioritize those."""
        rows = db_execute(
            """SELECT query, COUNT(*) as checks, COALESCE(SUM(client_mentioned), 0) as cited
               FROM geo_queries
               WHERE tenant_id=?
               GROUP BY query
               HAVING cited = 0 AND checks >= 1
               ORDER BY checks DESC
               LIMIT 20""",
            (self.ctx.tenant_id,)
        )
        if not rows:
            return []

        gap_queries = [r["query"] for r in rows]
        self.log(f"Found {len(gap_queries)} gap queries (NOT_CITED) from past checks.", "info")

        prompt = f"""{self.ctx.to_prompt_block()}

These are queries where {self.ctx.client_name} was NOT cited by AI systems:
{json.dumps(gap_queries, ensure_ascii=False)}

For each gap query, generate 1-2 VARIANT queries that approach the same topic differently.
The goal: if we create better content, these variants should also get monitored.

Return JSON array:
[{{"query": "variant query here", "category": "service", "source": "gap", "original_gap": "original query"}}]

Return ONLY valid JSON.
"""
        raw = self.call_claude(prompt, max_tokens=1000, model="claude-sonnet-4-6")
        if not raw:
            # Still return the original gap queries as-is for re-monitoring
            return [{"query": q, "category": "service", "source": "gap"} for q in gap_queries]
        try:
            variants = json.loads(strip_json_fences(raw, "["))
            # Also include the originals
            originals = [{"query": q, "category": "service", "source": "gap"} for q in gap_queries]
            return originals + variants
        except Exception:
            return [{"query": q, "category": "service", "source": "gap"} for q in gap_queries]

    # ── Source 3: competitor gap queries ─────────────────────────────────────

    def _queries_from_competitors(self) -> list:
        """Generate queries where competitors appear — we want to track those too."""
        # Find queries where competitors were mentioned but we weren't
        rows = db_execute(
            """SELECT query, competitors_mentioned
               FROM geo_queries
               WHERE tenant_id=?
                 AND client_mentioned = 0
                 AND competitors_mentioned IS NOT NULL
                 AND competitors_mentioned != 'null'
                 AND competitors_mentioned != '[]'
               ORDER BY checked_at DESC
               LIMIT 15""",
            (self.ctx.tenant_id,)
        )

        competitor_gaps = []
        for r in rows:
            try:
                comps = json.loads(r["competitors_mentioned"] or "[]")
                if comps:
                    competitor_gaps.append({"query": r["query"], "competitors": comps})
            except Exception:
                pass

        if not competitor_gaps:
            # Fallback: use competitors from DB to generate queries
            comp_rows = db_execute(
                "SELECT name FROM competitors WHERE tenant_id=? LIMIT 10",
                (self.ctx.tenant_id,)
            )
            if not comp_rows:
                return []
            comp_names = [r["name"] for r in comp_rows]

            industry = self.ctx.industry_vertical or "general"
            primary_svc = (self.ctx.services[0] if self.ctx.services else "service").lower()
            comp_example = comp_names[0] if comp_names else "a competitor"

            prompt = f"""{self.ctx.to_prompt_block()}

These are the main competitors of {self.ctx.client_name} (industry: {industry}):
{json.dumps(comp_names, ensure_ascii=False)}

Generate 10 queries that someone would ask when comparing these competitors or looking for alternatives.
These are queries where competitors likely get cited — we want to monitor them and eventually outrank.

Examples relevant to this industry:
- "{comp_example} vs {self.ctx.client_name}"
- "alternatives to {comp_example} for {primary_svc}"
- "best {primary_svc} besides {comp_example}"

Return JSON array:
[{{"query": "...", "category": "comparison", "source": "competitor"}}]
Return ONLY valid JSON.
"""
            raw = self.call_claude(prompt, max_tokens=800, model="claude-sonnet-4-6")
            if not raw:
                return []
            try:
                return json.loads(strip_json_fences(raw, "["))
            except Exception:
                return []

        # Generate variant queries from known competitor-cited queries
        prompt = f"""{self.ctx.to_prompt_block()}

These queries cited competitors but NOT {self.ctx.client_name}:
{json.dumps(competitor_gaps[:10], ensure_ascii=False)}

Generate variant queries for each — different phrasings of the same intent.
We need to monitor these to track when we start winning against competitors.

Return JSON array:
[{{"query": "...", "category": "comparison", "source": "competitor"}}]
Return ONLY valid JSON.
"""
        raw = self.call_claude(prompt, max_tokens=800, model="claude-sonnet-4-6")
        if not raw:
            return [{"query": g["query"], "category": "comparison", "source": "competitor"}
                    for g in competitor_gaps]
        try:
            return json.loads(strip_json_fences(raw, "["))
        except Exception:
            return [{"query": g["query"], "category": "comparison", "source": "competitor"}
                    for g in competitor_gaps]

    # ── Save to bank ──────────────────────────────────────────────────────────

    def _save_to_bank(self, queries: list) -> tuple[int, int]:
        added = 0
        skipped = 0
        NUM_PREFIX = re.compile(r"^\d+[\.\)]\s*")

        for q in queries:
            text = NUM_PREFIX.sub("", q.get("query", "").strip()).strip()
            cat  = q.get("category", "general")
            if not text or len(text) > 300:
                continue
            try:
                db_write(
                    "INSERT OR IGNORE INTO geo_query_bank (tenant_id, query, category) VALUES (?,?,?)",
                    (self.ctx.tenant_id, text, cat)
                )
                added += 1
            except Exception:
                skipped += 1

        return added, skipped

    # ── Main run ──────────────────────────────────────────────────────────────

    def run(self):
        self.log("GEO Query Generator starting...")
        self.set_status("working", "Analyzing keyword bank")
        ctx = self.ctx

        total_added = 0

        # Source 1 — Keywords → conversational queries
        self.log("Source 1: Converting top keywords to conversational queries...", "info")
        kw_queries = self._queries_from_keywords()
        added, _ = self._save_to_bank(kw_queries)
        total_added += added
        self.log(f"  → {len(kw_queries)} generated, {added} new added to bank", "success" if added else "info")

        if self.should_stop():
            return

        # Source 2 — Past NOT_CITED gaps
        self.set_status("working", "Analyzing past GEO gaps")
        self.log("Source 2: Mining NOT_CITED gaps from past results...", "info")
        gap_queries = self._queries_from_gaps()
        added, _ = self._save_to_bank(gap_queries)
        total_added += added
        self.log(f"  → {len(gap_queries)} gap queries, {added} new added to bank", "success" if added else "info")

        if self.should_stop():
            return

        # Source 3 — Competitor gaps
        self.set_status("working", "Analyzing competitor query gaps")
        self.log("Source 3: Generating queries from competitor analysis...", "info")
        comp_queries = self._queries_from_competitors()
        added, _ = self._save_to_bank(comp_queries)
        total_added += added
        self.log(f"  → {len(comp_queries)} competitor queries, {added} new added to bank", "success" if added else "info")

        # Final count
        bank_total = db_execute(
            "SELECT COUNT(*) n FROM geo_query_bank WHERE tenant_id=? AND active=1",
            (ctx.tenant_id,)
        )[0]["n"]

        self.log(
            f"Query bank updated: {total_added} new queries added. Total active: {bank_total}.",
            "success"
        )
        self.set_status("done", f"{total_added} new queries added · {bank_total} total in bank")
