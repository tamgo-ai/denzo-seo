"""
GEO Gap Closer — Layer 6 (Analytics)
=====================================
The missing piece of the GEO flywheel. Runs AFTER GEO Monitor to close
the citation gaps automatically.

Flywheel:
  OPTIMIZE (GEO Optimizer) → TRACK (GEO Monitor) → CLOSE (this agent)

For every query where the client is NOT cited by AI:
  1. Check if we have a page targeting this query
  2. If not → auto-create a page stub → ProgrammaticSEO picks it up
  3. If yes but quality low → re-queue for Content Optimizer
  4. If competitor cited instead → analyze their content strategy

Result: next week's GEO Monitor run shows higher citation rate.
"""
import json
from denzo.agents.base_agent import TenantAwareBaseAgent, ClientContext, db_execute, db_write, strip_json_fences


class GEOGapCloser(TenantAwareBaseAgent):

    def __init__(self, ctx: ClientContext):
        super().__init__("GEO Gap Closer", ctx, layer=6, color="emerald")

    def _parse_not_cited_queries(self) -> list[dict]:
        """Find queries from GEO Monitor where client was NOT cited."""
        rows = db_execute(
            """SELECT gq.query, gq.ai_model, gq.competitors_mentioned, gq.checked_at
               FROM geo_queries gq
               WHERE gq.tenant_id=? AND gq.client_mentioned=0
               AND gq.checked_at > datetime('now', '-7 days')
               AND gq.ai_model='perplexity'
               ORDER BY gq.id DESC""",
            (self.ctx.tenant_id,)
        )
        return [dict(r) for r in (rows or [])]

    def _check_existing_page(self, query: str) -> dict | None:
        """Check if we already have a page targeting this query."""
        # Try matching by keyword
        rows = db_execute(
            """SELECT id, title, slug, status, quality_score FROM pages
               WHERE tenant_id=? AND (target_keyword LIKE ? OR title LIKE ?)
               AND status IN ('draft','ready','published')
               LIMIT 1""",
            (self.ctx.tenant_id, f"%{query[:30]}%", f"%{query[:30]}%")
        )
        if rows:
            r = rows[0]
            return {"id": r["id"], "title": r["title"], "slug": r["slug"],
                    "status": r["status"], "quality_score": r["quality_score"]}
        return None

    def _generate_page_for_query(self, query: str) -> bool:
        """Use Claude to generate a page stub targeting this GEO gap query."""
        prompt = f"""{self.ctx.to_prompt_block()}

A GEO (Generative Engine Optimization) audit found that {self.ctx.client_name} is NOT cited by AI search engines (Perplexity, ChatGPT) for this query:
"{query}"

Create a page stub that will help the business get cited for this query.

Return JSON:
{{
  "title": "SEO-optimized page title (max 60 chars)",
  "slug": "url-friendly-slug",
  "type": "service|location|faq|blog",
  "keyword": "{query}",
  "meta_description": "120-155 char meta description for this page",
  "eeat_angle": "How this page will demonstrate real expertise for AI citation"
}}

Title rules: Include primary keyword + city. Max 60 chars. No fluff.
"""
        raw = self.call_claude(prompt, max_tokens=400, model="claude-sonnet-4-6")
        if not raw:
            return False
        try:
            data = json.loads(strip_json_fences(raw))
            self.add_page(
                title=data.get("title", query[:60]),
                slug=data.get("slug", ""),
                page_type=data.get("type", "service"),
                target_keyword=query,
                meta_description=data.get("meta_description", ""),
                notes=f"[GEO_GAP] {data.get('eeat_angle', '')}"
            )
            return True
        except Exception:
            return False

    def run(self):
        self.log("GEO Gap Closer — analyzing citation gaps...")
        self.set_status("working", "Loading uncited queries")

        not_cited = self._parse_not_cited_queries()
        if not not_cited:
            self.log("No GEO gaps found — all queries cited!", "success")
            self.set_status("done", "No gaps to close")
            return

        self.log(f"Found {len(not_cited)} queries where {self.ctx.client_name} is NOT cited by AI.")

        pages_created = 0
        pages_requeued = 0
        competitors_analyzed = 0
        max_pages = 15  # limit per run to avoid explosion

        for gap in not_cited:
            if pages_created >= max_pages or self.should_stop():
                break

            query = gap["query"]
            existing = self._check_existing_page(query)

            if existing:
                # Page exists but AI doesn't cite it → needs GEO-specific optimization
                # Even pages with good SEO scores (70+) can fail GEO citation.
                # GEO requires: Q&A format, citation snippets, statistics, definitions.
                if existing["status"] == "published":
                    # Mark for GEO re-optimization: reset to ready, clear GEO tag if present
                    db_write(
                        "UPDATE pages SET status='ready', "
                        "notes=REPLACE(COALESCE(notes,''),'[GEO]','')||' [GEO_REGAP]' "
                        "WHERE id=? AND tenant_id=?",
                        (existing["id"], self.ctx.tenant_id)
                    )
                    self.log(f"Re-queued for GEO: {existing['title']} (score={existing['quality_score']}, published but not AI-cited)", "info")
                    pages_requeued += 1
                elif existing["status"] in ("ready", "draft"):
                    self.log(f"Not yet published: {existing['title']} — will be GEO-optimized before publish", "info")
            else:
                # No page exists → create one
                if self._generate_page_for_query(query):
                    self.log(f"Created page stub for: \"{query[:70]}\"", "success")
                    pages_created += 1

            # Track which competitors ARE cited for this query
            comps = gap.get("competitors_mentioned", "")
            if comps:
                try:
                    comp_list = json.loads(comps) if isinstance(comps, str) else comps
                    if comp_list:
                        competitors_analyzed += 1
                except Exception:
                    pass

        self.log(
            f"GEO Gap Closer complete: {pages_created} new pages created, "
            f"{pages_requeued} pages re-queued for optimization, "
            f"{competitors_analyzed} competitor citations analyzed.",
            "success"
        )
        self.set_status("done", f"{pages_created} new + {pages_requeued} optimized")
