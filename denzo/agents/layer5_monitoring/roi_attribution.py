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

        prompt = f"""{ctx.to_prompt_block()}

You are a Senior Digital Marketing ROI Analyst with 12 years of experience measuring and attributing organic search value for local service businesses.

Pipeline metrics:
{json.dumps(metrics, indent=2)}

Generate a conservative, data-driven ROI attribution report for this SEO campaign.

Estimate (conservatively):
1. Monthly organic traffic potential based on keywords + pages
2. Estimated leads/month from organic traffic (use industry conversion rates)
3. Estimated revenue impact (use typical deal values for this industry)
4. Time to results (when should rankings start appearing?)
5. Priority actions to maximize ROI fastest

Return a JSON report:
{{
  "traffic_estimate_monthly": 0,
  "leads_estimate_monthly": 0,
  "revenue_impact_monthly_usd": 0,
  "time_to_results_weeks": 0,
  "roi_summary": "One paragraph executive summary",
  "top_priorities": ["Priority action 1", "action 2", "action 3"],
  "next_milestone": "What to achieve in next 30 days"
}}

Return ONLY valid JSON.
"""
        self.set_status("working", "Calculating ROI with AI")
        raw = self.call_claude(prompt, max_tokens=1500)

        if not raw:
            self.log("Could not generate ROI report.", "error")
            self.set_status("error", "No response")
            return

        try:
            raw = strip_json_fences(raw)
            report = json.loads(raw)
        except Exception:
            report = {}

        # Log key metrics
        self.log(f"Keywords researched: {kw_count}", "info")
        self.log(f"Pages published: {pages_published} / {pages_total}", "info")
        self.log(f"GEO citation rate: {metrics['citation_rate_pct']}%", "info")

        traffic = report.get("traffic_estimate_monthly", 0)
        leads   = report.get("leads_estimate_monthly", 0)
        revenue = report.get("revenue_impact_monthly_usd", 0)
        weeks   = report.get("time_to_results_weeks", 12)

        self.log(f"Estimated monthly traffic: {traffic:,} visits", "success")
        self.log(f"Estimated monthly leads: {leads}", "success")
        self.log(f"Estimated revenue impact: ${revenue:,}/month", "success")
        self.log(f"Time to results: {weeks} weeks", "info")

        summary = report.get("roi_summary", "")
        if summary:
            self.log(summary, "info")

        for p in report.get("top_priorities", []):
            self.log(f"Priority: {p}", "warning")

        milestone = report.get("next_milestone", "")
        if milestone:
            self.log(f"Next milestone: {milestone}", "success")

        # Save report
        db_write(
            "INSERT OR REPLACE INTO settings (tenant_id, key, value) VALUES (?,?,?)",
            (ctx.tenant_id, "roi_report", json.dumps({**metrics, **report}, ensure_ascii=False))
        )

        self.log("ROI Attribution report complete.", "success")
        self.set_status("done", f"${revenue:,}/mo estimated — {leads} leads/mo")
