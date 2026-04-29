"""
SERP Intelligence — Layer 6
Analyzes SERP features and content opportunities for top keywords.

Data source priority:
  1. Apify Google SERP Scraper — real SERP feature detection if apify_api_key is set
  2. Claude AI analysis — estimates SERP features based on keyword characteristics
"""
import json
from denzo.agents.base_agent import TenantAwareBaseAgent, ClientContext, db_execute, db_write, strip_json_fences


class SERPIntelligence(TenantAwareBaseAgent):

    def __init__(self, ctx: ClientContext):
        super().__init__("SERP Intelligence", ctx, layer=6, color="rose")

    def _ai_serp_analysis(self, kw_list: list) -> dict:
        """Fallback: Claude estimates SERP landscape."""
        ctx = self.ctx
        prompt = f"""{ctx.to_prompt_block()}

You are a Senior SERP Analysis Specialist with 12 years of experience analyzing
Google Search Result Pages for local businesses.

Analyze the SERP landscape for these keywords:
{json.dumps(kw_list, ensure_ascii=False)}

For each keyword group, identify:
1. SERP features likely present (Featured Snippet, People Also Ask, Local Pack, Reviews, FAQ)
2. Content format that wins (list, how-to, comparison, definition, local)
3. Top-ranking content type (service page, blog, directory, maps)
4. Our best opportunity to rank

Return a JSON object:
{{
  "serp_analysis": [
    {{
      "keyword": "...",
      "serp_features": ["Featured Snippet", "Local Pack"],
      "winning_format": "list|how-to|comparison|definition|local",
      "content_type": "service_page|blog|local_page",
      "opportunity": "high|medium|low",
      "action": "What specific content to create"
    }}
  ],
  "featured_snippet_opportunities": ["keyword 1"],
  "local_pack_keywords": ["keyword 1"],
  "paa_questions": ["Question 1"]
}}
Return ONLY valid JSON.
"""
        raw = self.call_claude(prompt, max_tokens=2500)
        if not raw:
            return {}
        try:
            return json.loads(strip_json_fences(raw))
        except Exception:
            return {}

    def run(self):
        self.log("Starting SERP intelligence analysis...")
        self.set_status("working", "Loading top keywords")

        pub_check = db_execute(
            "SELECT COUNT(*) AS n FROM pages WHERE tenant_id=? AND status='published'",
            (self.ctx.tenant_id,)
        )
        if not (pub_check and pub_check[0]["n"] > 0):
            self.log("No published pages found. Run a Publisher agent first.", "warning")
            self.set_status("idle", "No published pages — run a Publisher first")
            return

        keywords = db_execute(
            "SELECT keyword, location, category FROM keywords "
            "WHERE tenant_id=? ORDER BY priority DESC LIMIT 20",
            (self.ctx.tenant_id,)
        )
        if not keywords:
            self.log("No keywords found. Run Keyword Strategist first.", "warning")
            self.set_status("idle", "No keywords")
            return

        kw_strings = [r["keyword"] + (f" {r['location']}" if r["location"] else "") for r in keywords]
        domain     = self.ctx.domain or self.ctx.website_url or ""

        # ── Try Apify for real SERP data ─────────────────────────────────────
        from denzo.agents.utils.apify_service import ApifyService
        apify = ApifyService(log_fn=lambda m, l="info": self.log(m, l))

        result = {}
        if apify.available():
            self.log(f"[APIFY REAL] Fetching real SERP data for {len(kw_strings)} keywords...")
            self.set_status("working", "Fetching real SERP features via Apify")

            serp_data = apify.check_serp_rankings(kw_strings, domain)

            if serp_data:
                # Build result from real SERP data
                analysis       = []
                snippet_opps   = []
                local_pack_kws = []
                paa_questions  = []

                for row in serp_data:
                    kw    = row["keyword"]
                    feats = row["serp_features"]
                    paas  = row["paa_questions"]
                    pos   = row["position"]

                    # Determine best content format
                    if "Featured Snippet" in feats:
                        fmt = "definition"
                        snippet_opps.append(kw)
                    elif "Local Pack" in feats:
                        fmt = "local"
                        local_pack_kws.append(kw)
                    elif "People Also Ask" in feats:
                        fmt = "how-to"
                    else:
                        fmt = "list"

                    # Opportunity: if not ranking or ranking 11-30 → high opportunity
                    if pos is None or (pos and pos > 10):
                        opp = "high"
                    elif pos and pos <= 5:
                        opp = "low"  # already ranking well
                    else:
                        opp = "medium"

                    paa_questions.extend(paas)
                    analysis.append({
                        "keyword":       kw,
                        "current_pos":   pos,
                        "serp_features": feats,
                        "winning_format": fmt,
                        "content_type":  "local_page" if "Local Pack" in feats else "service_page",
                        "opportunity":   opp,
                        "action":        f"Optimize for {fmt} format" + (
                            f" — currently #{pos}" if pos else " — not ranking, create page"
                        ),
                    })

                result = {
                    "serp_analysis":                  analysis,
                    "featured_snippet_opportunities": list(set(snippet_opps))[:10],
                    "local_pack_keywords":            list(set(local_pack_kws))[:10],
                    "paa_questions":                  list(set(paa_questions))[:20],
                    "source":                         "apify_real",
                }

                self.log(
                    f"[APIFY REAL] SERP data: {len(snippet_opps)} snippet opps, "
                    f"{len(local_pack_kws)} local pack, {len(paa_questions)} PAA questions",
                    "success"
                )

        # ── Fallback to AI if no Apify or no results ─────────────────────────
        if not result:
            source_label = "AI analysis (no Apify key)" if not apify.available() else "AI analysis (Apify returned no data)"
            self.log(f"Using {source_label}...")
            self.set_status("working", "Analyzing SERP features with Claude AI")
            result = self._ai_serp_analysis(kw_strings[:15])
            if result:
                result["source"] = "ai_estimate"

        if not result:
            self.log("Analysis failed — no data from Apify or AI.", "error")
            self.set_status("error", "No SERP data")
            return

        # ── Add PAA questions as new keywords ─────────────────────────────────
        snippets   = result.get("featured_snippet_opportunities", [])
        local_pack = result.get("local_pack_keywords", [])
        paas       = result.get("paa_questions", [])

        self.log(f"Featured snippet opportunities: {len(snippets)}", "success")
        for kw in snippets[:5]:
            self.log(f"  → Snippet: {kw}", "info")

        self.log(f"Local Pack keywords: {len(local_pack)}", "success")
        for kw in local_pack[:5]:
            self.log(f"  → Local Pack: {kw}", "info")

        for q in paas[:5]:
            if q:
                self.log(f"  → PAA: {q}", "info")
                self.add_keyword(q, category="question", priority="high")

        # Save to settings
        db_write(
            "INSERT OR REPLACE INTO settings (tenant_id, key, value) VALUES (?,?,?)",
            (self.ctx.tenant_id, "serp_intelligence", json.dumps(result, ensure_ascii=False))
        )

        analysis = result.get("serp_analysis", [])
        source   = result.get("source", "unknown")
        self.log(
            f"SERP Intelligence complete ({source}): {len(analysis)} keywords analyzed, "
            f"{len(snippets)} snippet opportunities.",
            "success"
        )
        self.set_status("done", f"{len(analysis)} SERPs analyzed · {len(snippets)} snippet opps [{source}]")
