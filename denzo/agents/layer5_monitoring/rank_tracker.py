"""
Rank Tracker — Layer 6
Tracks keyword rankings.

Data source priority (richest → cheapest):
  1. Google Search Console (OAuth) — real position, clicks, impressions, CTR
     for the client's own site, pulled from gsc_queries table.
  2. Apify Google SERP Scraper — third-party SERP positions + PAA questions
     when GSC has no data for a keyword (or no GSC connection).
  3. AI estimates — Claude estimates difficulty/opportunity as final fallback.

Results are saved to settings["rank_estimates"]. Each entry carries a
'source' field so the UI can show whether the number is GSC-real,
Apify-real, or AI-estimated.
"""
import json

from denzo.agents.base_agent import (
    TenantAwareBaseAgent, ClientContext, db_execute, db_write, strip_json_fences,
)


class RankTracker(TenantAwareBaseAgent):

    def __init__(self, ctx: ClientContext):
        super().__init__("Rank Tracker", ctx, layer=6, color="emerald")

    # ── AI fallback ────────────────────────────────────────────────────────────

    def _ai_estimate(self, keywords: list, domain: str) -> list:
        kw_list = [{"keyword": r["keyword"], "location": r.get("location", "")} for r in keywords]
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

    # ── Main run loop ──────────────────────────────────────────────────────────

    def run(self):
        self.log("Starting rank tracking session...")
        self.set_status("working", "Loading tracked keywords")

        # Prereq: need at least one published page
        pub_check = db_execute(
            "SELECT COUNT(*) AS n FROM pages WHERE tenant_id=? AND status='published'",
            (self.ctx.tenant_id,),
        )
        if not (pub_check and pub_check[0]["n"] > 0):
            self.log("No published pages found. Run a Publisher agent first.", "warning")
            self.set_status("idle", "No published pages — run a Publisher first")
            return

        keywords = db_execute(
            "SELECT id, keyword, location FROM keywords "
            "WHERE tenant_id=? AND priority = 'high' ORDER BY id LIMIT 30",
            (self.ctx.tenant_id,),
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

        # ── Step 1: try GSC (real data from client's own Search Console) ──────
        gsc_rows: dict[str, dict] = {}
        gsc_connected = False
        try:
            from denzo.agents.utils import google_oauth
            from denzo.agents.utils.gsc_client import sync_last_n_days, position_for_query
            if google_oauth.is_connected(self.ctx.tenant_id, "gsc"):
                gsc_connected = True
                self.set_status("working", "Syncing Search Console data")
                summary = sync_last_n_days(
                    self.ctx.tenant_id, n_days=28,
                    log=lambda m: self.log(m, "info"),
                )
                self.log(
                    f"GSC sync: {summary['rows']} rows ({summary['inserted']} new, "
                    f"{summary['updated']} updated) for {summary['site']}",
                    "success",
                )
                # Look up each tracked keyword in the synced data
                for kw_row in keywords:
                    kw = kw_row["keyword"]
                    pq = position_for_query(self.ctx.tenant_id, kw, days=28)
                    if pq:
                        gsc_rows[kw] = pq
                self.log(
                    f"GSC matched {len(gsc_rows)}/{len(keywords)} tracked keywords with real data.",
                    "info",
                )
            else:
                self.log("Google Search Console not connected — skipping GSC step.", "info")
        except Exception as e:
            self.log(f"GSC step failed (continuing): {e}", "warning")

        # ── Step 2: Apify for keywords without GSC data ──────────────────────
        from denzo.agents.utils.apify_service import ApifyService
        apify = ApifyService(log_fn=lambda m, l="info": self.log(m, l))

        # Build the list of keywords still needing a position (those not matched in GSC)
        unmatched = [r for r in keywords if r["keyword"] not in gsc_rows]

        apify_results: dict[str, dict] = {}
        if apify.available() and unmatched:
            self.log(
                f"[APIFY] Checking {len(unmatched)} unmatched keywords in Google SERP...",
                "info",
            )
            self.set_status("working", "Fetching SERP positions via Apify")

            kw_strings = [
                r["keyword"] + (f" {r['location']}" if r["location"] else "")
                for r in unmatched
            ]
            serp_results = apify.check_serp_rankings(kw_strings, domain)

            for res, src_row in zip(serp_results, unmatched):
                apify_results[src_row["keyword"]] = res
                # Add PAA questions as new keyword opportunities
                for paa in res.get("paa_questions", []):
                    if paa:
                        self.add_keyword(paa, category="question", priority="high")

        # ── Step 3: AI estimate for whatever remains ─────────────────────────
        ai_unmatched = [
            r for r in keywords
            if r["keyword"] not in gsc_rows and r["keyword"] not in apify_results
        ]
        ai_results: dict[str, dict] = {}
        if ai_unmatched and not gsc_connected and not apify.available():
            self.set_status("working", "Generating AI rank estimates (no live data)")
            estimates = self._ai_estimate(list(ai_unmatched), domain)
            for est in estimates:
                kw = est.get("keyword", "")
                if kw:
                    ai_results[kw] = est

        # ── Compose report ────────────────────────────────────────────────────
        report_rows: list[dict] = []
        top10_count = 0
        quick_wins  = 0
        not_ranking = 0

        for kw_row in keywords:
            kw = kw_row["keyword"]

            if kw in gsc_rows:
                g = gsc_rows[kw]
                pos = round(g["position"], 1) if g.get("position") else None
                row = {
                    "keyword":     kw,
                    "source":      "gsc_real",
                    "position":    pos,
                    "clicks":      int(g.get("clicks") or 0),
                    "impressions": int(g.get("impressions") or 0),
                }
                if pos is not None:
                    if pos <= 10:
                        top10_count += 1
                    if pos <= 20:
                        quick_wins += 1
                    self.log(
                        f"[GSC #{pos}] {kw} — {row['clicks']} clicks, "
                        f"{row['impressions']} impressions",
                        "success" if pos <= 10 else "info",
                    )

            elif kw in apify_results:
                a = apify_results[kw]
                pos = a.get("position")
                row = {
                    "keyword":  kw,
                    "source":   "apify_real",
                    "position": pos,
                    "serp_features": a.get("serp_features", []),
                    "paa_questions": a.get("paa_questions", []),
                }
                if pos is not None:
                    if pos <= 10:
                        top10_count += 1
                    if pos <= 20:
                        quick_wins += 1
                    feat_str = f" [{', '.join(a.get('serp_features', []))}]" if a.get('serp_features') else ""
                    self.log(f"[APIFY #{pos}] {kw}{feat_str}",
                             "success" if pos <= 10 else "info")
                else:
                    not_ranking += 1
                    self.log(f"[not found] {kw} — not in top 100", "info")

            elif kw in ai_results:
                est = ai_results[kw]
                pos = est.get("estimated_position", 50)
                row = {
                    "keyword":    kw,
                    "source":     "ai_estimate",
                    "position":   pos,
                    "difficulty": est.get("difficulty", "medium"),
                    "opportunity": est.get("opportunity", "medium"),
                    "quick_win":   bool(est.get("quick_win")),
                    "notes":       est.get("notes", ""),
                }
                if est.get("quick_win"):
                    quick_wins += 1
                self.log(f"[~#{pos} AI est.] {kw} — {row['difficulty']} · {row['opportunity']}",
                         "info")
            else:
                row = {"keyword": kw, "source": "none", "position": None}
                not_ranking += 1

            report_rows.append(row)

        sources = {r["source"] for r in report_rows}
        primary_source = (
            "gsc_real" if "gsc_real" in sources else
            "apify_real" if "apify_real" in sources else
            "ai_estimate"
        )

        db_write(
            "INSERT OR REPLACE INTO settings (tenant_id, key, value, updated_at) "
            "VALUES (?,?,?,CURRENT_TIMESTAMP)",
            (self.ctx.tenant_id, "rank_estimates", json.dumps({
                "domain":         domain,
                "primary_source": primary_source,
                "sources_used":   sorted(list(sources)),
                "gsc_connected":  gsc_connected,
                "estimates":      report_rows,
            })),
        )

        self.log(
            f"Rank tracking complete. {top10_count} in top 10 · {quick_wins} in top 20 · "
            f"{not_ranking} not ranking · primary_source={primary_source}",
            "success",
        )
        self.set_status(
            "done",
            f"{top10_count} in top 10 · {quick_wins} in top 20 ({primary_source})",
        )
