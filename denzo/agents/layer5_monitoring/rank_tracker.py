"""
Rank Tracker — Layer 6
Tracks keyword rankings.

Data source priority:
  1. Apify Google SERP Scraper — real positions if apify_api_key is set
  2. AI estimates — Claude estimates difficulty/opportunity when no real data available

Results are saved to settings["rank_estimates"] (never to geo_queries table).
"""
import json
from denzo.agents.base_agent import TenantAwareBaseAgent, ClientContext, db_execute, db_write, strip_json_fences


class RankTracker(TenantAwareBaseAgent):

    def __init__(self, ctx: ClientContext):
        super().__init__("Rank Tracker", ctx, layer=6, color="emerald")

    def _ai_estimate(self, keywords: list, domain: str) -> list:
        """Fallback: ask Claude to estimate ranking difficulty/opportunity."""
        kw_list = [{"keyword": r["keyword"], "location": r["location"]} for r in keywords]
        prompt = f"""{self.ctx.to_prompt_block()}

You are a Senior SEO Rank Analysis Specialist with 12 years of experience evaluating
local keyword competitiveness for domain: {domain}

Keywords to analyze:
{json.dumps(kw_list, ensure_ascii=False)}

Estimate for each keyword:
- Expected ranking position (1-100) given business context and typical competition
- Ranking difficulty (easy/medium/hard/very_hard)
- Monthly opportunity (low/medium/high)
- Quick win potential (true/false)

Return JSON array:
[{{"keyword":"...","estimated_position":45,"difficulty":"medium","opportunity":"high","quick_win":true,"notes":"..."}}]
Return ONLY valid JSON array.
"""
        raw = self.call_claude(prompt, max_tokens=2000)
        if not raw:
            return []
        try:
            return json.loads(strip_json_fences(raw, "["))
        except Exception:
            return []

    def run(self):
        self.log("Starting rank tracking session...")
        self.set_status("working", "Loading tracked keywords")

        # Prereq: need at least one published page
        pub_check = db_execute(
            "SELECT COUNT(*) AS n FROM pages WHERE tenant_id=? AND status='published'",
            (self.ctx.tenant_id,)
        )
        if not (pub_check and pub_check[0]["n"] > 0):
            self.log("No published pages found. Run a Publisher agent first.", "warning")
            self.set_status("idle", "No published pages — run a Publisher first")
            return

        keywords = db_execute(
            "SELECT id, keyword, location FROM keywords "
            "WHERE tenant_id=? AND priority IN ('high','alta') ORDER BY id LIMIT 30",
            (self.ctx.tenant_id,)
        )
        if not keywords:
            self.log("No high-priority keywords to track. Run Keyword Strategist first.", "warning")
            self.set_status("idle", "No keywords")
            return

        domain = self.ctx.domain or self.ctx.website_url or ""
        if not domain:
            self.log("No domain configured. Set it in Settings.", "warning")
            self.set_status("idle", "No domain")
            return

        # ── Try Apify for real SERP data ─────────────────────────────────────
        from denzo.agents.utils.apify_service import ApifyService
        apify = ApifyService(log_fn=lambda m, l="info": self.log(m, l))

        if apify.available():
            self.log(f"[APIFY REAL] Checking {len(keywords)} keywords in Google SERP...")
            self.set_status("working", "Fetching real SERP positions via Apify")

            kw_strings = [
                r["keyword"] + (f" {r['location']}" if r["location"] else "")
                for r in keywords
            ]
            serp_results = apify.check_serp_rankings(kw_strings, domain)

            quick_wins  = 0
            top10_count = 0
            report_rows = []

            for res in serp_results:
                kw   = res["keyword"]
                pos  = res["position"]
                feats = res["serp_features"]

                if pos is not None:
                    level = "success" if pos <= 10 else ("warning" if pos <= 30 else "info")
                    feat_str = f" [{', '.join(feats)}]" if feats else ""
                    self.log(f"[#{pos}] {kw}{feat_str}", level)
                    if pos <= 10:
                        top10_count += 1
                    if pos <= 20:
                        quick_wins += 1
                else:
                    self.log(f"[not found] {kw} — not in top 100", "info")

                # Add PAA questions as new keyword opportunities
                for paa in res.get("paa_questions", []):
                    if paa:
                        self.add_keyword(paa, category="question", priority="high")

                report_rows.append(res)

            # Save to settings (real data)
            db_write(
                "INSERT OR REPLACE INTO settings (tenant_id, key, value, updated_at) "
                "VALUES (?,?,?,CURRENT_TIMESTAMP)",
                (self.ctx.tenant_id, "rank_estimates", json.dumps({
                    "domain":    domain,
                    "source":    "apify_real",
                    "estimates": report_rows,
                }))
            )

            self.log(
                f"Rank tracking complete (REAL SERP data). "
                f"{top10_count} in top 10 · {quick_wins} in top 20 · "
                f"{sum(1 for r in report_rows if r['position'] is None)} not ranking.",
                "success"
            )
            self.set_status(
                "done",
                f"{top10_count} in top 10 · {quick_wins} in top 20 (real data)"
            )
            return

        # ── Fallback: AI estimates ────────────────────────────────────────────
        self.log(
            f"Apify key not set — using AI estimates for {len(keywords)} keywords. "
            "Add apify_api_key in Platform Settings for real SERP data.",
            "warning"
        )
        self.set_status("working", "Generating AI rank estimates (no Apify key)")
        estimates = self._ai_estimate(list(keywords), domain)

        quick_wins = 0
        for est in estimates:
            kw   = est.get("keyword", "")
            pos  = est.get("estimated_position", 50)
            diff = est.get("difficulty", "medium")
            opp  = est.get("opportunity", "medium")
            qw   = est.get("quick_win", False)
            level = "success" if pos <= 10 else ("warning" if pos <= 30 else "info")
            self.log(f"[~#{pos} est.] {kw} — {diff} · {opp}{' ⚡' if qw else ''}", level)
            if qw:
                quick_wins += 1

        db_write(
            "INSERT OR REPLACE INTO settings (tenant_id, key, value, updated_at) "
            "VALUES (?,?,?,CURRENT_TIMESTAMP)",
            (self.ctx.tenant_id, "rank_estimates", json.dumps({
                "domain":    domain,
                "source":    "ai_estimate",
                "note":      "Add apify_api_key in Platform Settings for real rankings",
                "estimates": estimates,
            }))
        )

        self.log(
            f"Rank analysis complete (AI estimates). {quick_wins} quick-win opportunities.",
            "success"
        )
        self.set_status("done", f"{len(estimates)} keywords analyzed (AI est.) · {quick_wins} quick wins")
