"""
E-E-A-T Architect — Layer 2
Builds an E-E-A-T content strategy and generates high-authority page blueprints.
"""
import json
import re
from denzo.agents.base_agent import TenantAwareBaseAgent, ClientContext, db_execute, strip_json_fences


class EEATArchitect(TenantAwareBaseAgent):

    MIN_KEYWORDS = 10

    def __init__(self, ctx: ClientContext):
        super().__init__("E-E-A-T Architect", ctx, layer=2, color="indigo")

    def run(self):
        self.log("Building E-E-A-T content strategy...")
        self.set_status("working", "Analyzing business authority signals")
        ctx = self.ctx

        # Prereq check: need at least 10 keywords before building strategy
        kw_check = db_execute(
            "SELECT COUNT(*) AS n FROM keywords WHERE tenant_id=?", (ctx.tenant_id,)
        )
        kw_total = kw_check[0]["n"] if kw_check else 0
        if kw_total < 10:
            self.log(
                f"Not enough keywords to build strategy ({kw_total} found, need >= 10). "
                "Run Keyword Strategist first.", "warning"
            )
            self.set_status("idle", f"Waiting for keywords ({kw_total}/10)")
            return

        # Load top keywords from DB
        kw_rows = db_execute(
            "SELECT keyword, intent, category, priority FROM keywords WHERE tenant_id=? "
            "ORDER BY CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END ASC LIMIT 50",
            (ctx.tenant_id,)
        )
        keywords_summary = [dict(r) for r in kw_rows] if kw_rows else []

        # Load technical audit results from Layer 1 (if available)
        audit_row = db_execute(
            "SELECT value FROM settings WHERE tenant_id=? AND key='technical_audit'",
            (ctx.tenant_id,)
        )
        technical_audit = {}
        if audit_row:
            try:
                technical_audit = json.loads(audit_row[0]["value"])
                self.log(f"Loaded Technical Audit from Layer 1 — score {technical_audit.get('score','?')}/100", "info")
            except Exception:
                pass

        audit_block = ""
        if technical_audit:
            audit_block = f"""
Technical SEO Audit Results (from Technical Auditor — Layer 1):
- Score: {technical_audit.get('score', '?')}/100
- Critical issues: {technical_audit.get('critical', [])}
- High priority fixes: {technical_audit.get('high_priority', [])}
- Quick wins: {technical_audit.get('quick_wins', [])}
- Summary: {technical_audit.get('summary', '')}

Factor these audit findings into your E-E-A-T strategy priorities.
"""

        # Load Reviews Intelligence — competitor pain points + content opportunities
        reviews_row = db_execute(
            "SELECT value FROM settings WHERE tenant_id=? AND key='reviews_intelligence'",
            (ctx.tenant_id,)
        )
        reviews_intel = {}
        if reviews_row:
            try:
                reviews_intel = json.loads(reviews_row[0]["value"])
                self.log("Loaded Reviews Intelligence — competitor insights available for strategy", "info")
            except Exception:
                pass

        reviews_block = ""
        if reviews_intel:
            pain_points = reviews_intel.get("competitor_pain_points", [])[:4]
            strengths   = reviews_intel.get("competitor_strengths", [])[:3]
            triggers    = reviews_intel.get("emotional_triggers", [])[:6]
            opps        = reviews_intel.get("content_opportunities", [])[:3]
            if pain_points or opps:
                reviews_block = f"""
Competitor Intelligence (from real Google Maps reviews analysis):
- What customers HATE about competitors: {[p.get('issue', p.get('theme', '')) for p in pain_points]}
- Competitor strengths to out-position: {[s.get('strength', s.get('theme', '')) for s in strengths]}
- Emotional triggers that convert this audience: {triggers}
- High-value content opportunities identified: {[o.get('angle', o.get('suggested_title', o.get('description', ''))) for o in opps]}

Use this intelligence to:
1. Prioritize pages that directly address competitor pain points
2. Shape E-E-A-T angles around what competitors are failing at
3. Use emotional triggers to guide page messaging strategy
4. Turn content opportunities into high-priority pages
"""

        prompt = f"""{ctx.to_prompt_block()}

Top keywords:
{json.dumps(keywords_summary[:30], ensure_ascii=False)}
{audit_block}{reviews_block}

You are an E-E-A-T (Experience, Expertise, Authoritativeness, Trustworthiness) strategist.

Design a comprehensive content strategy that maximizes authority signals for this business.

Return a JSON object:
{{
  "authority_pillars": [
    {{
      "pillar": "Pillar name",
      "description": "What this pillar covers",
      "pages": ["Page title 1", "Page title 2"],
      "eeat_signals": ["signal 1", "signal 2"]
    }}
  ],
  "trust_signals": [
    "Trust signal to add to site (e.g. certifications display, review schema, staff bios)"
  ],
  "content_priorities": [
    {{
      "title": "Page or content title (max 60 chars)",
      "type": "service|location|faq|about|blog",
      "keyword": "primary target keyword",
      "eeat_angle": "How this page demonstrates E-E-A-T",
      "meta_description": "120-155 char SEO meta description for this page",
      "priority": "high|medium|low"
    }}
  ],
  "schema_recommendations": ["LocalBusiness", "Service", "FAQ", "Review"],
  "linking_strategy": "Internal linking hub-and-spoke description"
}}

Generate 4-5 authority pillars and 12-15 content priorities. Keep descriptions concise.
Return ONLY valid JSON. Do not add any text outside the JSON object.
"""
        self.set_status("working", "Generating E-E-A-T strategy with AI")
        # Sonnet for this complex structured-JSON task — Haiku truncates at this token count
        raw = self.call_claude(prompt, max_tokens=8000, model="claude-sonnet-4-6",
                              system=self.build_cacheable_system(), cache_system=True)

        if not raw:
            self.log("AI returned empty response", "error")
            self.set_status("error", "Empty API response")
            return

        # Use strip_json_fences to robustly extract JSON from any markdown wrapping
        cleaned = strip_json_fences(raw, start_char="{")

        # Parse JSON — with multi-strategy truncation recovery
        result = None
        try:
            result = json.loads(cleaned)
        except json.JSONDecodeError:
            self.log(f"JSON truncated ({len(cleaned)} chars) — attempting recovery", "warning")
            # Strategy 1: find last complete top-level key and close the object
            for end_marker in [']\n}', '],\n}', '] }', ']\n  }', '"}', '"}\n']:
                idx = cleaned.rfind(end_marker)
                if idx > 0:
                    try:
                        result = json.loads(cleaned[:idx + len(end_marker)])
                        self.log("JSON recovery successful (strategy 1)", "info")
                        break
                    except Exception:
                        continue
            # Strategy 2: extract just content_priorities array if object is truncated
            if result is None:
                m = re.search(r'"content_priorities"\s*:\s*(\[.*?\])', cleaned, re.DOTALL)
                if m:
                    try:
                        priorities_only = json.loads(m.group(1))
                        result = {"authority_pillars": [], "content_priorities": priorities_only,
                                  "trust_signals": [], "schema_recommendations": [], "linking_strategy": ""}
                        self.log("JSON recovery successful (strategy 2 — priorities only)", "info")
                    except Exception:
                        pass

        if result is None:
            self.log(f"JSON parse failed — preview: {cleaned[:200]}", "error")
            self.set_status("error", "Parse error — try running again")
            return

        pillars = result.get("authority_pillars", [])
        priorities = result.get("content_priorities", [])

        self.log(f"Strategy built: {len(pillars)} authority pillars, {len(priorities)} content priorities", "success")

        for pillar in pillars:
            self.log(f"Pillar: {pillar.get('pillar')} — {len(pillar.get('pages', []))} pages", "info")

        # Guard: cap total pages per client to prevent explosion on retries.
        # Default: 500 pages. Override per-client via settings key 'max_pages_cap'.
        cap_row = db_execute(
            "SELECT value FROM settings WHERE tenant_id=? AND key='max_pages_cap'",
            (ctx.tenant_id,)
        )
        MAX_PAGES_PER_CLIENT = int(cap_row[0]["value"]) if cap_row else 500
        existing_pages = db_execute(
            "SELECT COUNT(*) AS n FROM pages WHERE tenant_id=? AND status IN ('draft','ready','published')",
            (ctx.tenant_id,)
        )
        current_page_count = existing_pages[0]["n"] if existing_pages else 0
        remaining_slots = MAX_PAGES_PER_CLIENT - current_page_count

        if remaining_slots <= 0:
            self.log(
                f"Page cap reached ({current_page_count}/{MAX_PAGES_PER_CLIENT}). "
                "Increase cap in Settings (max_pages_cap) or run Programmatic SEO first.",
                "warning",
            )
            self.set_status("done", f"{len(pillars)} pillars · page cap reached ({current_page_count}/{MAX_PAGES_PER_CLIENT})")
            return

        if remaining_slots < len(priorities):
            self.log(
                f"Limiting to {remaining_slots} new pages (cap: {MAX_PAGES_PER_CLIENT}, "
                f"existing: {current_page_count})", "warning"
            )
            priorities = priorities[:remaining_slots]

        # Create page stubs from content priorities
        all_cities = ctx.all_cities  # includes primary_city + service_cities
        for item in priorities:
            if self.should_stop():
                break
            title = item.get("title", "")
            if not title:
                continue
            # Enforce 60-char title limit for SEO
            if len(title) > 60:
                title = title[:57] + "..."
            from denzo.db import slugify as _slugify
            slug = _slugify(title)
            meta_desc = item.get("meta_description", "")[:160] if item.get("meta_description") else ""

            # Extract location from keyword or title for geo/location pages
            location = None
            search_text = (item.get("keyword", "") + " " + title).lower()
            for city in all_cities:
                if city and city.lower() in search_text:
                    location = city
                    break

            self.add_page(
                title=title,
                slug=slug,
                page_type=item.get("type", "service"),
                location=location,
                target_keyword=item.get("keyword", ""),
                notes=item.get("eeat_angle", ""),
                meta_description=meta_desc,
            )

        trust_signals = result.get("trust_signals", [])
        for ts in trust_signals:
            self.log(f"Trust signal: {ts}", "info")

        self.log("E-E-A-T strategy complete.", "success")
        self.set_status("done", f"{len(pillars)} pillars, {len(priorities)} pages planned")
