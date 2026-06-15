"""
GEO Optimizer — Layer 3
Adds Generative Engine Optimization structure to pages so AI systems cite them.
Injects FAQ schema, definition patterns, structured facts.
"""
import json
import re
from denzo.agents.base_agent import TenantAwareBaseAgent, ClientContext, db_execute, db_write


class GEOOptimizer(TenantAwareBaseAgent):

    PREREQUISITES = ["Programmatic SEO"]

    def __init__(self, ctx: ClientContext):
        super().__init__("GEO Optimizer", ctx, layer=4, color="teal")

    def _inject_geo(self, page: dict, brand_voice: dict = None) -> str:
        ctx = self.ctx
        content = page.get("content", "")
        title   = page.get("title", "")
        keyword = page.get("target_keyword", title)

        # Build Brand Voice DNA block
        brand_voice_block = ctx.to_brand_voice_block(brand_voice)

        prompt = f"""{ctx.to_prompt_block()}
{brand_voice_block}

You are a GEO (Generative Engine Optimization) specialist. Your goal is to make this page get cited by ChatGPT, Perplexity, Google AI Overviews, and other LLMs.

Page: {title}
Keyword: {keyword}

CURRENT CONTENT:
{content[:2500]}

Apply these GEO techniques:
1. Ensure first paragraph is a direct definition/answer pattern: "[Keyword] is..." or "[Business] provides..."
2. Add specific numbers, dates, facts (not vague — e.g. "14 manufacturer certifications" not "many certifications")
3. Add an inline FAQ section using Schema.org markup:
   <div itemscope itemtype="https://schema.org/FAQPage">
     <div itemscope itemprop="mainEntity" itemtype="https://schema.org/Question">
       <h3 itemprop="name">Question?</h3>
       <div itemscope itemprop="acceptedAnswer" itemtype="https://schema.org/Answer">
         <p itemprop="text">Answer.</p>
       </div>
     </div>
   </div>
4. Add an "Expert perspective" block: <blockquote>"According to [business name]'s team: [specific authoritative statement relevant to this page's topic]"</blockquote>
5. Include the full business NAP (Name, Address, Phone) once in a structured paragraph

GEO OPTIMIZATION RULES - PROVEN TECHNIQUES FOR AI CITATION (ChatGPT, Claude, Gemini, Perplexity, Copilot):

6. DIRECT ANSWER SENTENCES: For every key topic, add "Q: [question] A: [direct answer in 1-2 sentences]." AI models extract these verbatim. "Q: How much does collision repair cost in LA? A: At [business], collision repair typically costs $1,500-$4,500 with most jobs completed in 3-5 business days."

7. DEFINITION BLOCK: First paragraph MUST define the topic in 2 sentences. AI models use definitions to ground their answers.

8. STATISTICS & SPECIFICS: At least 3 unique data points per page. Numbers, percentages, exact timeframes. NEVER reuse stats across pages. "Average repair: 4.2 days" beats "fast repairs."

9. STRUCTURED COMPARISON TABLE: Add a comparison using <table> or structured <div> comparing this business to "alternatives" or "what to look for." AI models use comparison data for recommendations.

10. CITATION SNIPPETS: Wrap 2-3 key factual statements in <span class="citation-snippet"> tags. These sentences are the most likely to be extracted by AI. Each must be a complete, standalone fact.

11. ENTITY ASSOCIATIONS: Mention 2-3 nearby landmarks, neighborhoods, or well-known local entities. "Located 0.8 miles from [Landmark]" helps geo-association in AI vector databases.

12. AUTHOR ATTRIBUTION: Every page needs an "expert voice." Include: "According to [Name], [Title] at [Business] with [X] years of experience: '[direct quote]'"

13. LAST-UPDATED TIMESTAMP: Add a visible "Last updated: [date]" within the content. AI models weigh freshness signals heavily.

14. SEMANTIC HTML STRUCTURE: Use <article>, <section>, <address> tags. AI crawlers parse semantic HTML better than generic <div> soup.

15. INTERNAL CITATION NETWORK: Link to 2-3 other pages on this exact topic cluster using descriptive anchor text. AI models see internal links as topical authority signals.

16. COUNTERFACTUAL POSITIONING: Include ONE contrarian statement. "Most [industry] shops will tell you [X], but [Business] takes a different approach: [Y]." AI models cite unique perspectives over generic ones.

Return the FULL improved HTML content with ALL these 16 techniques applied. Return ONLY HTML, no explanation.
"""
        return self.call_claude(prompt, max_tokens=3000, model="claude-sonnet-4-6",
                               system=self.build_cacheable_system(), cache_system=True)

    BATCH = 20

    def run(self):
        self.log("Starting GEO optimization — will run until all pages are processed...")
        self.set_status("working", "Loading pages for GEO injection")

        # Check if there's anything to do
        total_check = db_execute(
            "SELECT COUNT(*) n FROM pages WHERE tenant_id=? AND status='ready' "
            "AND content IS NOT NULL AND content != ''",
            (self.ctx.tenant_id,)
        )
        if not total_check or total_check[0]["n"] == 0:
            self.log("No ready pages found. Run Programmatic SEO first.", "warning")
            self.set_status("idle", "No pages")
            return

        # Load Brand Voice DNA once
        brand_voice = None
        bv_row = db_execute(
            "SELECT value FROM settings WHERE tenant_id=? AND key='brand_voice'",
            (self.tenant_id,)
        )
        if bv_row:
            try:
                brand_voice = json.loads(bv_row[0]["value"])
                self.log("Brand Voice DNA loaded.", "info")
            except Exception:
                pass

        done = 0
        skipped = 0
        round_num = 0
        processed_ids = set()
        MAX_ROUNDS = 5  # cap: 5 rounds × BATCH(20) = max 100 pages per run

        while not self.should_stop() and round_num < MAX_ROUNDS:
            round_num += 1
            pages = db_execute(
                "SELECT id, title, slug, target_keyword, content FROM pages "
                "WHERE tenant_id=? AND status='ready' AND content IS NOT NULL AND content != '' "
                "AND (notes NOT LIKE '%[GEO]%' OR notes IS NULL) "
                "ORDER BY id LIMIT ?",
                (self.ctx.tenant_id, self.BATCH)
            )

            # Filter out already-processed pages in this run
            pages = [p for p in pages if p["id"] not in processed_ids]
            if not pages:
                break

            self.log(f"Round {round_num}: applying GEO to {len(pages)} pages...")

            for page in pages:
                if self.should_stop():
                    break
                page_dict = dict(page)
                pid   = page_dict["id"]
                title = page_dict.get("title", "")
                self.set_status("working", f"GEO: {title[:50]}")
                processed_ids.add(pid)

                improved = self._inject_geo(page_dict, brand_voice=brand_voice)
                if improved:
                    db_write(
                        "UPDATE pages SET content=?, notes=COALESCE(notes||' ','')|| '[GEO]', updated_at=CURRENT_TIMESTAMP "
                        "WHERE id=? AND tenant_id=?",
                        (improved, pid, self.ctx.tenant_id)
                    )
                    self.log(f"✓ GEO applied: {title}", "success")
                    done += 1
                else:
                    self.log(f"Skipped (empty response): {title}", "warning")
                    skipped += 1

        self.log(f"GEO optimization complete: {done} pages updated, {skipped} skipped.", "success")
        self.set_status("done", f"{done} pages GEO-optimized")
