"""
Content Optimizer — Layer 3
Scores existing page content and rewrites low-quality pages.
"""
import json
from denzo.agents.base_agent import TenantAwareBaseAgent, ClientContext, db_execute, db_write, strip_json_fences


class ContentOptimizer(TenantAwareBaseAgent):

    def __init__(self, ctx: ClientContext):
        super().__init__("Content Optimizer", ctx, layer=4, color="yellow")

    MIN_SCORE = 70
    BATCH = 10

    def _score_and_fix(self, page: dict, brand_voice: dict = None) -> tuple[int, str]:
        """Returns (score, improved_content)."""
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

        # Two-pass approach: score first, then rewrite if needed (avoids token overflow)
        # PASS 1: Score only (fast, low tokens)
        score_prompt = f"""{ctx.to_prompt_block()}

Score this page content (0-100) on these 5 criteria. Be honest and critical.

Page title: {title}
Target keyword: {keyword}

CONTENT PREVIEW (first 2000 chars):
{content[:2000]}

Score on:
1. Keyword targeting — exact keyword in heading and first paragraph? (0-20)
2. Content depth — specific facts, numbers, process steps, not vague claims? (0-20)
3. E-E-A-T signals — certifications, experience, trust indicators? (0-20)
4. GEO readiness — definition pattern, FAQ schema, citable facts? (0-20)
5. Formatting — proper H2s, short paragraphs, calls to action? (0-20)

Return JSON only:
{{"score": 0-100, "issues": ["specific issue 1", "specific issue 2", "specific issue 3"]}}
"""
        raw = self.call_claude(score_prompt, max_tokens=400, model="claude-haiku-4-5-20251001")
        if not raw:
            return None, ""  # API failed — caller will skip DB update
        try:
            import re as _re
            cleaned = strip_json_fences(raw)
            result = json.loads(cleaned)
            score = int(result.get("score", 75))
            issues = result.get("issues", [])
        except Exception:
            # Try to extract just the score number
            m = _re.search(r'"score"\s*:\s*(\d+)', raw)
            score = int(m.group(1)) if m else 75
            issues = []

        if score >= self.MIN_SCORE:
            return score, ""

        # PASS 2: Rewrite with issues fixed (only if score < threshold)
        fix_prompt = f"""{ctx.to_prompt_block()}
{brand_voice_block}

Rewrite this page content to fix these specific issues:
{chr(10).join(f"- {i}" for i in issues[:5])}

Page title: {title}
Target keyword: {keyword}
Current score: {score}/100 (needs to reach {self.MIN_SCORE}+)

CURRENT CONTENT:
{content[:4000]}

Rules:
- Fix each issue listed above
- Keep the same HTML structure (hero-section, stats-bar, services-grid, FAQ)
- Use <h2> as main heading (NOT <h1>)
- Include exact target keyword in first heading and first paragraph
- Add specific numbers and facts
- Return ONLY the improved HTML fragment, no explanation
"""
        new_raw = self.call_claude(fix_prompt, max_tokens=5000, model="claude-sonnet-4-6")
        if not new_raw:
            # Retry with simplified prompt
            simple_prompt = f"Rewrite this page to score above {self.MIN_SCORE}/100. Fix: {', '.join(issues[:3])}.\n\nTarget keyword: {keyword}\n\nContent:\n{content[:3000]}\n\nReturn only improved HTML."
            new_raw = self.call_claude(simple_prompt, max_tokens=4000, model="claude-sonnet-4-6")
        if not new_raw:
            return score, ""
        cleaned2 = new_raw.strip()
        if cleaned2.startswith("```"):
            parts = cleaned2.split("```")
            for part in parts:
                candidate = part.strip()
                if candidate.startswith("html"):
                    candidate = candidate[4:].strip()
                if candidate.startswith("<"):
                    return score, candidate
        return score, cleaned2 if cleaned2.startswith("<") else ""

    def run(self):
        self.log("Starting content optimization — will run until all pages are optimized...")
        self.set_status("working", "Loading pages for review")

        # Prereq check: need pages with content (ready or published with unscored/low-quality)
        ready_check = db_execute(
            "SELECT COUNT(*) AS n FROM pages WHERE tenant_id=? AND status IN ('ready','published') "
            "AND content IS NOT NULL AND content != '' "
            "AND (quality_score IS NULL OR quality_score < ?)",
            (self.ctx.tenant_id, self.MIN_SCORE)
        )
        ready_count = ready_check[0]["n"] if ready_check else 0
        if ready_count == 0:
            self.log("No pages needing optimization found. All pages meet quality threshold or no pages exist.", "warning")
            self.set_status("idle", "All pages meet quality threshold — nothing to optimize")
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

        improved = 0
        skipped  = 0
        round_num = 0
        MAX_ROUNDS = 10  # cap: 10 rounds × BATCH(10) = max 100 pages per run

        while not self.should_stop() and round_num < MAX_ROUNDS:
            round_num += 1
            pages = db_execute(
                "SELECT id, title, slug, target_keyword, content FROM pages "
                "WHERE tenant_id=? AND status IN ('ready','published') AND content IS NOT NULL AND content != '' "
                "AND (quality_score IS NULL OR quality_score < ?) "
                "ORDER BY id LIMIT ?",
                (self.ctx.tenant_id, self.MIN_SCORE, self.BATCH)
            )

            if not pages:
                break

            self.log(f"Round {round_num}: optimizing {len(pages)} pages (threshold: {self.MIN_SCORE}/100)...")

            for page in pages:
                if self.should_stop():
                    break
                page_dict = dict(page)
                title = page_dict.get("title", "")
                self.set_status("working", f"Scoring: {title[:50]}")

                score, new_content = self._score_and_fix(page_dict, brand_voice=brand_voice)
                if score is None:
                    self.log(f"{title[:50]} → API failed, skipping", "warning")
                    continue
                self.log(f"{title[:50]} → score {score}/100")

                if new_content and len(new_content.strip()) > 200:
                    db_write(
                        "UPDATE pages SET content=?, quality_score=?, status='ready', "
                        "updated_at=CURRENT_TIMESTAMP WHERE id=? AND tenant_id=?",
                        (new_content, score, page_dict["id"], self.ctx.tenant_id)
                    )
                    self.log(f"✓ Improved: {title}", "success")
                    improved += 1
                else:
                    # Mark with score so it won't be re-selected next round
                    db_write(
                        "UPDATE pages SET quality_score=?, updated_at=CURRENT_TIMESTAMP "
                        "WHERE id=? AND tenant_id=?",
                        (score, page_dict["id"], self.ctx.tenant_id)
                    )
                    skipped += 1

        remaining = db_execute(
            "SELECT COUNT(*) n FROM pages WHERE tenant_id=? AND status IN ('ready','published') "
            "AND content IS NOT NULL AND (quality_score IS NULL OR quality_score < ?)",
            (self.ctx.tenant_id, self.MIN_SCORE)
        )
        left = remaining[0]["n"] if remaining else 0
        self.log(f"Optimization complete: {improved} improved, {skipped} already good. {left} pages remaining unscored.", "success")
        self.set_status("done", f"{improved} improved · {skipped} passed · {left} remaining")
