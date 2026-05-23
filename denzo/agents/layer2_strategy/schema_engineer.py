"""
Schema Engineer — Layer 2
Generates Schema.org JSON-LD markup for the business and its pages.
"""
import json
from denzo.agents.base_agent import TenantAwareBaseAgent, ClientContext, db_execute, db_write, strip_json_fences


class SchemaEngineer(TenantAwareBaseAgent):

    MIN_KEYWORDS = 5

    def __init__(self, ctx: ClientContext):
        super().__init__("Schema Engineer", ctx, layer=2, color="violet")

    def _business_type_schema(self) -> str:
        mapping = {
            "auto_body_shop":    "AutoRepair",
            "dental_clinic":     "Dentist",
            "medical_clinic":    "MedicalBusiness",
            "law_firm":          "LegalService",
            "insurance_agency":  "InsuranceAgency",
            "restaurant":        "Restaurant",
            "real_estate":       "RealEstateAgent",
            "home_services":     "HomeAndConstructionBusiness",
            "veterinary":        "VeterinaryCare",
            "education_academy": "EducationalOrganization",
            "online_courses":    "EducationalOrganization",
            "coaching":          "ProfessionalService",
            "saas_tech":         "SoftwareApplication",
            "agency":            "ProfessionalService",
            "ecommerce":         "Store",
        }
        return mapping.get(self.ctx.industry_vertical, "LocalBusiness")

    def run(self):
        self.log("Generating Schema.org markup...")
        self.set_status("working", "Building LocalBusiness schema")
        ctx = self.ctx

        # Prereq check: need at least some keywords before building schema
        kw_check = db_execute(
            "SELECT COUNT(*) AS n FROM keywords WHERE tenant_id=?", (ctx.tenant_id,)
        )
        kw_total = kw_check[0]["n"] if kw_check else 0
        if kw_total < 5:
            self.log(
                f"Not enough keywords to inform schema ({kw_total} found, need >= 5). "
                "Run Keyword Strategist first.", "warning"
            )
            self.set_status("idle", f"Waiting for keywords ({kw_total}/5)")
            return

        schema_type = self._business_type_schema()

        # LocalBusiness schema
        local_business = {
            "@context": "https://schema.org",
            "@type": ["LocalBusiness", schema_type],
            "name": ctx.client_name,
            "url": ctx.website_url or ctx.domain,
            "telephone": ctx.phone,
            "address": {
                "@type": "PostalAddress",
                "streetAddress": ctx.address,
                "addressLocality": ctx.primary_city or ctx.service_cities[0] if ctx.service_cities else "",
                "addressRegion": ctx.state,
                "addressCountry": "US"
            },
            "description": ctx.tagline,
            "areaServed": ctx.service_cities or [ctx.primary_city],
            "hasOfferCatalog": {
                "@type": "OfferCatalog",
                "name": "Services",
                "itemListElement": [
                    {"@type": "Offer", "itemOffered": {"@type": "Service", "name": svc}}
                    for svc in ctx.services[:8]
                ]
            }
        }

        self.log("LocalBusiness schema generated", "success")

        # Generate FAQ schema using Claude
        self.set_status("working", "Generating schema markup with AI")
        services_str = ", ".join(ctx.services[:6]) if ctx.services else "our services"
        city = ctx.primary_city or (ctx.service_cities[0] if ctx.service_cities else "")

        prompt = f"""{ctx.to_prompt_block()}

You are a senior Schema.org specialist and structured data expert with 12 years of experience generating FAQ markup that earns rich results in Google Search and gets cited by AI overviews.

Generate 8 realistic FAQ questions and answers for this business that would appear in Google's FAQ rich results and be cited by AI like ChatGPT and Perplexity.

Focus on:
- Pricing and process questions
- Service-specific questions
- Location/availability questions
- Trust/quality questions

Return a JSON array:
[
  {{"question": "...", "answer": "..."}}
]

Each answer: 2-4 sentences, specific, factual, mentions the business name and/or location once.
Return ONLY valid JSON array.
"""
        raw = self.call_claude(prompt, max_tokens=2000)
        faqs = []
        if raw:
            try:
                faqs = json.loads(strip_json_fences(raw, "["))
            except Exception:
                pass

        faq_schema = {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": faq["question"],
                    "acceptedAnswer": {
                        "@type": "Answer",
                        "text": faq["answer"]
                    }
                }
                for faq in faqs
            ]
        }

        self.log(f"FAQ schema: {len(faqs)} Q&As generated", "success")

        # Save schemas to DB settings
        db_write(
            "INSERT OR REPLACE INTO settings (tenant_id, key, value) VALUES (?,?,?)",
            (ctx.tenant_id, "schema_local_business", json.dumps(local_business, ensure_ascii=False))
        )
        db_write(
            "INSERT OR REPLACE INTO settings (tenant_id, key, value) VALUES (?,?,?)",
            (ctx.tenant_id, "schema_faq", json.dumps(faq_schema, ensure_ascii=False))
        )

        # Log FAQs for visibility
        for faq in faqs[:3]:
            self.log(f"Q: {faq.get('question','')}", "info")

        # ── Generate additional world-class schemas via Claude ────────────────
        self.set_status("working", "Generating Review + Service + HowTo + Speakable schemas")

        all_cities = ctx.all_cities
        services_list = ctx.services[:6] if ctx.services else ["our services"]
        certifications_list = ctx.certifications[:6] if ctx.certifications else []

        domain = ctx.domain or ctx.website_url or ""
        primary_svc = services_list[0] if services_list else "our services"
        svc_slug = primary_svc.lower().replace(" ", "-").replace("&", "and")

        # ── Breadcrumb + Speakable: generated in pure Python (no Claude needed) ──
        schema_breadcrumb = {
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Home", "item": domain},
                {"@type": "ListItem", "position": 2, "name": primary_svc, "item": f"{domain}/services/{svc_slug}"}
            ]
        }
        schema_speakable = {
            "@context": "https://schema.org",
            "@type": "WebPage",
            "speakable": {
                "@type": "SpeakableSpecification",
                "cssSelector": ["h1", ".hero-lead", ".schema-speakable"]
            }
        }

        # ── Call 1: Review + Service schema (structured, predictable) ──────────
        review_service_prompt = f"""You are a Schema.org specialist for local SEO.

Business: {ctx.client_name}
Services: {json.dumps(services_list[:6])}
Cities: {json.dumps(all_cities[:8])}
Domain: {domain}
Industry: {ctx.industry_vertical}

Return a JSON object with 2 keys: "review" and "service".

review: A Review schema with a realistic 2-3 sentence customer review about their experience with this {ctx.industry_vertical} business. Include reviewRating (5/5).

service: A Service schema for "{primary_svc}". Include provider as LocalBusiness, areaServed as list of City objects for each city above, and hasOfferCatalog with 3-5 services.

Return ONLY valid JSON, no markdown."""

        raw_rs = self.call_claude(review_service_prompt, max_tokens=1500, model="claude-sonnet-4-6")
        schema_review = {}
        schema_service = {}
        if raw_rs:
            try:
                rs = json.loads(strip_json_fences(raw_rs, "{"))
                schema_review = rs.get("review", {})
                schema_service = rs.get("service", {})
            except Exception as e:
                self.log(f"Review/Service schema parse error: {str(e)[:80]}", "warning")

        # ── Call 2: HowTo schemas (creative, needs its own call) ──────────────
        howto_prompt = f"""Generate 2-3 HowTo schemas for a {ctx.industry_vertical} business called {ctx.client_name}.

Each HowTo must have:
- name: actionable title relevant to this industry
- description: 1-2 sentences
- step: 3-4 HowToStep objects each with name and text

Examples by industry:
- auto_body_shop: "How to File an Insurance Claim After an Accident", "How to Choose a Collision Repair Shop", "How to Get a Free Repair Estimate"
- automotive_dealership: "How to Test Drive a Car", "How to Apply for Auto Financing", "How to Trade In Your Vehicle"
- dental_clinic: "How to Prepare for a Root Canal", "How to Choose a Family Dentist"
- law_firm: "How to Find a Personal Injury Attorney", "How to File an Insurance Claim After an Accident"

Return a JSON object with key "howto_list" containing an array of 2-3 HowTo schemas.
Return ONLY valid JSON, no markdown."""

        raw_ht = self.call_claude(howto_prompt, max_tokens=1200, model="claude-sonnet-4-6")
        howto_list = []
        if raw_ht:
            try:
                ht = json.loads(strip_json_fences(raw_ht, "{"))
                howto_list = ht.get("howto_list", [])
            except Exception as e:
                self.log(f"HowTo schema parse error: {str(e)[:80]}", "warning")
        if not isinstance(howto_list, list):
            howto_list = []

        # Save all additional schemas to DB settings
        db_write(
            "INSERT OR REPLACE INTO settings (tenant_id, key, value) VALUES (?,?,?)",
            (ctx.tenant_id, "schema_review", json.dumps(schema_review, ensure_ascii=False))
        )
        db_write(
            "INSERT OR REPLACE INTO settings (tenant_id, key, value) VALUES (?,?,?)",
            (ctx.tenant_id, "schema_breadcrumb_template", json.dumps(schema_breadcrumb, ensure_ascii=False))
        )
        db_write(
            "INSERT OR REPLACE INTO settings (tenant_id, key, value) VALUES (?,?,?)",
            (ctx.tenant_id, "schema_service", json.dumps(schema_service, ensure_ascii=False))
        )
        db_write(
            "INSERT OR REPLACE INTO settings (tenant_id, key, value) VALUES (?,?,?)",
            (ctx.tenant_id, "schema_howto", json.dumps(howto_list, ensure_ascii=False))
        )
        db_write(
            "INSERT OR REPLACE INTO settings (tenant_id, key, value) VALUES (?,?,?)",
            (ctx.tenant_id, "schema_speakable", json.dumps(schema_speakable, ensure_ascii=False))
        )

        n_howto = len(howto_list)
        self.log(
            f"Schema complete: LocalBusiness ({schema_type}) + FAQ ({len(faqs)} Q&As) "
            f"+ Review + Service + {n_howto} HowTo + SpeakableSpecification",
            "success"
        )
        self.set_status(
            "done",
            f"Schema generated: {schema_type} + FAQ ({len(faqs)} Q&As) + Review + Service + {n_howto} HowTo + Speakable"
        )
