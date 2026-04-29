"""
Reviews Intelligence — Layer 6
================================
Scrapes Google Maps reviews of top competitors and runs NLP analysis
to extract pain points, sentiment patterns, and content opportunities.

Requires Apify API key (apify_api_key in Platform Settings).
Uses: compass/google-maps-reviews-scraper ($0.30 / 1K reviews)

What it produces:
  - settings["reviews_intelligence"]: structured report with:
    · competitor_pain_points: what customers complain about at competitors
    · competitor_strengths: what customers praise at competitors
    · content_opportunities: gaps we can address in SEO content
    · emotional_triggers: phrases/words that drive reviews
    · citation_paragraphs: ready-to-use authority paragraphs for content agents
"""
import json
from denzo.agents.base_agent import TenantAwareBaseAgent, ClientContext, db_execute, db_write, strip_json_fences


class ReviewsIntelligence(TenantAwareBaseAgent):

    def __init__(self, ctx: ClientContext):
        super().__init__("Reviews Intelligence", ctx, layer=6, color="orange")

    MAX_REVIEWS_PER_COMPETITOR = 25   # ~$0.0075/competitor at $0.30/1K
    MAX_COMPETITORS = 6               # 6 × 25 = 150 reviews max = ~$0.045/run

    def _synthesize_reviews(self, competitor_reviews: list[dict]) -> dict:
        """Send competitor review data to Claude for NLP analysis."""
        ctx = self.ctx

        # Build compact review summary
        review_blocks = []
        for cr in competitor_reviews:
            comp_name = cr["name"]
            reviews   = cr["reviews"][:20]  # cap per competitor for token control
            if not reviews:
                continue
            texts = [f"★{r.get('rating',0)} — {r.get('text','')[:200]}" for r in reviews if r.get("text")]
            if texts:
                review_blocks.append(f"--- {comp_name} ---\n" + "\n".join(texts[:15]))

        if not review_blocks:
            return {}

        combined = "\n\n".join(review_blocks)[:8000]

        prompt = f"""{ctx.to_prompt_block()}

You are a customer insights analyst with expertise in extracting competitive intelligence
from customer reviews for local service businesses.

COMPETITOR REVIEWS (our competitors' Google Maps reviews):
{combined}

TASK: Extract competitive intelligence that helps {ctx.client_name} win more customers.

Analyze patterns across ALL competitor reviews and return ONLY valid JSON:
{{
  "competitor_pain_points": [
    {{
      "issue": "Specific problem customers experience at competitors",
      "frequency": "high|medium|low",
      "quote_example": "Exact short quote from a review illustrating this"
    }}
  ],
  "competitor_strengths": [
    {{
      "strength": "What competitors do well",
      "frequency": "high|medium|low"
    }}
  ],
  "content_opportunities": [
    {{
      "topic": "Content topic we should create to address this gap",
      "angle": "How to position {ctx.client_name} as the solution",
      "suggested_title": "SEO page or blog title"
    }}
  ],
  "emotional_triggers": [
    "Words/phrases that appear in positive reviews",
    "Words/phrases that appear in negative reviews"
  ],
  "citation_paragraphs": [
    "Ready-to-use paragraph showing how {ctx.client_name} solves the #1 competitor pain point. Include specifics. Under 100 words.",
    "Second paragraph addressing the #2 pain point."
  ],
  "review_themes": [
    "Top theme 1",
    "Top theme 2",
    "Top theme 3"
  ]
}}

Focus on ACTIONABLE insights. Return ONLY valid JSON, no explanation.
"""
        raw = self.call_claude(prompt, max_tokens=3000, model="claude-sonnet-4-6")
        if not raw:
            return {}
        try:
            return json.loads(strip_json_fences(raw))
        except Exception:
            return {}

    def run(self):
        self.log("Reviews Intelligence starting...")
        self.set_status("working", "Checking configuration")

        # Prereq: need competitors in DB
        comp_rows = db_execute(
            "SELECT name, url, location FROM competitors "
            "WHERE tenant_id=? AND tier IN (1,2) AND url != '' "
            "ORDER BY competitor_score DESC LIMIT ?",
            (self.ctx.tenant_id, self.MAX_COMPETITORS)
        )
        if not comp_rows:
            self.log("No competitors with URLs found. Run Competitor Intel first.", "warning")
            self.set_status("idle", "No competitors — run Competitor Intel first")
            return

        # Check Apify availability
        from denzo.agents.utils.apify_service import ApifyService
        apify = ApifyService(log_fn=lambda m, l="info": self.log(m, l))

        if not apify.available():
            self.log(
                "Apify API key not set — Reviews Intelligence requires Apify. "
                "Add apify_api_key in Platform Settings.",
                "warning"
            )
            self.set_status("idle", "Requires apify_api_key in Platform Settings")
            return

        competitors = [dict(r) for r in comp_rows]
        self.log(f"Scraping reviews for {len(competitors)} competitors...")

        # Build Google Maps search URLs from competitor names + location
        # We use the competitor name + city as the search query since we may not
        # have direct Google Maps place URLs
        all_competitor_reviews = []

        for comp in competitors:
            if self.should_stop():
                break

            name     = comp["name"]
            city     = comp.get("location") or self.ctx.primary_city or ""
            comp_url = comp.get("url", "")

            self.set_status("working", f"Fetching reviews: {name[:40]}")
            self.log(f"Searching reviews: {name} ({city})...")

            # Use Google Maps URL format to search for place reviews
            # compass/google-maps-reviews-scraper accepts Google Maps URLs or place names
            search_query = f"{name} {city}".strip()

            reviews = apify.get_reviews(
                place_urls=[f"https://www.google.com/maps/search/{search_query.replace(' ', '+')}"],
                max_reviews_per_place=self.MAX_REVIEWS_PER_COMPETITOR
            )

            if not reviews:
                # Fallback: try the competitor's own URL as context
                self.log(f"No reviews via Maps search for {name} — skipping", "info")
                all_competitor_reviews.append({"name": name, "reviews": []})
                continue

            self.log(f"  → {len(reviews)} reviews collected for {name}", "success")
            all_competitor_reviews.append({"name": name, "reviews": reviews})

        # Filter to only competitors with reviews
        with_reviews = [c for c in all_competitor_reviews if c["reviews"]]
        if not with_reviews:
            self.log(
                "No reviews collected. This may be because the competitor Google Maps "
                "place URLs need to be direct Maps links. Reviews Intelligence works best "
                "when competitors have direct Google Maps place URLs.",
                "warning"
            )
            self.set_status("idle", "No reviews collected — see log for details")
            return

        total_reviews = sum(len(c["reviews"]) for c in with_reviews)
        self.log(f"Total reviews collected: {total_reviews} from {len(with_reviews)} competitors")

        # Log sample reviews
        for cr in with_reviews[:2]:
            for r in cr["reviews"][:2]:
                self.log(
                    f"[{cr['name']}] ★{r.get('rating',0)} — {r.get('text','')[:80]}...",
                    "info"
                )

        # AI synthesis
        self.set_status("working", "Synthesizing review insights with AI")
        self.log("Analyzing competitor reviews with Claude NLP...")
        report = self._synthesize_reviews(with_reviews)

        if not report:
            self.log("AI synthesis returned empty report.", "warning")
            self.set_status("done", "Reviews collected but synthesis failed")
            return

        # Log insights
        pain_points = report.get("competitor_pain_points", [])
        content_opps = report.get("content_opportunities", [])
        themes = report.get("review_themes", [])

        if pain_points:
            self.log(f"Competitor pain points found: {len(pain_points)}", "success")
            for pp in pain_points[:3]:
                freq = pp.get("frequency", "")
                self.log(
                    f"  [{freq}] {pp.get('issue','')} — \"{pp.get('quote_example','')}\"",
                    "warning"
                )

        if content_opps:
            self.log(f"Content opportunities: {len(content_opps)}", "success")
            for opp in content_opps[:3]:
                self.log(f"  → {opp.get('suggested_title','')}", "info")

        if themes:
            self.log(f"Top themes: {' | '.join(themes[:5])}", "info")

        # Add content opportunities as high-priority keywords
        for opp in content_opps:
            title = opp.get("suggested_title", "")
            if title:
                self.add_keyword(title, category="review_opportunity", priority="high")

        # Save full report
        report["total_reviews_analyzed"] = total_reviews
        report["competitors_analyzed"]   = len(with_reviews)
        report["source"]                 = "apify_real"

        db_write(
            "INSERT OR REPLACE INTO settings (tenant_id, key, value, updated_at) VALUES (?,?,?,CURRENT_TIMESTAMP)",
            (self.ctx.tenant_id, "reviews_intelligence", json.dumps(report))
        )

        self.log(
            f"Reviews Intelligence complete: {total_reviews} reviews analyzed, "
            f"{len(pain_points)} pain points, {len(content_opps)} content opportunities.",
            "success"
        )
        self.set_status(
            "done",
            f"{total_reviews} reviews · {len(pain_points)} pain points · {len(content_opps)} opportunities"
        )
