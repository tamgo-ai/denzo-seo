"""
Keyword Strategist — Layer 1
Generates a full keyword universe using Claude based on business context.
Saves results to the keywords table.
"""
import json
import re
from denzo.agents.base_agent import TenantAwareBaseAgent, ClientContext, db_execute, db_write, strip_json_fences

_NUM_PREFIX = re.compile(r"^\d+[\.\)]\s*")


def _recover_truncated_json_array(text: str):
    """
    When Claude truncates mid-JSON, find the last complete object in the array
    and close the array there. Returns a list or None if unrecoverable.
    """
    # Find last complete } in the text
    last_brace = text.rfind("}")
    if last_brace == -1:
        return None
    truncated = text[:last_brace + 1] + "]"
    try:
        return json.loads(truncated)
    except json.JSONDecodeError:
        # Walk backwards finding a valid close
        pos = last_brace - 1
        while pos > 0:
            prev = text.rfind("}", 0, pos)
            if prev == -1:
                break
            candidate = text[:prev + 1] + "]"
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pos = prev - 1
        return None


class KeywordStrategist(TenantAwareBaseAgent):

    def __init__(self, ctx: ClientContext):
        super().__init__("Keyword Strategist", ctx, layer=1, color="blue")

    def run(self):
        self.log("Starting keyword research...")
        self.set_status("working", "Analyzing business context")
        ctx = self.ctx

        existing = db_execute(
            "SELECT COUNT(*) FROM keywords WHERE tenant_id=?", (self.ctx.tenant_id,)
        )
        existing_count = existing[0][0] if existing else 0

        # Build prompt — cap cities/services to prevent runaway keyword explosion
        # 15 cities × 5 services × intent variants = 375+ keywords already. Cap hard.
        MAX_CITIES    = 15
        MAX_SERVICES  = 12
        service_cities = ctx.service_cities[:MAX_CITIES]
        services       = ctx.services[:MAX_SERVICES]
        if len(ctx.service_cities) > MAX_CITIES:
            self.log(
                f"Capped service cities to {MAX_CITIES} (client has {len(ctx.service_cities)}) "
                "to prevent keyword explosion. Edit client settings to adjust.",
                "warning"
            )
        if len(ctx.services) > MAX_SERVICES:
            self.log(
                f"Capped services to {MAX_SERVICES} (client has {len(ctx.services)}) "
                "for focused keyword research.", "warning"
            )

        cities_str   = ", ".join(service_cities) if service_cities else ctx.primary_city
        services_str = ", ".join(services) if services else "general services"
        certs_str    = ", ".join(ctx.certifications[:5]) if ctx.certifications else ""

        # Build industry-aware keyword categories
        industry = ctx.industry_vertical or "general"
        svc0 = services_str.split(',')[0].strip() if services_str else "services"

        if industry in ("auto_body_shop", "collision_repair"):
            intent_categories = """
3. Problem-aware: "my car was hit", "need estimate", "insurance claim help"
4. Comparison: "best body shop near me", "[service] reviews"
5. Question: "how much does [service] cost", "what to do after accident"
6. Emergency/urgent: "24 hour tow", "same day estimate", "near me"
7. Long-tail location combos: service + each city in [{cities_str}]"""
        elif industry in ("automotive_dealership",):
            intent_categories = """
3. Buyer-intent: "buy {svc0}", "{svc0} for sale", "{svc0} near me", "best price"
4. Comparison: "{svc0} vs [competitor brand]", "{svc0} review", "which {svc0} to buy"
5. Finance: "{svc0} lease deals", "{svc0} financing", "{svc0} monthly payment calculator"
6. CPO/Pre-owned: "certified pre-owned {svc0}", "used {svc0} [city]", "CPO warranty"
7. Long-tail location combos: service + each city in [{cities_str}]""".format(svc0=svc0, cities_str=cities_str)
        elif industry in ("saas_tech", "agency"):
            intent_categories = """
3. Problem-aware: "how to automate [service]", "best [service] software", "[service] pricing"
4. Comparison: "[service] vs [alternative]", "best [service] tool", "[service] reviews"
5. Question: "what is [service]", "how does [service] work", "[service] ROI"
6. Demo/trial: "[service] free trial", "[service] demo", "[service] case study"
7. Long-tail combos: service + industry + location in [{cities_str}]""".format(cities_str=cities_str)
        else:
            intent_categories = """
3. Problem-aware: "need [service]", "[service] near me", "best [service] provider"
4. Comparison: "best [service] near me", "[service] reviews", "[service] vs alternatives"
5. Question: "how much does [service] cost", "what is [service]", "[service] benefits"
6. Urgency: "same day [service]", "affordable [service]", "[service] deals"
7. Long-tail location combos: service + each city in [{cities_str}]""".format(cities_str=cities_str)

        prompt = f"""{ctx.to_prompt_block()}

You are an expert Local SEO keyword strategist. Generate a comprehensive keyword list for this specific business.

IMPORTANT: This business operates in the "{industry}" industry. Generate keywords relevant to THEIR actual business — NOT generic or wrong-industry keywords.

Return a JSON array of keyword objects. Each object:
{{
  "keyword": "full keyword phrase",
  "volume": "estimated monthly searches (number only, e.g. 1200)",
  "difficulty": "easy|medium|hard",
  "intent": "informational|navigational|commercial|transactional",
  "location": "city name or empty for generic",
  "category": "service|brand|location|comparison|question|conversion",
  "priority": "high|medium|low"
}}

Generate AT LEAST 50 keywords covering:
1. Core service keywords + each city: e.g. "{svc0} {ctx.primary_city}"
2. Brand/certification keywords: {certs_str or 'specialist, certified, expert, authorized'}
{intent_categories}

RULES:
- All keywords must be directly relevant to "{industry}" — never generate keywords for other industries
- Include local modifiers: near me, [city name], [region name]
- Mix head terms (1-2 words) and long-tail (4-6 words)
- Assign realistic monthly volume estimates based on keyword type and competition

Return ONLY the JSON array, no markdown.
"""
        self.set_status("working", "Generating keyword universe with AI")
        raw = self.call_claude(prompt, max_tokens=8000, model="claude-sonnet-4-6")

        if not raw:
            self.log("AI returned empty response", "error")
            self.set_status("error", "Empty API response")
            return

        # Parse JSON — with truncation recovery
        cleaned = strip_json_fences(raw, "[")
        try:
            keywords = json.loads(cleaned)
        except json.JSONDecodeError as e:
            self.log(f"JSON truncated, attempting recovery... ({e})", "warning")
            keywords = _recover_truncated_json_array(cleaned)
            if keywords is None:
                self.log(f"JSON recovery failed: {e}", "error")
                self.set_status("error", f"JSON parse error: {e}")
                return
            self.log(f"Recovered {len(keywords)} keywords from truncated response", "warning")

        saved = 0
        for kw in keywords:
            if self.should_stop():
                break
            keyword = _NUM_PREFIX.sub("", kw.get("keyword", "").strip()).strip()
            if not keyword:
                continue
            self.add_keyword(
                keyword=keyword,
                volume=str(kw.get("volume", "")),
                difficulty=kw.get("difficulty", "medium"),
                intent=kw.get("intent", "commercial"),
                location=kw.get("location", ""),
                category=kw.get("category", "service"),
                priority=kw.get("priority", "medium"),
            )
            saved += 1

        self.log(f"Saved {saved} keywords (had {existing_count} before).", "success")
        self.set_status("done", f"{saved} keywords generated")
