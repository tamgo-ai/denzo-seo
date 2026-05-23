"""
Programmatic SEO — Layer 3
Generates content for all planned pages using Claude.
Works through pages in 'draft' status and writes full HTML content.
"""
import json
from denzo.agents.base_agent import TenantAwareBaseAgent, ClientContext, db_execute, db_write


class ProgrammaticSEO(TenantAwareBaseAgent):

    PREREQUISITES = ["E-E-A-T Architect", "Schema Engineer", "Vertical Matrix Generator"]
    MIN_KEYWORDS = 10

    def __init__(self, ctx: ClientContext):
        super().__init__("Programmatic SEO", ctx, layer=3, color="orange")

    def _generate_page_content(self, page: dict, style_guide: dict = None, site_images: list = None,
                               brand_voice: dict = None, data_intel: dict = None,
                               reviews_intel: dict = None) -> str:
        ctx = self.ctx
        title    = page.get("title", "")
        keyword  = page.get("target_keyword", title)
        location = page.get("location", ctx.primary_city or "")
        ptype    = page.get("type", "service")
        notes    = page.get("notes", "")

        faq_schema = db_execute(
            "SELECT value FROM settings WHERE tenant_id=? AND key='schema_faq'",
            (ctx.tenant_id,)
        )
        faq_block = faq_schema[0]["value"] if faq_schema else ""

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

        # Build Data Intelligence block
        data_intel_block = ""
        if data_intel:
            pain_points = data_intel.get('pain_points', [])
            citation_bait = data_intel.get('citation_bait_paragraphs', [])
            data_stories = data_intel.get('data_stories', [])
            data_intel_block = f"""
ORIGINAL DATA TO WEAVE IN (use these to make content non-commodity and citation-worthy):
Pain points real users are discussing: {pain_points[:3]}
Data stories to reference: {data_stories[:2]}
Citation bait paragraphs you can adapt: {citation_bait[:1]}

Rules:
- Reference at least one real data point from the above in the content
- Pain points should be acknowledged and addressed directly
- The content should feel like it was written by someone who has seen these exact problems
"""

        # Build style context block
        style_block = ""
        if style_guide:
            tone = style_guide.get("writing_tone", "")
            tone_adj = ", ".join(style_guide.get("tone_adjectives", []))
            notes_brand = style_guide.get("content_notes", "")
            layout = style_guide.get("layout_style", "")
            style_block = f"""
BRAND STYLE GUIDE:
- Writing tone: {tone}
- Tone adjectives: {tone_adj}
- Layout style: {layout}
- Content notes: {notes_brand}
"""

        # Build image reference block (provide up to 5 relevant images)
        image_block = ""
        if site_images:
            # Prefer hero/services context images
            priority = [img for img in site_images if img.get("context") in ("hero", "services", "about", "gallery")]
            sample = (priority + site_images)[:5]
            img_lines = []
            for img in sample:
                alt = img.get("alt", "")
                url = img.get("url", "")
                ctx_label = img.get("context", "general")
                img_lines.append(f'  - {url} | alt="{alt}" | context={ctx_label}')
            image_block = "\nAVAILABLE REAL IMAGES FROM CLIENT WEBSITE (use these in the content):\n" + "\n".join(img_lines)

        # Industry-aware CTA labels — covers 14 verticals
        industry = ctx.industry_vertical or "general"
        _cta_by_industry = {
            "automotive_dealership":  {"service": "Schedule a Test Drive →",       "about": "Contact Our Team →",      "location": "Get Directions →",  "inventory": "Browse Our Inventory →", "financing": "Apply for Financing →", "dealer": "Contact Our Team →"},
            "car_dealership":         {"service": "Schedule a Test Drive →",       "about": "Contact Our Team →",      "location": "Get Directions →",  "inventory": "Browse Our Inventory →", "financing": "Apply for Financing →", "dealer": "Contact Our Team →"},
            "auto_dealership":        {"service": "Schedule a Test Drive →",       "about": "Contact Our Team →",      "location": "Get Directions →",  "inventory": "Browse Our Inventory →", "financing": "Apply for Financing →", "dealer": "Contact Our Team →"},
            "auto_body_shop":         {"service": "Get a Free Estimate →",         "about": "Contact Us →",            "location": "Get Directions →"},
            "collision_repair":       {"service": "Get a Free Estimate →",         "about": "Contact Us →",            "location": "Get Directions →"},
            "saas_tech":              {"service": "Get a Free Demo →",             "about": "Talk to Our Team →",      "location": "Contact Us →"},
            "agency":                 {"service": "Get a Free Strategy Call →",    "about": "Meet Our Team →",         "location": "Contact Us →"},
            "restaurant":             {"service": "Reserve a Table →",             "about": "Contact Us →",            "location": "Get Directions →"},
            "law_firm":               {"service": "Get a Free Consultation →",     "about": "Contact Our Firm →",      "location": "Get Directions →"},
            "dental_clinic":          {"service": "Book an Appointment →",         "about": "Meet Our Dentists →",     "location": "Get Directions →"},
            "medical_clinic":         {"service": "Book an Appointment →",         "about": "Meet Our Doctors →",      "location": "Get Directions →"},
            "insurance_agency":       {"service": "Get a Free Quote →",            "about": "Meet Our Team →",         "location": "Get Directions →"},
            "real_estate":            {"service": "Schedule a Showing →",          "about": "Meet Our Agents →",       "location": "Get Directions →"},
            "home_services":          {"service": "Get a Free Quote →",            "about": "Contact Us →",            "location": "Get Directions →"},
            "veterinary":             {"service": "Book an Appointment →",         "about": "Meet Our Vets →",         "location": "Get Directions →"},
            "education_academy":      {"service": "Apply Now →",                   "about": "Learn More →",            "location": "Visit Our Campus →"},
            "online_courses":         {"service": "Enroll Now →",                  "about": "Meet the Instructor →",   "location": "Contact Us →"},
            "coaching":               {"service": "Book a Discovery Call →",       "about": "Meet Your Coach →",       "location": "Contact Us →"},
            "ecommerce":              {"service": "Shop Now →",                    "about": "Our Story →",             "location": "Contact Us →"},
            "gym":                    {"service": "Start Your Free Trial →",       "about": "Meet Our Team →",         "location": "Get Directions →"},
            "hotel":                  {"service": "Check Availability →",          "about": "About Our Hotel →",       "location": "Get Directions →"},
            "spa":                    {"service": "Book a Treatment →",            "about": "Meet Our Team →",         "location": "Get Directions →"},
            "financial_services":     {"service": "Get a Free Consultation →",     "about": "Meet Our Advisors →",     "location": "Get Directions →"},
        }
        cta_map = _cta_by_industry.get(industry, {"service": "Get a Free Consultation →", "about": "Contact Us Today →", "location": "Get Directions →"})
        cta_label = cta_map.get(ptype, cta_map.get("service", "Contact Us →"))

        # Build Reviews Intelligence block
        reviews_block = ""
        if reviews_intel:
            pain_points = reviews_intel.get("competitor_pain_points", [])[:3]
            strengths   = reviews_intel.get("competitor_strengths", [])[:3]
            triggers    = reviews_intel.get("emotional_triggers", [])[:8]
            opps        = reviews_intel.get("content_opportunities", [])[:2]
            citations   = reviews_intel.get("citation_paragraphs", [])[:1]
            if pain_points or triggers:
                reviews_block = f"""
COMPETITOR INTELLIGENCE (from real Google Maps reviews — use this to make content win):
- Top competitor pain points (what their customers HATE): {[p.get('issue', p.get('theme','')) for p in pain_points]}
- Emotional triggers that convert (use these exact phrases): {triggers}
- Competitor strengths to acknowledge and surpass: {[s.get('strength', s.get('theme','')) for s in strengths]}
{f'- Ready-to-use authority paragraph: "{citations[0]}"' if citations else ''}
{f'- Content angle for this page: {opps[0].get("angle", opps[0].get("description","")) if opps else ""}' if opps else ''}

Rules: Address at least one competitor pain point directly. Use 2-3 emotional triggers naturally. Position {ctx.client_name} as the solution.
"""

        prompt = f"""{ctx.to_prompt_block()}
{brand_voice_block}{data_intel_block}{reviews_block}{style_block}{image_block}

Write a complete, conversion-optimized HTML content block for this page.
CRITICAL: This business is in the "{industry}" industry. Write content ONLY appropriate for this industry. Do not reference other industries.

Title: {title}
Target keyword: {keyword}
Location: {location}
Page type: {ptype}
Notes: {notes}

OUTPUT STRUCTURE — follow this exact HTML structure in order:

1. HERO SECTION — wrap in <div class="hero-section">:
   - <div class="hero-badge">★ [Short trust signal, e.g. "Certified Google Partner" or "Serving California Since 2018"]</div>
   - <h2 class="hero-h1">[exact target keyword as main heading]</h2>
   - <p class="hero-lead">[1-2 sentence direct answer: what this is + primary benefit]</p>
   - <a href="tel:{ctx.phone}" class="btn-primary">{cta_label} →</a>
   - <div class="trust-bar"><span>✓ [differentiator 1]</span><span>✓ [differentiator 2]</span><span>✓ [differentiator 3]</span></div>

2. STATS BAR — wrap in <div class="stats-bar">:
   - 3-4 <div class="stat"><strong>[number]</strong><span>[label]</span></div>
   - Use real or plausible numbers from the business context (years, clients, locations, certifications)

3. INTRO SECTION — <h2> + 2 paragraphs. First paragraph answers "why choose {ctx.client_name} for {keyword}". Include specific facts, not vague claims.

4. SERVICES GRID — <h2>Our [keyword-related] Services</h2> + <div class="services-grid"> with 4-6 <div class="service-card">:
   - Each card: <h3>[service name]</h3> + <p>[2-sentence description]</p>

5. PROCESS SECTION — <h2>How It Works</h2> + <div class="process-steps"> with 3-4 <div class="step">:
   - Each step: <div class="step-num">[number]</div><h3>[step title]</h3><p>[description]</p>

6. WHY US SECTION — <h2>Why {ctx.client_name}?</h2> + 3-4 <p> paragraphs using E-E-A-T signals. Include specific certifications, years, client outcomes. Mention phone {ctx.phone}.

7. SERVICE AREA — <h2>Serving [location area]</h2> + paragraph mentioning: {', '.join(ctx.service_cities[:8]) if ctx.service_cities else location}

8. FAQ SECTION — <h2>Frequently Asked Questions</h2> + <div itemscope itemtype="https://schema.org/FAQPage"> with 5 Q&A using proper schema markup:
   <div itemscope itemprop="mainEntity" itemtype="https://schema.org/Question">
     <h3 itemprop="name">[Question?]</h3>
     <div itemscope itemprop="acceptedAnswer" itemtype="https://schema.org/Answer">
       <p itemprop="text">[Answer — 2-3 specific sentences]</p>
     </div>
   </div>

WRITING RULES:
- NO breadcrumbs, NO navigation, NO header/footer elements
- Use <h2> as the main heading (WordPress theme provides <h1> from page title — do NOT use <h1> tags)
- Every claim must be specific: numbers, timeframes, named services — never "many" or "various"
- Minimum 800 words total
- Add descriptive alt text to ALL <img> tags (include keyword + location)
- If real images were provided above, embed 1-2 <img> tags with exact src URLs
- Start your output with EXACTLY this comment on line 1: <!-- META_DESC: [your 120-155 char SEO meta description here] -->
- Then output the HTML fragment (no <html>, <body>, <head> tags)
"""
        raw = self.call_claude(prompt, max_tokens=4000, model="claude-sonnet-4-6")
        if not raw:
            return raw, ""

        # Extract meta description from <!-- META_DESC: ... --> comment on line 1
        meta_desc = ""
        cleaned = raw.strip()
        if cleaned.startswith("<!-- META_DESC:"):
            end = cleaned.find("-->")
            if end > 0:
                meta_desc = cleaned[len("<!-- META_DESC:"):end].strip()[:160]
                cleaned = cleaned[end + 3:].strip()

        # Strip markdown code fences
        if cleaned.startswith("```"):
            parts = cleaned.split("```")
            for part in parts:
                candidate = part.strip()
                if candidate.startswith("html"):
                    candidate = candidate[4:].strip()
                if candidate.startswith("<"):
                    cleaned = candidate
                    break

        # Strip full HTML document wrappers — Claude occasionally generates these
        # even when told to output only fragments. If these reach WordPress they break the layout.
        import re as _re
        cleaned = _re.sub(r'<!DOCTYPE[^>]*>', '', cleaned, flags=_re.IGNORECASE)
        cleaned = _re.sub(r'<html[^>]*>', '', cleaned, flags=_re.IGNORECASE)
        cleaned = _re.sub(r'</html>', '', cleaned, flags=_re.IGNORECASE)
        cleaned = _re.sub(r'<head>.*?</head>', '', cleaned, flags=_re.IGNORECASE | _re.DOTALL)
        cleaned = _re.sub(r'<body[^>]*>', '', cleaned, flags=_re.IGNORECASE)
        cleaned = _re.sub(r'</body>', '', cleaned, flags=_re.IGNORECASE)
        cleaned = cleaned.strip()

        # Enforce no H1 tags — WordPress/site theme provides the H1 from the page title.
        # Claude ignores this instruction ~40% of the time, so post-process unconditionally.
        cleaned = _re.sub(r'<h1(\s|>)', lambda m: '<h2' + m.group(1), cleaned)
        cleaned = _re.sub(r'</h1>', '</h2>', cleaned)

        # Fallback meta description: extract first <p> text if Claude didn't include META_DESC
        if not meta_desc:
            p_match = _re.search(r'<p[^>]*>([^<]{30,})</p>', cleaned)
            if p_match:
                raw_text = _re.sub(r'<[^>]+>', '', p_match.group(1))
                meta_desc = raw_text.strip()[:155]

        return cleaned, meta_desc

    def run(self):
        self.log("Starting programmatic content generation...")
        self.set_status("working", "Loading draft pages")

        # Prereq check: need keywords in DB
        kw_check = db_execute(
            "SELECT COUNT(*) AS n FROM keywords WHERE tenant_id=?", (self.ctx.tenant_id,)
        )
        kw_total = kw_check[0]["n"] if kw_check else 0
        if kw_total < 10:
            self.log(
                f"Not enough keywords ({kw_total} found, need >= 10). "
                "Run Keyword Strategist first.", "warning"
            )
            self.set_status("idle", f"Waiting for keywords ({kw_total}/10)")
            return

        # Prereq check: need at least one Layer 2 agent to have produced pages (E-E-A-T or Schema)
        layer2_done = db_execute(
            "SELECT COUNT(*) AS n FROM agents WHERE tenant_id=? AND name IN ('E-E-A-T Architect','Schema Engineer') AND status='done'",
            (self.ctx.tenant_id,)
        )
        l2_count = layer2_done[0]["n"] if layer2_done else 0
        if l2_count < 1:
            self.log(
                "Layer 2 agents have not completed (E-E-A-T Architect, Schema Engineer). "
                "Run them first to plan content priorities.", "warning"
            )
            self.set_status("idle", "Waiting for Layer 2 agents")
            return

        MAX_PAGES = 200  # Hard cap: E-E-A-T Architect is capped at 150; this is a safety floor
        # Load LocalBusiness schema once — embedded in every page's schema_markup
        lb_row = db_execute(
            "SELECT value FROM settings WHERE tenant_id=? AND key='schema_local_business'",
            (self.ctx.tenant_id,)
        )
        schema_local_business = lb_row[0]["value"] if lb_row else None
        if schema_local_business:
            self.log("LocalBusiness schema loaded for page embedding.", "info")

        pages = db_execute(
            "SELECT id, title, slug, type, location, target_keyword, notes FROM pages WHERE tenant_id=? AND status='draft' ORDER BY id LIMIT ?",
            (self.ctx.tenant_id, MAX_PAGES)
        )

        if not pages:
            self.log("No draft pages found. Run E-E-A-T Architect or Keyword Strategist first.", "warning")
            self.set_status("idle", "No pages to process yet. Run E-E-A-T Architect first.")
            return

        # Load style guide and images if available
        style_guide = None
        site_images = None
        brand_voice = None
        data_intel  = None

        style_row = db_execute(
            "SELECT value FROM settings WHERE tenant_id=? AND key='site_style_guide'",
            (self.ctx.tenant_id,)
        )
        if style_row:
            try:
                style_guide = json.loads(style_row[0]["value"])
                self.log("Site style guide loaded.", "info")
            except Exception:
                pass

        images_rows = db_execute(
            "SELECT url, alt, context, description FROM site_images "
            "WHERE tenant_id=? AND analyzed=1 ORDER BY context LIMIT 10",
            (self.ctx.tenant_id,)
        )
        if images_rows:
            site_images = [dict(r) for r in images_rows]
            self.log(f"Site images loaded: {len(site_images)} real images available.", "info")

        # Load Brand Voice DNA
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

        # Load Data Intelligence report
        di_row = db_execute(
            "SELECT value FROM settings WHERE tenant_id=? AND key='data_intelligence_report'",
            (self.tenant_id,)
        )
        if di_row:
            try:
                data_intel = json.loads(di_row[0]["value"])
                self.log("Data Intelligence report loaded.", "info")
            except Exception:
                pass

        # Load Reviews Intelligence — competitor pain points + content opportunities
        reviews_intel = None
        ri_row = db_execute(
            "SELECT value FROM settings WHERE tenant_id=? AND key='reviews_intelligence'",
            (self.tenant_id,)
        )
        if ri_row:
            try:
                reviews_intel = json.loads(ri_row[0]["value"])
                self.log("Reviews Intelligence loaded — competitor insights available.", "info")
            except Exception:
                pass

        self.log(f"Found {len(pages)} draft pages to generate content for.")
        done = 0

        for page in pages:
            if self.should_stop():
                self.log("Stopped by user.", "warning")
                break

            page_dict = dict(page)
            title = page_dict.get("title", "")
            self.set_status("working", f"Writing: {title[:60]}")
            self.log(f"Generating: {title}")

            content, meta_desc = self._generate_page_content(
                page_dict,
                style_guide=style_guide,
                site_images=site_images,
                brand_voice=brand_voice,
                data_intel=data_intel,
                reviews_intel=reviews_intel,
            )
            if not content:
                self.log(f"Skipped (empty response): {title}", "warning")
                continue

            db_write(
                "UPDATE pages SET content=?, meta_description=?, quality_score=65, schema_markup=?, "
                "status='ready', updated_at=CURRENT_TIMESTAMP WHERE id=? AND tenant_id=?",
                (content, meta_desc, schema_local_business, page_dict["id"], self.ctx.tenant_id)
            )
            done += 1
            self.log(f"✓ {title} ({len(content)} chars)", "success")

        # Get examples of generated page titles
        example_rows = db_execute(
            "SELECT title FROM pages WHERE tenant_id=? AND status='ready' AND content IS NOT NULL ORDER BY id DESC LIMIT 3",
            (self.ctx.tenant_id,)
        )
        examples = [r["title"] for r in (example_rows or [])] if example_rows else []
        self.log_result("Pages generated", done, examples)
        self.set_status("done", f"{done} pages written")
