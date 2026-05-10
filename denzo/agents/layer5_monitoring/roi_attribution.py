"""
ROI Attribution — Layer 5
Generates an ROI report based on pages published, keywords tracked, and estimated traffic.
"""
import json
from denzo.agents.base_agent import TenantAwareBaseAgent, ClientContext, db_execute, db_write, strip_json_fences


class ROIAttribution(TenantAwareBaseAgent):

    def __init__(self, ctx: ClientContext):
        super().__init__("ROI Attribution", ctx, layer=6, color="amber")

    def run(self):
        self.log("Generating ROI attribution report...")
        self.set_status("working", "Collecting pipeline metrics")
        ctx = self.ctx

        # Prereq check: need at least one published page
        pub_check = db_execute(
            "SELECT COUNT(*) AS n FROM pages WHERE tenant_id=? AND status='published'",
            (ctx.tenant_id,)
        )
        pub_count = pub_check[0]["n"] if pub_check else 0
        if pub_count == 0:
            self.log("No published pages found. Run a Publisher agent first.", "warning")
            self.set_status("idle", "No published pages — run a Publisher first")
            return

        # Collect metrics from DB
        kw_rows = db_execute(
            "SELECT COUNT(*) AS n FROM keywords WHERE tenant_id=?", (ctx.tenant_id,)
        )
        kw_count = kw_rows[0]["n"] if kw_rows else 0

        pt_rows = db_execute(
            "SELECT COUNT(*) AS n FROM pages WHERE tenant_id=?", (ctx.tenant_id,)
        )
        pages_total = pt_rows[0]["n"] if pt_rows else 0

        pp_rows = db_execute(
            "SELECT COUNT(*) AS n FROM pages WHERE tenant_id=? AND status='published'", (ctx.tenant_id,)
        )
        pages_published = pp_rows[0]["n"] if pp_rows else 0

        pr_rows = db_execute(
            "SELECT COUNT(*) AS n FROM pages WHERE tenant_id=? AND status='ready'", (ctx.tenant_id,)
        )
        pages_ready = pr_rows[0]["n"] if pr_rows else 0

        comp_rows = db_execute(
            "SELECT COUNT(*) AS n FROM competitors WHERE tenant_id=?", (ctx.tenant_id,)
        )
        competitors = comp_rows[0]["n"] if comp_rows else 0

        geo_rows = db_execute(
            "SELECT COUNT(*) AS total, SUM(client_mentioned) AS cited FROM geo_queries WHERE tenant_id=?",
            (ctx.tenant_id,)
        )
        geo_total = geo_rows[0]["total"] or 0 if geo_rows else 0
        geo_cited = int(geo_rows[0]["cited"] or 0) if geo_rows else 0

        metrics = {
            "keywords_researched": kw_count,
            "pages_total": pages_total,
            "pages_published": pages_published,
            "pages_ready": pages_ready,
            "competitors_analyzed": competitors,
            "geo_queries_tested": geo_total,
            "geo_citations": geo_cited,
            "citation_rate_pct": round((geo_cited / geo_total * 100) if geo_total else 0),
        }

        # Log factual pipeline metrics — these are real numbers, not estimates
        self.log(f"Keywords researched: {kw_count}", "info")
        self.log(f"Pages total / published / ready: {pages_total} / {pages_published} / {pages_ready}", "info")
        self.log(f"Competitors analyzed: {competitors}", "info")
        self.log(f"GEO citation rate: {metrics['citation_rate_pct']}% ({geo_cited}/{geo_total} queries)", "info")

        progress_pct = round((pages_published / max(pages_total, 1)) * 100)
        self.log(f"Pipeline progress: {progress_pct}% of pages published", "info")

        # Use Claude for strategic recommendations ONLY — no revenue/traffic hallucination
        prompt = f"""{ctx.to_prompt_block()}

SEO pipeline metrics (verified factual data — do NOT estimate or invent numbers beyond these):
{json.dumps(metrics, indent=2)}

Based on these factual metrics, provide ONLY:
1. Campaign progress assessment (% complete based on pages published vs total)
2. Realistic time-to-first-rankings estimate (cite typical SEO timelines for this industry — label clearly as estimate)
3. Top 3 specific, actionable priority tasks to improve rankings fastest
4. Next 30-day milestone based on current state

DO NOT estimate traffic, leads, or revenue — you have no conversion or analytics data. Only recommend actions.

Return JSON only:
{{
  "campaign_progress_pct": {progress_pct},
  "time_to_first_rankings_weeks": 12,
  "time_note": "Explanation of why this timeline applies",
  "top_priorities": ["Specific action 1", "Specific action 2", "Specific action 3"],
  "next_milestone": "Concrete measurable goal for next 30 days",
  "summary": "2-3 sentence campaign status summary using only the factual data above"
}}"""

        self.set_status("working", "Generating strategic recommendations")
        raw = self.call_claude(prompt, max_tokens=800)

        report = {}
        if raw:
            try:
                report = json.loads(strip_json_fences(raw))
            except Exception:
                pass

        weeks = int(report.get("time_to_first_rankings_weeks", 12) or 12)
        self.log(f"Estimated time to first rankings: ~{weeks} weeks (industry estimate — not guaranteed)", "info")

        summary = report.get("summary", "")
        if summary:
            self.log(summary, "info")

        for p in report.get("top_priorities", []):
            self.log(f"Priority: {p}", "warning")

        milestone = report.get("next_milestone", "")
        if milestone:
            self.log(f"Next milestone: {milestone}", "success")

        self.log(
            "NOTE: Revenue and traffic projections require connecting Google Analytics / Search Console. "
            "This report shows pipeline metrics only.",
            "warning",
        )

        # Save report — only factual metrics + strategic recommendations, no invented numbers
        db_write(
            "INSERT OR REPLACE INTO settings (tenant_id, key, value) VALUES (?,?,?)",
            (ctx.tenant_id, "roi_report", json.dumps({**metrics, **report, "disclaimer": "Traffic/revenue projections require Analytics integration"}, ensure_ascii=False))
        )

        self.log("ROI Attribution report complete.", "success")
        self.set_status("done", f"{pages_published} pages published · {metrics['citation_rate_pct']}% GEO citation rate")
