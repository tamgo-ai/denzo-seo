"""
Competitor Intel — Layer 1  (Enhanced v3)
==========================================
For brand-certified businesses (e.g. BMW-certified collision repair shops),
the real competitors are OTHER shops with the SAME brand certification in
nearby cities — not generic shops in the same city.

CRITICAL: Industry filtering — a BMW-certified collision repair shop competes
with OTHER collision repair shops, NOT with BMW car dealerships. The agent
detects business type and filters accordingly.

Key enhancements:
  A. Geo-Radius Competitor Discovery  — finds certified shops in a 35-mile radius
  B. Brand-Specific Competitor Scoring — tier 1 = same brand cert, tier 2 = generic
  C. Industry Filtering               — dealerships ≠ body shops, never mix industries
  D. Competitive Gap Analysis         — keywords & cities they target that we don't
  E. Keyword Cannibalization Detection — flags our own pages competing with each other
"""
import json
import re
import requests
from bs4 import BeautifulSoup
from denzo.agents.base_agent import (
    TenantAwareBaseAgent, ClientContext,
    db_write, db_execute, strip_json_fences
)

# ── Industry classification helpers ──────────────────────────────────────────

# Name fragments that signal a CAR DEALERSHIP (not a body/collision shop)
_DEALERSHIP_NAME_SIGNALS = [
    " of ", "motors", "auto group", "car group", "dealership",
    "BMW of ", "Audi of ", "Mercedes-Benz of ", "Honda of ",
    "Toyota of ", "Lexus of ", "Porsche of ", "Ford of ",
    "Chevrolet of ", "Hyundai of ", "Kia of ", "Nissan of ",
    "Volkswagen of ", "Volvo of ", "Infiniti of ", "Acura of ",
    "Cadillac of ", "Buick of ", "GMC of ", "Jeep of ",
]

# URL fragments that signal a car dealership domain
_DEALERSHIP_URL_SIGNALS = [
    "bmwof", "audiof", "mercedesof", "toyotaof", "hondaof",
    "lexusof", "drivebmw", "bmwdealer", "autogroup",
]

# Certifications that belong to CAR DEALERSHIPS (sales certs), not body shops
_DEALER_ONLY_CERTS = [
    "certified pre-owned", "cpo", "authorized dealer",
    "factory dealer", "franchise dealer",
]

# Certifications that are specific to COLLISION REPAIR shops
_COLLISION_REPAIR_CERTS = [
    "certified collision", "collision repair", "ccrc",
    "certified body", "body shop", "frame straightening",
    "i-car", "icar", "structural repair", "refinishing",
    "paintless dent", "pdr certified",
]

# Industry verticals that are collision repair (from ClientContext.industry_vertical)
_COLLISION_REPAIR_VERTICALS = {
    "collision_repair", "auto_body", "body_shop",
    "auto collision", "collision", "bodywork",
}


def _is_dealership_by_name(name: str) -> bool:
    """Return True if the competitor name looks like a car dealership, not a body shop."""
    name_lower = name.lower()
    for signal in _DEALERSHIP_NAME_SIGNALS:
        if signal.lower() in name_lower:
            return True
    return False


def _is_dealership_by_url(url: str) -> bool:
    """Return True if the URL looks like a car dealership site."""
    url_lower = (url or "").lower()
    for signal in _DEALERSHIP_URL_SIGNALS:
        if signal in url_lower:
            return True
    return False


def _has_only_dealer_certs(certified_brands_raw: str) -> bool:
    """Return True if the competitor only lists dealership-type certifications."""
    text = certified_brands_raw.lower() if certified_brands_raw else ""
    if not text or text == "[]":
        return False
    has_dealer_cert = any(c in text for c in _DEALER_ONLY_CERTS)
    has_collision_cert = any(c in text for c in _COLLISION_REPAIR_CERTS)
    return has_dealer_cert and not has_collision_cert


def _classify_scraped_content(scraped: dict) -> str:
    """
    Given scraped page content, detect if this is a car dealership vs a body shop.
    Returns 'dealership', 'collision_repair', or 'unknown'.
    This is only used as a secondary signal — primary filtering is by name/URL.
    """
    text = (scraped.get("all_text") or "").lower()
    if not text:
        return "unknown"

    dealership_signals = [
        "new vehicles", "certified pre-owned", "vehicle inventory",
        "test drive", "financing options", "new and used cars", "auto loan",
        "msrp", "trade-in value", "car dealership", "lease deals", "sticker price",
        "new car", "used car", "vehicle sales",
    ]
    collision_signals = [
        "collision repair", "body shop", "auto body", "frame repair",
        "paintless dent", "vehicle restoration", "insurance claim",
        "accident repair", "dent repair", "paint and body",
        "bumper repair", "quarter panel", "structural repair",
    ]

    dealer_hits = sum(1 for s in dealership_signals if s in text)
    collision_hits = sum(1 for s in collision_signals if s in text)

    if collision_hits > dealer_hits:
        return "collision_repair"
    elif dealer_hits > collision_hits:
        return "dealership"
    else:
        return "unknown"

from denzo.data.nearby_cities import NEARBY_CITIES as _NEARBY_CITIES


def _nearby_cities_for(city: str) -> list[str]:
    """Return nearby cities for a given city (case-insensitive)."""
    return _NEARBY_CITIES.get(city.lower().strip(), [])


