"""
GEO Optimizer — Layer 3
Adds Generative Engine Optimization structure to pages so AI systems cite them.
Injects FAQ schema, definition patterns, structured facts.
"""
import json
import re
from denzo.agents.base_agent import TenantAwareBaseAgent, ClientContext, db_execute, db_write


class GEOOptimizer(TenantAwareBaseAgent):

    def __init__(self, ctx: ClientContext):
        super().__init__("GEO Optimizer", ctx, layer=4, color="teal")

    def _inject_geo(self, page: dict, brand_voice: dict = None) -> str:
        ctx = self.ctx
        content = page.get("content", "")
        title   = page.get("title", "")
        keyword = page.get("target_keyword", title)

        # Build Brand Voice DNA block
        brand_voice_block = ""
        if brand_voice:
            brand_voice_block = f"""
BRAND VOICE DNA — follow this exactly:
- Brand name: {brand_voice.get('brand_name', ctx.client_name)}
- Writing style: {brand_voice.get('writing_style', 'professional')}
- Years of experience to reference: {brand_voice.get('years_experience', '')}
- Clients served: {brand_voice.get('clients_served', '')}
- Founder voice: {brand_voice.get('founder_name', '')}
- Key proprietary insights to weave in: {brand_voice.get('key_insight_1', '')} / {brand_voice.get('key_insight_2', '')} / {brand_voice.get('key_insight_3', '')}
- Contrarian position: {brand_voice.get('contrarian_position', '')}
- Signature phrases to use: {brand_voice.get('phrases_to_use', '')}
- Phrases to NEVER use: {brand_voice.get('phrases_to_avoid', '')}

AUTHORITY SIGNAL RULES — include at least 2 of these in every piece:
1. First-person data: "In our experience with [X clients/years]..."
2. Named framework: Create a named methodology (e.g. "The [Brand] [Method/Framework/Approach]")
3. Contrarian position: "Most [industry players] will tell you X, but that's wrong because..."
4. Specific numbers: Use exact figures, percentages, timeframes — never vague estimates
5. Expert quote: "As {brand_voice.get('founder_name', 'our founder')}, puts it: '...'"
"""

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

Return the FULL improved HTML content. Return ONLY HTML, no explanation.
"""
        return self.call_claude(prompt, max_tokens=3000, model="claude-sonnet-4-6")

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
                        "UPDATE pages SET content=?, updated_at=CURRENT_TIMESTAMP "
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
