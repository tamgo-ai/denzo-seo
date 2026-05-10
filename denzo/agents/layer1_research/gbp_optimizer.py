"""
GBP Optimizer — Layer 1
Optimizes Google Business Profile — the #1 local ranking factor.
Works for ALL verticals: auto body, dental, law, HVAC, restaurant, etc.
"""
import json
from denzo.agents.base_agent import TenantAwareBaseAgent, ClientContext, db_execute, db_write, strip_json_fences


class GBPOptimizer(TenantAwareBaseAgent):

    def __init__(self, ctx: ClientContext):
        super().__init__("GBP Optimizer", ctx, layer=1, color="green")

    def run(self):
        self.log("Starting Google Business Profile optimization...")
        self.set_status("working", "Loading business context")
        ctx = self.ctx

        # Load google_api_key from tenant settings
        google_api_key = None
        api_key_row = db_execute(
            "SELECT value FROM settings WHERE tenant_id=? AND key='google_api_key'",
            (self.ctx.tenant_id,)
        )
        if api_key_row:
            google_api_key = api_key_row[0]["value"]
            self.log("Google API key found in settings.", "info")
        else:
            self.log("No google_api_key in settings — running in AI analysis mode.", "info")

        # Build comprehensive GBP optimization prompt using full client context
        services_str = ", ".join(ctx.services[:15]) if ctx.services else "General services"
        certs_str = ", ".join(ctx.certifications[:10]) if ctx.certifications else "None specified"
        industry = ctx.industry_vertical or "general"

        prompt = f"""{ctx.to_prompt_block()}

You are a Local SEO Expert specializing in Google Business Profile (GBP) optimization.
Your task: generate a COMPLETE GBP optimization plan for this {industry} business.

GBP is the #1 local ranking factor. Every section must be optimized for:
1. Keyword relevance to primary services
2. Local relevance to the service area
3. E-E-A-T signals (Experience, Expertise, Authoritativeness, Trustworthiness)
4. Conversion optimization (calls, directions, website clicks)

INDUSTRY: {industry}
SERVICES: {services_str}
CERTIFICATIONS: {certs_str}

Generate a comprehensive GBP optimization plan. Return a JSON object with this EXACT structure:
{{
  "business_category_primary": "The single most accurate Google Business Category for this business",
  "business_categories_secondary": ["Category 2", "Category 3", "Category 4"],
  "business_description": "A 155-character optimized GBP description that includes the primary keyword and service area",
  "attributes_to_enable": ["Attribute 1 (e.g. Women-led)", "Attribute 2 (e.g. Wheelchair accessible)", "..."],
  "services_to_add": [
    {{"name": "Service Name", "description": "2-3 sentence service description with keywords"}},
    {{"name": "Service Name 2", "description": "..."}}
  ],
  "gbp_posts": [
    {{
      "type": "OFFER|WHAT_NEW|EVENT",
      "title": "Post title (max 58 chars)",
      "content": "Post body text (150-300 words, include primary keyword and CTA)",
      "cta": "Call now|Book|Learn more|Order online|Get offer|Sign up|Visit"
    }}
  ],
  "qa_seeds": [
    {{"question": "Common question customers ask", "answer": "Thorough, keyword-rich answer (2-4 sentences)"}},
    {{"question": "Question 2", "answer": "Answer 2"}}
  ],
  "photo_strategy": [
    "Photo action item 1 (e.g. Upload 5 before/after photos showing repair quality)",
    "Photo action item 2"
  ],
  "missing_info_checklist": [
    "Action item 1 (e.g. Add appointment booking URL)",
    "Action item 2"
  ],
  "optimization_score_estimate": 65,
  "priority_actions": [
    "Top priority action 1",
    "Top priority action 2",
    "Top priority action 3"
  ]
}}

RULES:
- business_description MUST be exactly 150-160 characters
- Generate AT LEAST 3 gbp_posts (mix OFFER + WHAT_NEW types)
- Generate AT LEAST 5 qa_seeds covering the most common customer questions
- Generate AT LEAST 5 services_to_add with keyword-rich descriptions
- optimization_score_estimate: estimate 0-100 based on how complete this GBP likely is for a typical {industry} business
- All content must be specific to this business — no generic filler
- Include relevant certifications and specializations prominently

Return ONLY the JSON object, no markdown, no explanation.
"""

        self.set_status("working", "Generating GBP optimization plan with AI")
        raw = self.call_claude(prompt, max_tokens=5000, model="claude-sonnet-4-6")

        if not raw:
            self.log("AI returned empty response", "error")
            self.set_status("error", "Empty API response")
            return

        cleaned = strip_json_fences(raw, start_char="{")
        try:
            gbp_plan = json.loads(cleaned)
        except json.JSONDecodeError as e:
            self.log(f"JSON parse failed — preview: {cleaned[:200]}", "error")
            self.set_status("error", f"Parse error: {e}")
            return

        # Log key sections
        self.log(
            f"Primary category: {gbp_plan.get('business_category_primary', 'N/A')}",
            "info"
        )
        secondary = gbp_plan.get("business_categories_secondary", [])
        self.log(f"Secondary categories: {len(secondary)} — {', '.join(secondary[:3])}", "info")

        posts = gbp_plan.get("gbp_posts", [])
        qa_seeds = gbp_plan.get("qa_seeds", [])
        services = gbp_plan.get("services_to_add", [])
        score = gbp_plan.get("optimization_score_estimate", 0)
        photo_strategy = gbp_plan.get("photo_strategy", [])
        priority_actions = gbp_plan.get("priority_actions", [])

        self.log(f"GBP posts generated: {len(posts)}", "info")
        self.log(f"Q&A seeds generated: {len(qa_seeds)}", "info")
        self.log(f"Services to add: {len(services)}", "info")
        self.log(f"Photo strategy: {len(photo_strategy)} actions", "info")

        for action in priority_actions[:3]:
            self.log(f"Priority action: {action}", "info")

        # Save full GBP optimization plan to settings
        db_write(
            "INSERT OR REPLACE INTO settings (tenant_id, key, value, updated_at) "
            "VALUES (?,?,?,CURRENT_TIMESTAMP)",
            (self.ctx.tenant_id, "gbp_optimization_plan", json.dumps(gbp_plan))
        )
        self.log("GBP optimization plan saved to settings.", "success")

        # Save GBP posts queue separately for easy access by publishers
        if posts:
            db_write(
                "INSERT OR REPLACE INTO settings (tenant_id, key, value, updated_at) "
                "VALUES (?,?,?,CURRENT_TIMESTAMP)",
                (self.ctx.tenant_id, "gbp_posts_queue", json.dumps(posts))
            )
            self.log(f"GBP posts queue saved: {len(posts)} posts ready.", "success")

        # Try GBP Insights API if key is available
        if google_api_key:
            self.log("Google API key present — attempting GBP Insights API call...", "info")
            try:
                import urllib.request
                import urllib.error

                api_url = (
                    f"https://mybusiness.googleapis.com/v4/accounts/-/locations"
                    f"?key={google_api_key}"
                )
                req = urllib.request.Request(
                    api_url,
                    headers={"User-Agent": "DenzoSEO/1.0"}
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode())
                    location_count = len(data.get("locations", []))
                    self.log(
                        f"GBP API connected — {location_count} locations found.",
                        "success"
                    )
            except Exception as e:
                error_str = str(e)
                if "401" in error_str or "403" in error_str or "OAuth" in error_str.lower():
                    self.log(
                        "GBP API needs OAuth setup — using AI analysis mode. "
                        "To enable real GBP data, complete OAuth flow in Google Cloud Console.",
                        "info"
                    )
                else:
                    self.log(
                        f"GBP API unavailable ({error_str[:80]}) — using AI analysis mode.",
                        "info"
                    )
        else:
            self.log(
                "No google_api_key configured — running in AI analysis mode. "
                "Add google_api_key in Platform Settings to enable live GBP data.",
                "info"
            )

        self.log(
            f"GBP optimization complete. Score estimate: {score}/100. "
            f"{len(posts)} posts queued. {len(qa_seeds)} Q&A seeds ready.",
            "success"
        )
        self.set_status(
            "done",
            f"GBP plan ready — score {score}/100 — {len(posts)} posts queued"
        )
