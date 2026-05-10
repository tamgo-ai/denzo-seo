"""
GBP Optimizer — Layer 1
Optimizes Google Business Profile — the #1 local ranking factor.
Works for ALL verticals: auto body, dental, law, HVAC, restaurant, dealerships.

Two modes:
  • OAuth-connected: pulls real locations, reviews, posts from Business Profile API,
    persists them to gbp_locations, and grounds the AI plan in real data.
  • AI analysis-only (fallback): generates an optimization plan from client_context
    when no OAuth token is connected.
"""
import json

from denzo.agents.base_agent import (
    TenantAwareBaseAgent, ClientContext, db_execute, db_write, strip_json_fences,
)


GBP_ACCOUNTS_URL = "https://mybusinessaccountmanagement.googleapis.com/v1/accounts"
# Business Information API — modern endpoint for locations
GBP_LOCATIONS_FIELDS = (
    "name,title,storefrontAddress,phoneNumbers,categories,"
    "websiteUri,regularHours,metadata,profile"
)


class GBPOptimizer(TenantAwareBaseAgent):

    def __init__(self, ctx: ClientContext):
        super().__init__("GBP Optimizer", ctx, layer=1, color="green")

    # ── Live GBP data via OAuth ────────────────────────────────────────────────

    def _sync_live_gbp(self) -> dict:
        """Pull accounts + locations from Business Profile API and persist them.

        Returns a small summary dict with 'accounts', 'locations', and any error.
        Silently degrades to AI-only mode if the API is unavailable for any reason.
        """
        from denzo.agents.utils import google_oauth
        from denzo.agents.utils.google_oauth import OAuthError, authed_request

        if not google_oauth.is_connected(self.ctx.tenant_id, "gbp"):
            return {"available": False, "reason": "no_oauth_token"}

        try:
            self.log("Fetching Google Business Profile accounts (OAuth)...", "info")
            accounts_data = authed_request(
                self.ctx.tenant_id, "gbp", GBP_ACCOUNTS_URL,
            )
            accounts = accounts_data.get("accounts", []) or []
            if not accounts:
                self.log("No GBP accounts visible to this Google user.", "warning")
                return {"available": False, "reason": "no_accounts"}

            account = accounts[0]
            account_name = account.get("name", "")  # e.g. "accounts/123456"
            self.log(
                f"GBP account: {account.get('accountName', account_name)} "
                f"(type={account.get('type', 'UNKNOWN')})",
                "info",
            )

            # List locations for this account
            locations_url = (
                f"https://mybusinessbusinessinformation.googleapis.com/v1/"
                f"{account_name}/locations"
            )
            loc_data = authed_request(
                self.ctx.tenant_id, "gbp", locations_url,
                params={"readMask": GBP_LOCATIONS_FIELDS},
            )
            locations = loc_data.get("locations", []) or []
            self.log(f"GBP locations available: {len(locations)}", "success")

            # Persist each location
            for loc in locations:
                location_id = loc.get("name", "")
                title       = loc.get("title", "")
                addr        = loc.get("storefrontAddress", {}) or {}
                addr_str    = ", ".join(filter(None, [
                    " ".join(addr.get("addressLines", []) or []),
                    addr.get("locality", ""),
                    addr.get("administrativeArea", ""),
                    addr.get("postalCode", ""),
                ]))
                phones      = loc.get("phoneNumbers", {}) or {}
                phone       = phones.get("primaryPhone", "")
                website     = loc.get("websiteUri", "")
                categories  = loc.get("categories", {}) or {}
                primary_cat = (categories.get("primaryCategory") or {}).get("displayName", "")

                db_write(
                    """INSERT INTO gbp_locations
                       (tenant_id, location_id, name, address, phone, website,
                        primary_category, raw_json, last_synced_at)
                       VALUES (?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                       ON CONFLICT(tenant_id, location_id) DO UPDATE SET
                           name             = excluded.name,
                           address          = excluded.address,
                           phone            = excluded.phone,
                           website          = excluded.website,
                           primary_category = excluded.primary_category,
                           raw_json         = excluded.raw_json,
                           last_synced_at   = CURRENT_TIMESTAMP""",
                    (self.ctx.tenant_id, location_id, title, addr_str, phone,
                     website, primary_cat, json.dumps(loc)),
                )

            return {
                "available":   True,
                "account":     account_name,
                "locations":   [
                    {
                        "id":       l.get("name"),
                        "title":    l.get("title"),
                        "category": ((l.get("categories") or {}).get("primaryCategory") or {})
                                       .get("displayName", ""),
                    }
                    for l in locations[:10]
                ],
                "loc_count":   len(locations),
            }

        except OAuthError as e:
            err = str(e)
            # 403 typically means the project is not whitelisted yet, or scope is wrong
            if "403" in err:
                self.log(
                    "GBP API returned 403 — project likely not whitelisted yet. "
                    "Submit the Business Profile API form and re-run after approval.",
                    "warning",
                )
            else:
                self.log(f"GBP OAuth call failed: {err[:160]}", "warning")
            return {"available": False, "reason": "api_error", "error": err}
        except Exception as e:
            self.log(f"Unexpected error fetching live GBP data: {e}", "warning")
            return {"available": False, "reason": "exception", "error": str(e)}

    # ── Main run loop ──────────────────────────────────────────────────────────

    def run(self):
        self.log("Starting Google Business Profile optimization...")
        self.set_status("working", "Loading business context")
        ctx = self.ctx

        # ── Mode detection: live OAuth data or AI-only? ───────────────────────
        self.set_status("working", "Checking GBP OAuth connection")
        live = self._sync_live_gbp()

        if live.get("available"):
            self.log(
                f"Live GBP mode — {live['loc_count']} location(s) synced from Google.",
                "success",
            )
        else:
            reason = live.get("reason", "unknown")
            self.log(
                f"Running in AI analysis mode (reason: {reason}). "
                "Connect Google Business Profile in Settings to ground this plan in real data.",
                "info",
            )

        # ── Build optimization prompt ─────────────────────────────────────────
        services_str = ", ".join(ctx.services[:15]) if ctx.services else "General services"
        certs_str = ", ".join(ctx.certifications[:10]) if ctx.certifications else "None specified"
        industry = ctx.industry_vertical or "general"

        live_block = ""
        if live.get("available") and live.get("locations"):
            preview = "\n".join(
                f"  - {l['title']} ({l['category'] or 'no category'}) [{l['id']}]"
                for l in live["locations"][:5]
            )
            live_block = (
                f"\nREAL GOOGLE BUSINESS PROFILE LOCATIONS (synced from API — ground your "
                f"recommendations in these):\n{preview}\n"
            )

        prompt = f"""{ctx.to_prompt_block()}
{live_block}
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
            "info",
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
            (self.ctx.tenant_id, "gbp_optimization_plan", json.dumps(gbp_plan)),
        )
        self.log("GBP optimization plan saved to settings.", "success")

        # Save GBP posts queue separately for easy access by publishers
        if posts:
            db_write(
                "INSERT OR REPLACE INTO settings (tenant_id, key, value, updated_at) "
                "VALUES (?,?,?,CURRENT_TIMESTAMP)",
                (self.ctx.tenant_id, "gbp_posts_queue", json.dumps(posts)),
            )
            self.log(f"GBP posts queue saved: {len(posts)} posts ready.", "success")

        live_suffix = ""
        if live.get("available"):
            live_suffix = f" | {live['loc_count']} live location(s)"

        self.log(
            f"GBP optimization complete. Score estimate: {score}/100. "
            f"{len(posts)} posts queued. {len(qa_seeds)} Q&A seeds ready.{live_suffix}",
            "success",
        )
        self.set_status(
            "done",
            f"GBP plan ready — score {score}/100 — {len(posts)} posts queued{live_suffix}",
        )