class CompetitorIntel(TenantAwareBaseAgent):

    def __init__(self, ctx: ClientContext):
        super().__init__("Competitor Intel", ctx, layer=1, color="purple")

    # ── Private helpers ────────────────────────────────────────────────────────

    def _scrape(self, url: str) -> dict:
        """Fetch and parse a competitor website — 3-pass Cloudflare bypass."""
        from denzo.agents.utils.stealth_fetch import fetch_and_parse
        result = fetch_and_parse(url, timeout=25, log_fn=lambda m: self.log(m, "info"))

        if not result.get("ok"):
            return {"url": url, "ok": False, "error": result.get("error", "blocked"),
                    "title": "", "h1": [], "h2": [], "paragraphs": [], "all_text": ""}

        return {
            "url": url,
            "title": result.get("title", ""),
            "h1": [result.get("h1", "")],
            "h2": result.get("h2s", []),
            "paragraphs": [],
            "all_text": result.get("all_text", ""),
            "ok": True,
        }

    def _ai_analyze_competitor(self, name: str, url: str, city: str) -> dict:
        """
        When a competitor site is blocked (Cloudflare, etc.), use Claude to generate
        competitive intelligence based on known information about the competitor.
        """
        prompt = f"""{self.ctx.to_prompt_block()}

The competitor website {url} is protected and cannot be scraped.
Based on your knowledge of "{name}" located in {city}, generate realistic competitive intelligence.

Return ONLY this JSON, no markdown:
{{
  "title": "Likely page title / main positioning (10-15 words)",
  "h1": ["Main heading they probably use", "Secondary heading"],
  "h2": ["Service/section heading 1", "Service/section heading 2", "Service/section heading 3", "Service/section heading 4"],
  "paragraphs": ["Key value proposition they likely promote", "Secondary message"],
  "strengths": "2-3 sentence analysis of this competitor's likely strengths vs {self.ctx.client_name}",
  "weaknesses": "2-3 sentence analysis of likely weaknesses or content gaps we could exploit",
  "keyword_themes": ["theme 1", "theme 2", "theme 3", "theme 4", "theme 5"]
}}"""
        raw = self.call_claude(prompt, max_tokens=600, model="claude-haiku-4-5-20251001")
        if not raw:
            return {}
        try:
            return json.loads(strip_json_fences(raw))
        except Exception:
            return {}

    # ── Brand tier validation ──────────────────────────────────────────────────

    # Known brand tier mappings — use to validate/override Claude's suggestions
    _BUDGET_BRANDS = {
        "maaco", "midas", "jiffy lube", "meineke", "pep boys", "monro", "sears auto",
        "mcdonalds", "wendy's", "burger king", "taco bell", "dollar general", "dollar tree",
        "spirit airlines", "frontier airlines",
    }
    _MID_BRANDS = {
        "caliber collision", "service king", "fix auto", "gerber collision",
        "toyota", "honda", "nissan", "ford", "chevrolet", "hyundai", "kia", "jeep",
        "subway", "olive garden", "applebee's", "chili's", "denny's",
    }
    _PREMIUM_BRANDS = {
        "bmw certified", "mercedes certified", "audi certified", "lexus certified",
        "porsche certified", "cadillac certified", "infinity certified", "acura certified",
        "bmw", "mercedes", "audi", "lexus", "genesis certified",
    }
    _LUXURY_BRANDS = {
        "rolls royce", "bentley", "lamborghini", "ferrari", "mclaren",
        "porsche", "maserati", "aston martin",
    }

    _TIER_ORDER = {"budget": 0, "mid": 1, "premium": 2, "luxury": 3}

    def _infer_competitor_tier(self, name: str, url: str = "") -> str | None:
        """
        Infer brand tier of a competitor from name/URL.
        Returns 'budget'|'mid'|'premium'|'luxury' or None if cannot determine.
        """
        text = (name + " " + (url or "")).lower()
        for brand in self._LUXURY_BRANDS:
            if brand in text:
                return "luxury"
        for brand in self._PREMIUM_BRANDS:
            if brand in text:
                return "premium"
        for brand in self._MID_BRANDS:
            if brand in text:
                return "mid"
        for brand in self._BUDGET_BRANDS:
            if brand in text:
                return "budget"
        return None

    def _is_tier_mismatch(self, comp_name: str, comp_url: str = "") -> bool:
        """
        Return True if this competitor appears to be a different brand tier than our client.
        Only returns True when we're CONFIDENT (known brand lists).
        """
        client_tier = getattr(self.ctx, "brand_tier", "mid") or "mid"
        inferred = self._infer_competitor_tier(comp_name, comp_url)
        if inferred is None:
            return False  # Unknown tier — give benefit of the doubt
        client_level  = self._TIER_ORDER.get(client_tier, 1)
        comp_level    = self._TIER_ORDER.get(inferred, 1)
        # Allow 1 tier difference max (e.g. mid can compete with premium)
        return abs(client_level - comp_level) > 1

    def _search_certified_competitors(self, brand: str, city: str) -> list[dict]:
        """
        Find competitors with the same brand/certification in the same industry as the client.
        Works for ANY industry: collision repair, restaurants, universities, law firms, etc.
        Returns a list of {name, url, city, business_type, likely_certified, source} dicts.
        """
        industry   = self.ctx.industry_vertical or "general"
        brand_tier = getattr(self.ctx, "brand_tier", "mid")
        services_sample = ", ".join(self.ctx.services[:5]) if self.ctx.services else "similar services"

        # Brand-tier comparison guidance
        _tier_guidance = {
            "budget":  "budget/value brands — affordable options, high volume, price-competitive",
            "mid":     "mid-range brands — balanced quality and price",
            "premium": "premium brands — higher quality, professional-grade service",
            "luxury":  "luxury/ultra-premium brands — exclusive, high-end clientele",
        }
        tier_desc = _tier_guidance.get(brand_tier, "mid-range brands")

        prompt = f"""{self.ctx.to_prompt_block()}

You are a senior local SEO research analyst.

CLIENT BRAND TIER: {brand_tier} ({tier_desc}).
Only recommend competitors at the SAME brand tier level.
- 'budget' tier clients → compare with other budget/value competitors
- 'mid' tier clients → compare with other mid-range competitors
- 'premium' tier clients → compare with other premium competitors
- 'luxury' tier clients → compare ONLY with other luxury/premium competitors
Do NOT include competitors from a different price/brand tier segment.

TASK: Find LOCAL COMPETITORS of "{self.ctx.client_name}" that:
1. Operate in the SAME INDUSTRY: {industry}
2. Offer similar services: {services_sample}
3. Have {brand} certification/affiliation (if applicable to this industry)
4. Are located within a 35-mile radius of {city}, {self.ctx.state}
5. Are NOT our client
6. Are at the same brand tier ({brand_tier}) as our client

CRITICAL: Only include businesses in the SAME category as our client.
Industry = "{industry}" means:
- If "collision_repair" or "auto_body": find OTHER body shops/collision centers, NOT car dealerships
- If "restaurant": find OTHER restaurants in the same cuisine/price range, NOT caterers
- If "university" or "education": find OTHER schools/universities, NOT tutoring centers
- If "law_firm": find OTHER law firms with same practice area, NOT legal document services
- Always match the EXACT same industry vertical

Return a JSON array:
[
  {{
    "name": "Competitor Name",
    "url": "https://...",
    "city": "City, {self.ctx.state}",
    "business_type": "{industry}",
    "likely_certified": true,
    "source": "known|likely"
  }}
]

Return ONLY valid JSON array, no markdown. If uncertain about a business type, omit it.
"""
        raw = self.call_claude(prompt, max_tokens=1500, model="claude-sonnet-4-6")
        if not raw:
            return []
        try:
            raw = strip_json_fences(raw, start_char="[")
            data = json.loads(raw)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _search_dealership_competitors(self, primary_city: str) -> list[dict]:
        """
        Dealership-specific competitor search.
        Tier 1 = same brand/make in nearby cities (strongest threat — same product, nearby market).
        Tier 2 = other automotive brands in same/nearby cities (competing for same buyer).
        Never returns collision repair shops or non-dealer businesses.
        """
        our_brands = self.ctx.certifications or []
        brand_names = ", ".join(our_brands[:5]) or "automotive"

        prompt = f"""{self.ctx.to_prompt_block()}

You are finding competitors for an AUTOMOTIVE DEALERSHIP.

Dealership competition has two tiers:

TIER 1 — Same Brand, Different Territory (strongest threat):
Customers shop their preferred brand across a wide radius. Find OTHER dealerships selling
the SAME brand(s) [{brand_names}] within ~35 miles of {primary_city}, {self.ctx.state}.
Example: BMW of Murrieta competes with BMW of Temecula, BMW of Riverside, BMW of San Diego.

TIER 2 — Other Brands, Same Territory (secondary threat):
Buyers comparison-shop between brands. Find dealers of OTHER automotive brands (Toyota, Honda,
Ford, Mercedes, etc.) in {primary_city} and immediately nearby cities.

Return a JSON array:
[
  {{
    "name": "Dealer Name",
    "url": "https://...",
    "city": "City, {self.ctx.state}",
    "business_type": "automotive_dealership",
    "brand_make": "BMW",
    "competition_tier": 1,
    "source": "known"
  }}
]

NEVER include: collision repair shops, body shops, auto parts stores, mechanic shops, or insurance companies.
Return ONLY valid JSON array."""

        raw = self.call_claude(prompt, max_tokens=2000, model="claude-sonnet-4-6")
        if not raw:
            return []
        try:
            data = json.loads(strip_json_fences(raw, start_char="["))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _verify_keyword_industry_relevance(self, keywords: list, industry: str) -> tuple:
        """
        Before saving gap keywords, verify that the typical SERP for each keyword
        would be populated by businesses in the SAME industry as our client.
        Catches cross-industry contamination (e.g. collision center keywords
        leaking into dealership clients because both appear in auto-related SERPs).

        Returns (relevant: list, filtered_out: list).
        """
        if not keywords:
            return [], []

        kw_list = "\n".join(f"- {kw}" for kw in keywords[:40])

        prompt = f"""You are a search-intent analyst for LOCAL SEO.

Our client industry: "{industry}"

For each keyword below, determine: if someone searches this on Google locally, would the
TOP organic results primarily come from "{industry}" businesses?

Mark RELEVANT if: top rankers are typically from the "{industry}" category.
Mark FILTERED if: top rankers are typically from a DIFFERENT industry category.
  - Directories (Yelp, Angi, Thumbtack) dominating = still RELEVANT (they list our industry)
  - Dealerships ranking for body-shop keywords = FILTERED
  - Body shops ranking for dealership keywords = FILTERED
  - Insurance companies ranking for repair keywords = FILTERED
  - Manufacturers ranking for local service keywords = FILTERED

Keywords to classify:
{kw_list}

Return ONLY valid JSON:
{{
  "relevant": ["keyword 1", "keyword 2"],
  "filtered": ["keyword 3"],
  "filter_reasons": {{"keyword 3": "why it was filtered"}}
}}"""

        raw = self.call_claude(prompt, max_tokens=1500, model="claude-haiku-4-5-20251001")
        if not raw:
            return keywords, []
        try:
            data = json.loads(strip_json_fences(raw))
            relevant = data.get("relevant", [])
            filtered = data.get("filtered", [])
            for kw, reason in data.get("filter_reasons", {}).items():
                self.log(f"[SERP Industry Filter] Dropped '{kw}': {reason}", "warning")
            return relevant, filtered
        except Exception:
            return keywords, []

    def _is_different_industry(self, competitor_data: dict) -> bool:
        """
        Return True if this competitor appears to be a DIFFERENT industry than our client.
        Works for any industry vertical.
        """
        our_vertical = (self.ctx.industry_vertical or "").lower()
        name = competitor_data.get("name", "").lower()
        url  = competitor_data.get("url", "").lower()
        detected = competitor_data.get("detected_industry", "")

        # Collision/body shop clients: dealerships are NOT competitors
        is_our_collision = any(v in our_vertical for v in ["collision", "body", "repair", "auto_body"])
        if is_our_collision:
            if _is_dealership_by_name(competitor_data.get("name", "")) or \
               _is_dealership_by_url(url) or detected == "dealership":
                return True

        # Automotive dealership clients: collision repair shops are NOT competitors
        is_our_dealership = any(v in our_vertical for v in ["dealership", "dealer", "automotive_dealer"])
        if is_our_dealership:
            collision_signals = ["collision", "body shop", "bodyshop", "autobody",
                                 "caliber", "service king", "fix auto", "gerber collision",
                                 "paintless", "dent repair", "frame straighten"]
            if any(sig in name for sig in collision_signals) or \
               any(sig in url for sig in ["calibercollision", "serviceking", "fixauto", "gerbercollision"]):
                return True

        # SaaS/tech clients: brick-and-mortar businesses are NOT competitors
        # (No hard filter — too complex to detect reliably)

        return False

    def _compute_competitor_score(self, competitor_data: dict,
                                   our_brands: list[str],
                                   our_cities: list[str]) -> tuple[int, float]:
        """
        Returns (tier, score).
        tier 0 = different industry (dealership vs body shop) — should be excluded
        tier 1 = same brand collision cert AND in nearby city — direct threat
        tier 2 = different brand or farther city — indirect competitor
        Score = brand_match * 2 + proximity_score + authority_score
        """
        # Industry gate — different industry = tier 0 (filtered out in display)
        if self._is_different_industry(competitor_data):
            return 0, 0.0

        # Normalize certified_brands: only keep collision-relevant certs
        raw_brands = competitor_data.get("certified_brands", [])
        if isinstance(raw_brands, str):
            try:
                raw_brands = json.loads(raw_brands)
            except Exception:
                raw_brands = [raw_brands] if raw_brands else []

        # Strip out dealer-only cert strings (CPO, authorized dealer)
        collision_brands = []
        for b in raw_brands:
            b_lower = b.lower()
            is_dealer_cert = any(dc in b_lower for dc in _DEALER_ONLY_CERTS)
            if not is_dealer_cert:
                collision_brands.append(b_lower)

        comp_city = (competitor_data.get("city") or
                     competitor_data.get("location") or "").lower().strip()
        # Strip ", CA" or ", California" suffixes for matching
        comp_city = re.sub(r",?\s*(ca|california)$", "", comp_city).strip()

        our_brands_lower = [b.lower() for b in our_brands]

        # Brand match: only count if the brand appears in their collision certs
        brand_matches = sum(1 for b in our_brands_lower
                            if any(b in cb for cb in collision_brands))
        brand_score   = min(brand_matches * 2.0, 4.0)

        # Proximity score: primary city = 3, nearby = 2, service city = 1.5, state = 1
        our_primary = self.ctx.primary_city.lower().strip()
        our_nearby  = [c.lower() for c in _nearby_cities_for(our_primary)]

        if comp_city == our_primary:
            proximity_score = 3.0
        elif comp_city in our_nearby or any(comp_city in c or c in comp_city
                                             for c in [our_primary] + our_nearby):
            proximity_score = 2.0
        elif any(comp_city in c.lower() or c.lower() in comp_city
                 for c in our_cities):
            proximity_score = 1.5
        else:
            proximity_score = 1.0

        # Authority score: 1 if has a real URL
        authority_score = 1.0 if competitor_data.get("url") else 0.0

        score = brand_score + proximity_score + authority_score

        # Tier 1: same brand collision cert + in nearby radius
        if brand_matches >= 1 and proximity_score >= 2.0:
            tier = 1
        else:
            tier = 2

        return tier, round(score, 2)

    def _detect_cannibalization(self) -> list[dict]:
        """
        Detect internal keyword cannibalization between our own pages.
        Two pages cannibalize when they target the same keyword (same brand + similar city).

        Risk levels:
        - HIGH: same brand, same city — always flagged regardless of business type
        - MEDIUM: same brand, nearby cities — only flagged for multi-location businesses
          (single-location businesses intentionally target multiple cities via programmatic SEO;
           flagging those as risks would produce hundreds of false positives)
        """
        # Check if this is a multi-location business
        client_row = db_execute(
            "SELECT is_multilocation FROM clients WHERE tenant_id=?",
            (self.ctx.tenant_id,)
        )
        is_multilocation = bool(client_row[0]["is_multilocation"]) if client_row else False

        pages = db_execute(
            "SELECT id, title, slug, type, location, target_keyword "
            "FROM pages WHERE tenant_id=? AND status != 'archived'",
            (self.ctx.tenant_id,)
        )
        if not pages:
            return []

        page_list = [dict(p) for p in pages]

        # Group by brand keyword
        brand_groups: dict[str, list[dict]] = {}
        for p in page_list:
            kw = (p.get("target_keyword") or p.get("title") or "").lower().strip()
            # Extract brand from keyword — whole-word match only to avoid
            # false positives like "ram" matching inside "frame"
            for brand in self.ctx.certifications:
                brand_lower = brand.lower()
                if re.search(r'\b' + re.escape(brand_lower) + r'\b', kw):
                    key = brand_lower
                    brand_groups.setdefault(key, []).append(p)
                    break

        risks = []
        for brand_key, group in brand_groups.items():
            if len(group) < 2:
                continue
            # Check each pair
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    pa = group[i]
                    pb = group[j]
                    slug_a = pa.get("slug", "")
                    slug_b = pb.get("slug", "")
                    # Only flag if both pages exist and slugs are different
                    if not slug_a or not slug_b or slug_a == slug_b:
                        continue

                    city_a = (pa.get("location") or "").lower().strip()
                    city_b = (pb.get("location") or "").lower().strip()

                    # High risk: exact same city
                    if city_a and city_b and city_a == city_b:
                        risk_level = "high"
                        suggestion = (
                            f"Merge or differentiate: both '{pa['title']}' and "
                            f"'{pb['title']}' target {brand_key.title()} in {city_a.title()}. "
                            "Consider consolidating into one authoritative page."
                        )
                    # Medium risk: nearby cities with same brand
                    # Only relevant for multi-location businesses — single-location businesses
                    # intentionally create pages for all service cities (programmatic SEO strategy).
                    elif is_multilocation and city_a and city_b and (
                        city_b in _nearby_cities_for(city_a) or
                        city_a in _nearby_cities_for(city_b)
                    ):
                        risk_level = "medium"
                        suggestion = (
                            f"Nearby-city competition: '{pa['title']}' ({city_a.title()}) "
                            f"and '{pb['title']}' ({city_b.title()}) both target {brand_key.title()} "
                            "repair. Add city-specific differentiators (testimonials, certifier name, "
                            "local landmarks) to separate them in SERP intent."
                        )
                    else:
                        continue  # No meaningful risk

                    risks.append({
                        "page_slug_a":   slug_a,
                        "page_title_a":  pa.get("title", ""),
                        "page_slug_b":   slug_b,
                        "page_title_b":  pb.get("title", ""),
                        "shared_keyword": brand_key,
                        "risk_level":     risk_level,
                        "suggestion":     suggestion,
                    })

        return risks

    # ── Main run ───────────────────────────────────────────────────────────────

    def run(self):
        self.log("Starting enhanced competitor analysis...", "info")
        self.set_status("working", "Loading competitor list")

        # ── Step 1: Load manually configured competitors + those already in DB ─
        competitors = list(self.ctx.competitors or [])
        db_rows = db_execute(
            "SELECT name, url, location, certified_brands, tier, competitor_score "
            "FROM competitors WHERE tenant_id=?", (self.ctx.tenant_id,)
        )
        existing_names = {r["name"].lower() for r in db_rows}
        for comp in competitors:
            if comp.get("name", "").lower() not in existing_names:
                db_write(
                    "INSERT OR IGNORE INTO competitors (tenant_id, name, url, discovery_method) "
                    "VALUES (?,?,?,?)",
                    (self.ctx.tenant_id, comp.get("name", ""), comp.get("url", ""), "manual")
                )

        # ── Step 2: Geo-radius discovery ─────────────────────────────────────────
        our_brands   = self.ctx.certifications or []
        our_cities   = self.ctx.all_cities or [self.ctx.primary_city]
        primary_city = self.ctx.primary_city
        industry     = self.ctx.industry_vertical or "general"
        services_sample = ", ".join(self.ctx.services[:3]) if self.ctx.services else industry

        # Try Apify Maps first for real Google Maps data
        from denzo.agents.utils.apify_service import ApifyService
        apify = ApifyService(log_fn=lambda m, l="info": self.log(m, l))

        if apify.available() and primary_city:
            self.set_status("working", "Discovering real competitors via Google Maps (Apify)")
            self.log(f"[APIFY REAL] Searching Google Maps for competitors near {primary_city}...")

            # Build search queries: brand-certified + general category
            map_queries = []
            for brand in our_brands[:3]:
                map_queries.append(f"{brand} certified {services_sample} near {primary_city} {self.ctx.state}")
            if not map_queries:
                map_queries.append(f"{services_sample} near {primary_city} {self.ctx.state}")

            map_places = apify.find_local_businesses(map_queries, max_per_query=20)
            self.log(f"[APIFY MAPS] Found {len(map_places)} places in Google Maps")

            for place in map_places:
                cname = place.get("name", "").strip()
                curl  = place.get("url", "").strip()
                ccity = place.get("city", "").strip() or primary_city
                if not cname:
                    continue
                if self.ctx.client_name.lower() in cname.lower():
                    continue
                if cname.lower() in existing_names:
                    continue
                if _is_dealership_by_name(cname) or _is_dealership_by_url(curl):
                    continue
                if self._is_tier_mismatch(cname, curl):
                    continue

                existing_names.add(cname.lower())
                db_write(
                    "INSERT OR IGNORE INTO competitors "
                    "(tenant_id, name, url, location, discovery_method) VALUES (?,?,?,?,?)",
                    (self.ctx.tenant_id, cname, curl, ccity, "apify_maps")
                )
                rating = place.get("rating")
                rev_ct = place.get("reviews_count", 0)
                self.log(
                    f"[Maps] {cname} ({ccity})"
                    + (f" — ★{rating} ({rev_ct} reviews)" if rating else ""),
                    "info"
                )

        our_vertical  = (self.ctx.industry_vertical or "").lower()
        we_are_collision = any(v in our_vertical for v in ["collision", "body", "auto_body"])
        we_are_dealer    = any(v in our_vertical for v in ["dealership", "dealer"])

        # ── Dealership branch: same-brand-different-territory + other-brand-same-territory ──
        if we_are_dealer and primary_city:
            self.set_status("working", "Discovering dealership competitors (same brand + other brands)")
            self.log(
                f"Dealership client — searching: (1) same-brand nearby dealers, "
                f"(2) other-brand dealers in {primary_city} area..."
            )
            dealer_found = self._search_dealership_competitors(primary_city)
            for comp in dealer_found:
                cname = comp.get("name", "").strip()
                curl  = comp.get("url", "").strip()
                ccity = comp.get("city", "").strip()
                tier_hint = comp.get("competition_tier", 2)  # 1=same brand, 2=other brand
                brand_make = comp.get("brand_make", "")
                if not cname:
                    continue
                if self.ctx.client_name.lower() in cname.lower():
                    continue
                if cname.lower() in existing_names:
                    continue
                existing_names.add(cname.lower())
                cert_json = json.dumps([brand_make]) if brand_make else "[]"
                db_write(
                    "INSERT OR IGNORE INTO competitors "
                    "(tenant_id, name, url, location, certified_brands, discovery_method, tier) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (self.ctx.tenant_id, cname, curl, ccity,
                     cert_json, "geo_radius", tier_hint)
                )
                tier_label = "Tier 1 (same brand)" if tier_hint == 1 else "Tier 2 (other brand)"
                self.log(f"Discovered dealer [{tier_label}]: {cname} ({ccity}) — {brand_make}", "info")

        # ── Non-dealership branch: brand-certified geo-radius search ──
        elif our_brands and primary_city:
            self.set_status("working", "Discovering geo-radius brand-certified competitors")

            for brand in our_brands[:3]:   # top 3 brands to avoid over-calling
                if self.should_stop():
                    return
                self.log(f"Searching {brand}-certified shops near {primary_city} (AI)...")
                found = self._search_certified_competitors(brand, primary_city)
                for comp in found:
                    cname = comp.get("name", "").strip()
                    curl  = comp.get("url", "").strip()
                    ccity = comp.get("city", "").strip()
                    btype = comp.get("business_type", "unknown")
                    if not cname:
                        continue
                    if self.ctx.client_name.lower() in cname.lower():
                        continue
                    if cname.lower() in existing_names:
                        continue

                    if we_are_collision:
                        if _is_dealership_by_name(cname) or _is_dealership_by_url(curl) or btype == "dealership":
                            self.log(
                                f"Filtered (dealership): {cname} — not same industry as collision shop", "warning"
                            )
                            continue
                    else:
                        # Non-collision non-dealer: still block obvious dealerships
                        if _is_dealership_by_name(cname) or _is_dealership_by_url(curl):
                            self.log(f"Filtered (dealership): {cname} — not same industry", "warning")
                            continue

                    if self._is_tier_mismatch(cname, curl):
                        inferred_tier = self._infer_competitor_tier(cname, curl)
                        self.log(
                            f"Filtered (tier mismatch): {cname} — "
                            f"appears to be '{inferred_tier}' tier, client is '{getattr(self.ctx, 'brand_tier', 'mid')}'",
                            "warning"
                        )
                        continue

                    existing_names.add(cname.lower())
                    certified_brands = json.dumps([brand])
                    db_write(
                        "INSERT OR IGNORE INTO competitors "
                        "(tenant_id, name, url, location, certified_brands, discovery_method) "
                        "VALUES (?,?,?,?,?,?)",
                        (self.ctx.tenant_id, cname, curl, ccity, certified_brands, "geo_radius")
                    )
                    self.log(f"Discovered: {cname} ({ccity}) — {brand} certified", "info")

        # ── Step 3: Reload all competitors for analysis ──────────────────────
        if self.should_stop():
            return

        all_comps = db_execute(
            "SELECT id, name, url, location, certified_brands FROM competitors "
            "WHERE tenant_id=? ORDER BY id",
            (self.ctx.tenant_id,)
        )
        if not all_comps:
            self.log("No competitors to analyze.", "warning")
            self.set_status("idle", "No competitors found")
            return

        self.log(f"Analyzing {len(all_comps)} competitors...")

        # ── Step 4: Scrape + score each competitor ───────────────────────────
        scraped_data = []
        for row in all_comps[:8]:   # cap at 8 to avoid timeouts
            if self.should_stop():
                return
            cid   = row["id"]
            name  = row["name"]
            url   = row["url"] or ""
            city  = row["location"] or ""
            try:
                cert_brands = json.loads(row["certified_brands"] or "[]")
            except Exception:
                cert_brands = []

            comp_data = {
                "id": cid, "name": name, "url": url,
                "city": city, "certified_brands": cert_brands
            }

            # Compute tier and score
            tier, score = self._compute_competitor_score(comp_data, our_brands, our_cities)

            # Scrape website if URL available
            scraped = {}
            if url:
                self.set_status("working", f"Scraping {name}")
                self.log(f"Scraping {name}...")
                scraped = self._scrape(url)
                # If site is bot-protected, fall back to AI-generated competitive intel
                if not scraped.get("ok"):
                    self.log(f"Site blocked — using AI analysis for {name}", "info")
                    ai_data = self._ai_analyze_competitor(name, url, city)
                    if ai_data:
                        scraped.update(ai_data)
                        scraped["ok"] = True
                        scraped["source"] = "ai_generated"
                # Detect industry from scraped content
                scraped["detected_industry"] = _classify_scraped_content(scraped)

            comp_data.update(scraped)

            # Compute tier and score AFTER industry detection
            tier, score = self._compute_competitor_score(comp_data, our_brands, our_cities)
            comp_data["tier"]  = tier
            comp_data["score"] = score

            # Tier 0 = different industry — log and skip saving
            if tier == 0:
                self.log(
                    f"Skipped (different industry): {name} — "
                    f"detected as {scraped.get('detected_industry', 'unknown')}", "warning"
                )
                # Mark in DB as tier 0 so it can be hidden in the UI
                db_write(
                    "UPDATE competitors SET tier=0, competitor_score=0 WHERE id=? AND tenant_id=?",
                    (cid, self.ctx.tenant_id)
                )
                continue  # Do not include in gap analysis

            scraped_data.append(comp_data)

            # Save tier, score, strengths, weaknesses, keyword themes to DB
            strengths = comp_data.get("strengths", "")
            weaknesses = comp_data.get("weaknesses", "")
            kw_themes = comp_data.get("keyword_themes", [])
            kw_themes_json = json.dumps(kw_themes) if kw_themes else "[]"
            title_note = comp_data.get("title", "")
            db_write(
                "UPDATE competitors SET tier=?, competitor_score=?, strengths=?, weaknesses=?, "
                "notes=?, top_keywords=? WHERE id=? AND tenant_id=?",
                (tier, score, strengths, weaknesses, title_note, kw_themes_json, cid, self.ctx.tenant_id)
            )

        if not scraped_data:
            self.log("No competitor data collected.", "warning")
            return

        # ── Step 5: AI gap analysis ──────────────────────────────────────────
        self.set_status("working", "Running AI gap analysis")
        self.log("Analyzing competitor gaps with AI...")

        # Build compact summary (respect token limits)
        summary_items = []
        for c in scraped_data:
            item = {
                "name": c.get("name"),
                "url": c.get("url"),
                "city": c.get("city"),
                "tier": c.get("tier"),
                "certified_brands": c.get("certified_brands", []),
                "title": c.get("title", ""),
                "h1": c.get("h1", [])[:3],
                "h2": c.get("h2", [])[:5],
            }
            summary_items.append(item)
        summary = json.dumps(summary_items, ensure_ascii=False)[:6000]

        our_pages = db_execute(
            "SELECT title, slug, location, target_keyword FROM pages "
            "WHERE tenant_id=? LIMIT 50",
            (self.ctx.tenant_id,)
        )
        our_page_slugs = [p["slug"] for p in our_pages] if our_pages else []
        our_cities_str = ", ".join(our_cities[:15])
        our_brands_str = ", ".join(our_brands[:10]) or "N/A"

        industry   = self.ctx.industry_vertical or "general"
        brand_tier = getattr(self.ctx, "brand_tier", "mid")
        system_prompt = f"""You are a Senior Local SEO Strategist specializing in geo-competitive analysis.

CLIENT INDUSTRY: {industry}
CLIENT BRAND TIER: {brand_tier}
Our client: {self.ctx.client_name}
Our certifications/brands: {our_brands_str}
Our service cities: {our_cities_str}
Our existing pages (slugs): {', '.join(our_page_slugs[:30]) or 'none yet'}

BRAND TIER CONTEXT:
- Client brand tier: {brand_tier}. Only compare with competitors at the same brand tier level.
- For 'budget' tier clients, compare with other budget/value brands.
- For 'mid' tier clients, compare with other mid-range brands.
- For 'premium' tier clients, compare with other premium brands.
- For 'luxury' tier clients, compare ONLY with other luxury/premium brands.
- Do NOT flag budget competitors as threats to luxury clients, or vice versa — they serve different markets.

INDUSTRY CONTEXT:
- Analyze competitors ONLY within the same industry as our client ({industry})
- A competitor must offer the same category of product/service as our client
- Do NOT include competitors from adjacent or unrelated industries
  (e.g. if client is a body shop, exclude car dealerships; if client is a law firm, exclude accountants)

CERTIFICATION INSIGHT (when applicable):
- Brand certifications signal authority and narrow the competitive pool
- Customers may travel 30-40 miles to reach a certified specialist
- Tier 1: Same certification/specialization + within 35-mile radius → HIGH threat
- Tier 2: Different specialization OR farther geography → LOWER threat

Analyze with this industry, brand tier, and geographic intelligence built in."""

        prompt = f"""Here are our competitors (pre-scored):
{summary}

For each competitor, identify:
1. Their strongest keyword themes (what they rank for)
2. Cities they target that we DON'T have pages for (gap cities)
3. Brand certifications they advertise
4. Content weaknesses we can exploit

Also identify:
- Top 15 gap keywords (keywords they rank for, we don't)
- Top 5 strategic opportunities for us

Return ONLY valid JSON (no markdown):
{{
  "competitors": [
    {{
      "name": "...",
      "url": "...",
      "tier": 1,
      "certified_brands": ["BMW", "Mercedes"],
      "strengths": ["strength 1", "strength 2"],
      "weaknesses": ["weakness 1"],
      "keyword_themes": ["theme 1"],
      "gap_cities": ["city we don't cover"],
      "gap_keywords": ["keyword they rank for we don't"]
    }}
  ],
  "gap_keywords": ["keyword gap 1", "keyword gap 2"],
  "opportunities": ["opportunity 1", "opportunity 2"],
  "brand_threats": {{
    "BMW": ["competitor A in Glendale", "competitor B in Pasadena"],
    "Mercedes": []
  }}
}}
"""
        raw = self.call_claude(prompt, max_tokens=3500,
                               system=system_prompt, model="claude-sonnet-4-6")
        if not raw:
            self.log("AI analysis returned empty response.", "error")
            return

        try:
            raw = strip_json_fences(raw)
            result = json.loads(raw)
        except Exception as e:
            self.log(f"JSON parse error: {e}", "error")
            return

        # ── Step 6: Store AI intelligence back to DB ─────────────────────────
        for comp in result.get("competitors", []):
            cname           = comp.get("name", "")
            strengths       = json.dumps(comp.get("strengths", []))
            weaknesses      = json.dumps(comp.get("weaknesses", []))
            keyword_themes  = comp.get("keyword_themes", [])
            gap_cities      = json.dumps(comp.get("gap_cities", []))
            gap_kws         = json.dumps(comp.get("gap_keywords", []))
            cert_brands     = json.dumps(comp.get("certified_brands", []))
            tier_from_ai    = comp.get("tier", 2)
            notes           = "Keywords: " + ", ".join(keyword_themes[:8])

            db_write(
                """UPDATE competitors
                   SET strengths=?, weaknesses=?, notes=?,
                       certified_brands=?, gap_cities=?, gap_keywords_json=?,
                       tier=?
                   WHERE tenant_id=? AND name=?""",
                (strengths, weaknesses, notes,
                 cert_brands, gap_cities, gap_kws,
                 tier_from_ai,
                 self.ctx.tenant_id, cname)
            )

        # ── Step 7: Save gap keywords (with SERP industry filter) ───────────
        gap_kw_list = result.get("gap_keywords", [])
        if gap_kw_list:
            self.set_status("working", "Verifying gap keywords — SERP industry filter")
            self.log(
                f"Running SERP industry filter on {len(gap_kw_list)} gap keywords "
                f"(industry: {industry})..."
            )
            relevant_kws, dropped_kws = self._verify_keyword_industry_relevance(gap_kw_list, industry)
            if dropped_kws:
                self.log(
                    f"[SERP Filter] Removed {len(dropped_kws)} cross-industry keywords: "
                    + ", ".join(f'"{k}"' for k in dropped_kws[:6])
                    + ("…" if len(dropped_kws) > 6 else ""),
                    "warning"
                )
            self.log(f"[SERP Filter] Keeping {len(relevant_kws)} industry-relevant gap keywords.", "info")
        else:
            relevant_kws = []

        for kw in relevant_kws:
            self.add_keyword(kw, category="competitor_gap", priority="high")

        # ── Step 8: Cannibalization detection ────────────────────────────────
        if self.should_stop():
            return
        self.set_status("working", "Detecting keyword cannibalization")
        self.log("Checking for internal keyword cannibalization...")
        risks = self._detect_cannibalization()
        cannibal_count = 0
        for risk in risks:
            try:
                db_write(
                    """INSERT OR IGNORE INTO cannibalization_risks
                       (tenant_id, page_slug_a, page_title_a, page_slug_b, page_title_b,
                        shared_keyword, risk_level, suggestion)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (self.ctx.tenant_id,
                     risk["page_slug_a"], risk["page_title_a"],
                     risk["page_slug_b"], risk["page_title_b"],
                     risk["shared_keyword"], risk["risk_level"],
                     risk["suggestion"])
                )
                cannibal_count += 1
            except Exception:
                pass  # duplicate row — already flagged

        if cannibal_count > 0:
            self.log(
                f"{cannibal_count} cannibalization risk(s) detected — "
                "check the Competitors tab for details.",
                "warning"
            )

        # ── Step 9: Log opportunities ─────────────────────────────────────────
        opps = result.get("opportunities", [])
        if opps:
            self.log("Opportunities: " + " | ".join(opps[:5]), "success")

        brand_threats = result.get("brand_threats", {})
        for brand, shops in brand_threats.items():
            if shops:
                self.log(
                    f"Brand threat — {brand}: {', '.join(shops[:4])}", "warning"
                )

        tier1_count = sum(1 for c in scraped_data if c.get("tier") == 1)
        self.log(
            f"Analysis complete. {tier1_count} Tier-1 competitors | "
            f"{len(gap_kw_list)} gap keywords | {cannibal_count} cannibalization risks.",
            "success"
        )
        self.set_status("done", "Competitor analysis complete")
