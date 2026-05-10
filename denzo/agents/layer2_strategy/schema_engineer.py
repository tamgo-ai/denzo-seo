"""
Schema Engineer — Layer 2
Generates Schema.org JSON-LD markup for the business and its pages.
"""
import json
from denzo.agents.base_agent import TenantAwareBaseAgent, ClientContext, db_execute, db_write, strip_json_fences


class SchemaEngineer(TenantAwareBaseAgent):

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

        additional_prompt = f"""{ctx.to_prompt_block()}

You are a Schema.org structured data specialist. Generate the following 4 schemas for this business.
Return a JSON array with exactly 4 objects in this order: [review, breadcrumb, service, howto_list, speakable].
Actually return a JSON object with these 5 keys.

Services: {json.dumps(services_list)}
Cities served: {json.dumps(all_cities[:8])}
Certifications: {json.dumps(certifications_list)}
Primary domain: {ctx.domain or ctx.website_url}
Primary city: {ctx.primary_city}
Industry vertical: {ctx.industry_vertical}

Return a JSON object with exactly these 5 keys:

{{
  "review": {{
    "@context": "https://schema.org",
    "@type": "Review",
    "itemReviewed": {{
      "@type": "LocalBusiness",
      "name": "{ctx.client_name}"
    }},
    "reviewRating": {{
      "@type": "Rating",
      "ratingValue": "5",
      "bestRating": "5"
    }},
    "author": {{"@type": "Person", "name": "Satisfied Customer"}},
    "reviewBody": "<WRITE an authentic 2-3 sentence review specific to this business vertical and services>"
  }},
  "breadcrumb": {{
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    "itemListElement": [
      {{"@type": "ListItem", "position": 1, "name": "Home", "item": "<domain>/"}},
      {{"@type": "ListItem", "position": 2, "name": "<primary_service>", "item": "<domain>/services/<slug>"}}
    ]
  }},
  "service": {{
    "@context": "https://schema.org",
    "@type": "Service",
    "serviceType": "<primary service name>",
    "provider": {{
      "@type": "LocalBusiness",
      "name": "{ctx.client_name}"
    }},
    "areaServed": [<list of {{"@type": "City", "name": "..."}} objects for each city>],
    "hasOfferCatalog": {{
      "@type": "OfferCatalog",
      "name": "<primary service> Services"
    }}
  }},
  "howto_list": [
    {{
      "@context": "https://schema.org",
      "@type": "HowTo",
      "name": "How to <action relevant to {ctx.industry_vertical}>",
      "description": "<2 sentences>",
      "step": [
        {{"@type": "HowToStep", "name": "Step 1", "text": "..."}},
        {{"@type": "HowToStep", "name": "Step 2", "text": "..."}},
        {{"@type": "HowToStep", "name": "Step 3", "text": "..."}}
      ]
    }}
  ],
  "speakable": {{
    "@context": "https://schema.org",
    "@type": "WebPage",
    "speakable": {{
      "@type": "SpeakableSpecification",
      "cssSelector": ["h1", ".hero-lead", ".schema-speakable"]
    }}
  }}
}}

Rules:
- Fill in ALL placeholder values with real, specific content for this business
- howto_list must have 2-3 HowTo schemas relevant to the industry vertical
- For auto body: "How to file an insurance claim", "How to choose a body shop"
- For dental: "How to prepare for a root canal", "How to choose a dentist"
- For law firms: "How to find a personal injury attorney", "How to file a claim"
- breadcrumb item should use the actual domain and primary service slug
- service areaServed must list all cities provided
- Return ONLY valid JSON. No markdown fences, no extra text.
"""

        raw_extra = self.call_claude(additional_prompt, max_tokens=3000, model="claude-sonnet-4-6")

        schema_review   = {}
        schema_breadcrumb = {}
        schema_service  = {}
        howto_list      = []
        schema_speakable = {}

        if raw_extra:
            try:
                extra = json.loads(strip_json_fences(raw_extra, "{"))
                schema_review      = extra.get("review", {})
                schema_breadcrumb  = extra.get("breadcrumb", {})
                schema_service     = extra.get("service", {})
                howto_list         = extra.get("howto_list", [])
                schema_speakable   = extra.get("speakable", {})
            except Exception as e:
                self.log(f"Extra schemas parse error: {str(e)[:80]}", "warning")

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
