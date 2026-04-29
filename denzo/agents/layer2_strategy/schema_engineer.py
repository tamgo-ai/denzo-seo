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

        self.log(f"Schema complete: LocalBusiness ({schema_type}) + {len(faqs)} FAQ entries saved.", "success")
        self.set_status("done", f"Schema generated: {schema_type} + FAQ ({len(faqs)} Q&As)")
