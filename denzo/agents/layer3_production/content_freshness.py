"""
Content Freshness Agent — Layer 4
Re-evaluates and updates published pages older than 90 days.
Google rewards fresh content — this agent keeps the site evergreen.
Works for ALL verticals.
"""
import json
from datetime import datetime
from denzo.agents.base_agent import TenantAwareBaseAgent, ClientContext, db_execute, db_write, strip_json_fences


class ContentFreshness(TenantAwareBaseAgent):

    def __init__(self, ctx: ClientContext):
        super().__init__("Content Freshness", ctx, layer=4, color="lime")

    def run(self):
        self.log("Starting content freshness audit...")
        self.set_status("working", "Checking for stale published pages")
        ctx = self.ctx

        # Prereq: need at least 1 published page
        pub_check = db_execute(
            "SELECT COUNT(*) AS n FROM pages WHERE tenant_id=? AND status='published'",
            (self.ctx.tenant_id,)
        )
        if not (pub_check and pub_check[0]["n"] > 0):
            self.log("No published pages found. Run a Publisher agent first.", "warning")
            self.set_status("idle", "No published pages — run a Publisher first")
            return

        # Load pages older than 90 days, lowest quality first (up to 15 pages)
        stale_pages = db_execute(
            """SELECT id, title, slug, type, target_keyword, content, meta_description,
                      quality_score, updated_at
               FROM pages
               WHERE tenant_id=?
                 AND status='published'
                 AND updated_at < datetime('now', '-90 days')
                 AND content IS NOT NULL
               ORDER BY quality_score ASC
               LIMIT 15""",
            (self.ctx.tenant_id,)
        )

        if not stale_pages:
            self.log(
                "All published pages are fresh (< 90 days) — nothing to refresh.",
                "info"
            )
            self.set_status("done", "All pages fresh")
            return

        self.log(
            f"Found {len(stale_pages)} published pages older than 90 days — starting refresh...",
            "info"
        )

        current_year = datetime.utcnow().year
        refreshed = 0
        skipped = 0

        for page in stale_pages:
            if self.should_stop():
                break

            page_dict = dict(page)
            page_id = page_dict["id"]
            title = page_dict.get("title", "")
            slug = page_dict.get("slug", "")
            target_keyword = page_dict.get("target_keyword", title)
            content = page_dict.get("content", "")
            updated_at_str = page_dict.get("updated_at", "")
            quality_score = page_dict.get("quality_score", 0) or 0

            # Calculate age in days
            age_days = 999  # fallback
            if updated_at_str:
                try:
                    updated_dt = datetime.strptime(updated_at_str[:19], "%Y-%m-%d %H:%M:%S")
                    age_days = (datetime.utcnow() - updated_dt).days
                except ValueError:
                    pass

            self.set_status("working", f"Analyzing freshness: {title[:50]}")
            self.log(f"Checking: {title[:60]} (age: {age_days} days, score: {quality_score})")

            # PASS 1: Quick freshness analysis with Haiku (cheap)
            analysis_prompt = f"""{ctx.to_prompt_block()}

Analyze this page content for freshness issues. Look for:
- References to specific years that may be outdated (e.g. "In 2022...", "As of 2023...")
- Vehicle model years or specific product versions that may be outdated
- Prices or cost estimates that may have changed
- Regulatory or insurance information that may have changed
- Any "current" or "latest" references that may be stale

Page title: {title}
Target keyword: {target_keyword}

CONTENT (first 1500 chars):
{content[:1500]}

Return JSON only:
{{"needs_refresh": true|false, "stale_elements": ["element 1", "element 2"], "freshness_score": 0-100, "reason": "brief explanation"}}
"""
            raw_analysis = self.call_claude(
                analysis_prompt,
                max_tokens=400,
                model="claude-haiku-4-5-20251001"
            )

            if not raw_analysis:
                self.log(f"API failed for analysis of '{title[:50]}' — skipping", "warning")
                skipped += 1
                continue

            try:
                analysis_cleaned = strip_json_fences(raw_analysis, start_char="{")
                analysis = json.loads(analysis_cleaned)
            except json.JSONDecodeError:
                self.log(f"Analysis parse failed for '{title[:50]}' — skipping", "warning")
                skipped += 1
                continue

            needs_refresh = analysis.get("needs_refresh", False)
            freshness_score = int(analysis.get("freshness_score", 50))
            stale_elements = analysis.get("stale_elements", [])

            # Skip if content is fresh enough
            if not needs_refresh and freshness_score > 75:
                self.log(
                    f"  Skipping (fresh enough): freshness_score={freshness_score}, "
                    f"no stale elements detected."
                )
                skipped += 1
                # Bump updated_at so it won't be re-checked for another 90 days
                db_write(
                    "UPDATE pages SET updated_at=CURRENT_TIMESTAMP WHERE id=? AND tenant_id=?",
                    (page_id, self.ctx.tenant_id)
                )
                continue

            # PASS 2: Full refresh with Sonnet (only when needed)
            stale_str = (
                "\n".join(f"- {el}" for el in stale_elements[:5])
                if stale_elements
                else "- General content freshness update needed"
            )
            self.set_status("working", f"Refreshing: {title[:50]}")

            refresh_prompt = f"""{ctx.to_prompt_block()}

You are updating a published webpage to keep it fresh for {current_year}.

Page details:
- Title: {title}
- Slug: {slug}
- Target keyword: {target_keyword}
- Current freshness score: {freshness_score}/100
- Page age: {age_days} days

STALE ELEMENTS FOUND:
{stale_str}

CURRENT CONTENT:
{content[:5000]}

INSTRUCTIONS:
1. Keep the EXACT same HTML structure (all divs, classes, sections must remain)
2. Update ALL year references to {current_year} where appropriate (e.g. "As of {current_year}...")
3. Update any price estimates to reflect {current_year} market rates
4. If the content mentions vehicle models or product versions, update to current {current_year} equivalents
5. Add "Updated: {datetime.utcnow().strftime('%B %Y')}" in a visible but unobtrusive location (e.g. in a small paragraph near the top or bottom)
6. DO NOT change: target keyword, slug, URL structure, meta description (unless clearly outdated)
7. DO NOT change the overall page purpose or messaging
8. Make the content feel like it was just written today

Return ONLY the updated HTML content, no explanation or markdown fences.
"""
            new_content_raw = self.call_claude(
                refresh_prompt,
                max_tokens=6000,
                model="claude-sonnet-4-6"
            )

            if not new_content_raw or len(new_content_raw.strip()) < 200:
                self.log(
                    f"Refresh failed for '{title[:50]}' — AI returned empty/short response",
                    "warning"
                )
                skipped += 1
                continue

            # Strip any accidental markdown fences from the returned HTML
            new_content = new_content_raw.strip()
            if new_content.startswith("```"):
                parts = new_content.split("```")
                for part in parts:
                    candidate = part.strip()
                    if candidate.startswith("html"):
                        candidate = candidate[4:].strip()
                    if candidate.startswith("<"):
                        new_content = candidate
                        break

            # Update the page in DB — set status to 'ready' for re-publishing
            db_write(
                "UPDATE pages SET content=?, quality_score=72, status='ready', "
                "updated_at=CURRENT_TIMESTAMP WHERE id=? AND tenant_id=?",
                (new_content, page_id, self.ctx.tenant_id)
            )

            self.log(
                f"Refreshed: {title} (was {age_days} days old, "
                f"freshness {freshness_score} → ready for re-publish)",
                "success"
            )
            refreshed += 1

        total_processed = refreshed + skipped
        self.log(
            f"Content freshness audit complete: {refreshed} pages refreshed, "
            f"{skipped} pages skipped (fresh or API error).",
            "success"
        )
        self.set_status(
            "done",
            f"{refreshed} pages refreshed for {current_year}"
        )
