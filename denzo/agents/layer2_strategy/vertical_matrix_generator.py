"""
Vertical Matrix Generator — Layer 2
Generates hundreds of high-intent page stubs based on the client's industry vertical.
Each vertical has its own "matrix" of damage types, modifiers, insurance keywords, etc.
This is what differentiates Denzo from generic SEO tools.
"""
import json
import re
from denzo.agents.base_agent import TenantAwareBaseAgent, ClientContext, db_execute, db_write, strip_json_fences


class VerticalMatrixGenerator(TenantAwareBaseAgent):

    def __init__(self, ctx: ClientContext):
        super().__init__("Vertical Matrix Generator", ctx, layer=2, color="fuchsia")

    VERTICAL_MATRICES = {
        "auto_body_shop": {
            "damage_types": [
                "hail damage", "bumper repair", "door ding repair",
                "fender bender repair", "frame damage", "paint scratch repair",
                "windshield repair", "flood damage repair",
            ],
            "insurance_keywords": [
                "State Farm approved", "USAA certified", "Allstate preferred",
                "Progressive direct repair", "Geico authorized", "Farmers approved",
                "AAA certified", "insurance claim help",
            ],
            "qualifiers": [
                "OEM parts", "certified", "free estimate", "lifetime warranty",
                "same day", "mobile", "paintless dent",
            ],
        },
        "collision_repair": {
            "damage_types": [
                "collision repair", "front end damage", "rear end damage",
                "T-bone collision", "side swipe repair", "total loss avoidance",
            ],
            "insurance_keywords": [
                "insurance approved", "direct repair program", "DRP shop",
            ],
            "qualifiers": [
                "certified technicians", "OEM certified", "free rental car", "towing",
            ],
        },
        "dental": {
            "procedures": [
                "teeth whitening", "dental implants", "Invisalign", "veneers",
                "root canal", "dental crown", "wisdom tooth removal", "deep cleaning",
                "dental bonding", "pediatric dentistry",
            ],
            "conditions": [
                "tooth pain", "broken tooth", "missing teeth", "yellow teeth",
                "crooked teeth", "gum disease", "dental emergency",
            ],
            "qualifiers": [
                "same day", "no insurance", "affordable", "sedation", "weekend",
            ],
        },
        "law_firm": {
            "practice_areas": [
                "personal injury", "car accident", "wrongful death", "slip and fall",
                "workers compensation", "DUI defense", "family law", "divorce",
                "criminal defense", "immigration", "estate planning",
            ],
            "case_types": [
                "free consultation", "no win no fee", "contingency", "bilingual",
                "Spanish speaking", "emergency",
            ],
            "qualifiers": [
                "experienced", "top rated", "award winning", "board certified",
            ],
        },
        "hvac": {
            "services": [
                "AC repair", "furnace repair", "heat pump installation", "duct cleaning",
                "AC installation", "emergency HVAC", "HVAC maintenance", "thermostat",
            ],
            "seasons": ["summer", "winter", "spring tune-up"],
            "qualifiers": [
                "24/7", "same day", "licensed", "financing available", "free estimate",
            ],
        },
        "plumbing": {
            "services": [
                "drain cleaning", "pipe repair", "water heater", "leak detection",
                "sewer line", "faucet repair", "toilet repair", "emergency plumbing",
            ],
            "qualifiers": [
                "24/7 emergency", "licensed", "same day", "free estimate", "no overtime",
            ],
        },
        "restaurant": {
            "occasions": [
                "birthday party", "corporate lunch", "family dinner", "date night",
                "brunch", "private event", "catering",
            ],
            "dietary": ["vegan", "gluten free", "halal", "kosher", "keto friendly"],
            "qualifiers": [
                "best", "authentic", "award winning", "delivery", "outdoor seating",
            ],
        },
        "auto_dealership": {
            "makes": [
                "Toyota", "Honda", "Ford", "Chevrolet", "Nissan",
                "Hyundai", "Kia", "Jeep", "Ram", "GMC",
                "Mazda", "Subaru", "Volkswagen", "BMW", "Mercedes-Benz",
            ],
            "vehicle_types": [
                "new", "used", "certified pre-owned",
            ],
            "financing_keywords": [
                "bad credit auto loans", "no credit check car loans",
                "first time buyer car loans", "lease deals", "zero down payment",
                "trade-in value", "buy here pay here", "in-house financing",
            ],
            "service_keywords": [
                "service center", "oil change", "parts department",
                "recall service", "tire rotation", "brake service",
            ],
            "qualifiers": [
                "family owned", "best price", "low miles",
                "certified", "factory warranty", "0 APR",
            ],
        },
        "general": {
            "generic_modifiers": [
                "near me", "best", "affordable", "top rated", "certified",
                "professional", "licensed", "free estimate", "same day",
            ],
        },
    }

    # Maps aliases → canonical matrix keys
    VERTICAL_ALIAS_MAP = {
        "auto_repair":       "auto_body_shop",
        "body_shop":         "auto_body_shop",
        "collision":         "collision_repair",
        "dentist":           "dental",
        "dental_clinic":     "dental",
        "dental_office":     "dental",
        "attorney":          "law_firm",
        "legal":             "law_firm",
        "heating_cooling":   "hvac",
        "air_conditioning":  "hvac",
        "dealership":        "auto_dealership",
        "dealer":            "auto_dealership",
        "car_dealer":        "auto_dealership",
        "auto_dealer":       "auto_dealership",
        "used_cars":         "auto_dealership",
        "new_cars":          "auto_dealership",
    }

    def _get_matrix_config(self) -> dict:
        vertical = self.ctx.industry_vertical or "general"
        key = self.VERTICAL_ALIAS_MAP.get(vertical, vertical)
        return self.VERTICAL_MATRICES.get(key, self.VERTICAL_MATRICES["general"]), key

    @staticmethod
    def _slugify(text: str) -> str:
        """Local slugify — avoids circular import issues."""
        try:
            from denzo.db import slugify as _db_slugify
            return _db_slugify(text)
        except Exception:
            s = text.lower().strip()
            s = re.sub(r"[^\w\s-]", "", s)
            s = re.sub(r"[\s_]+", "-", s)
            s = re.sub(r"-+", "-", s)
            return s.strip("-")

    def run(self):
        self.log("Starting Vertical Matrix generation...")
        self.set_status("working", "Checking prerequisites")
        ctx = self.ctx

        # ── Prereq: need >= 10 keywords ───────────────────────────────────────
        kw_check = db_execute(
            "SELECT COUNT(*) AS n FROM keywords WHERE tenant_id=?", (ctx.tenant_id,)
        )
        kw_total = kw_check[0]["n"] if kw_check else 0
        if kw_total < 10:
            self.log(
                f"Not enough keywords ({kw_total}/10). Run Keyword Strategist first.",
                "warning",
            )
            self.set_status("idle", f"Waiting for keywords ({kw_total}/10)")
            return

        # ── Prereq: E-E-A-T Architect must be done ────────────────────────────
        eeat_check = db_execute(
            "SELECT status FROM agents WHERE tenant_id=? AND name=?",
            (ctx.tenant_id, "E-E-A-T Architect"),
        )
        eeat_done = eeat_check and eeat_check[0]["status"] == "done"
        if not eeat_done:
            self.log(
                "E-E-A-T Architect has not completed yet. Run it before Vertical Matrix Generator.",
                "warning",
            )
            self.set_status("idle", "Waiting for E-E-A-T Architect")
            return

        # ── Page cap check ────────────────────────────────────────────────────
        cap_row = db_execute(
            "SELECT value FROM settings WHERE tenant_id=? AND key='max_pages_cap'",
            (ctx.tenant_id,),
        )
        MAX_PAGES_CAP = int(cap_row[0]["value"]) if cap_row else 500

        existing_pages = db_execute(
            "SELECT COUNT(*) AS n FROM pages WHERE tenant_id=? AND status IN ('draft','ready','published')",
            (ctx.tenant_id,),
        )
        current_count = existing_pages[0]["n"] if existing_pages else 0
        remaining_slots = MAX_PAGES_CAP - current_count

        if remaining_slots <= 0:
            self.log(
                f"Page cap reached ({current_count}/{MAX_PAGES_CAP}). "
                "Increase cap in Settings (max_pages_cap) to generate more pages.",
                "warning",
            )
            self.set_status("done", f"Cap reached ({current_count}/{MAX_PAGES_CAP}) — no new pages added")
            return

        max_this_run = min(200, remaining_slots)
        self.log(
            f"Page cap: {MAX_PAGES_CAP} | Existing: {current_count} | "
            f"Slots: {remaining_slots} | Max this run: {max_this_run}",
            "info",
        )

        # ── Load matrix config ─────────────────────────────────────────────────
        matrix_config, matrix_key = self._get_matrix_config()
        all_cities = ctx.all_cities
        if not all_cities:
            all_cities = [ctx.primary_city] if ctx.primary_city else ["local area"]

        self.log(
            f"Vertical: {ctx.industry_vertical} → Matrix: {matrix_key} | "
            f"Cities: {len(all_cities)} | Matrix keys: {list(matrix_config.keys())}",
            "info",
        )

        self.set_status("working", f"Generating matrix pages for {matrix_key}")

        pages_added   = 0
        pages_skipped = 0

        def _try_add(title: str, slug: str, page_type: str, city: str,
                     keyword: str, matrix_type: str):
            """Add page if slug is not already in DB. Returns True if added."""
            nonlocal pages_added, pages_skipped
            if pages_added >= max_this_run:
                return False
            existing = db_execute(
                "SELECT id FROM pages WHERE tenant_id=? AND slug=?",
                (ctx.tenant_id, slug),
            )
            if existing:
                pages_skipped += 1
                return True  # continue iterating but don't count as added
            self.add_page(
                title=title,
                slug=slug,
                page_type=page_type,
                location=city,
                target_keyword=keyword,
                notes=f"Matrix: {matrix_type}",
            )
            pages_added += 1
            return True

        # ── AUTO BODY / COLLISION ──────────────────────────────────────────────
        if matrix_key in ("auto_body_shop", "collision_repair"):
            damage_types      = matrix_config.get("damage_types", [])
            insurance_kws     = matrix_config.get("insurance_keywords", [])
            qualifiers        = matrix_config.get("qualifiers", [])
            top_services      = ctx.services[:3] if ctx.services else ["auto body repair"]
            top_qualifiers    = qualifiers[:3]
            certifications    = ctx.certifications[:6] if ctx.certifications else []

            # damage_type + city
            for damage in damage_types:
                for city in all_cities:
                    if pages_added >= max_this_run or self.should_stop():
                        break
                    keyword = f"{damage} {city}"
                    title   = f"{damage.title()} in {city}"
                    slug    = self._slugify(f"{damage}-{city}")
                    _try_add(title, slug, "service", city, keyword, f"{matrix_key}:damage_type")
                if pages_added >= max_this_run or self.should_stop():
                    break

            self.log(f"Damage-type pages: {pages_added} added so far", "info")

            # insurance_keyword + city
            for ins_kw in insurance_kws:
                for city in all_cities:
                    if pages_added >= max_this_run or self.should_stop():
                        break
                    keyword = f"{ins_kw} {city}"
                    title   = f"{ins_kw.title()} Body Shop in {city}"
                    slug    = self._slugify(f"{ins_kw}-body-shop-{city}")
                    _try_add(title, slug, "service", city, keyword, f"{matrix_key}:insurance")
                if pages_added >= max_this_run or self.should_stop():
                    break

            self.log(f"Insurance-keyword pages: {pages_added} added so far", "info")

            # qualifier + primary_service + city (top 3 × top 3 × all cities)
            for qual in top_qualifiers:
                for svc in top_services:
                    for city in all_cities:
                        if pages_added >= max_this_run or self.should_stop():
                            break
                        keyword = f"{qual} {svc} {city}"
                        title   = f"{qual.title()} {svc.title()} in {city}"
                        slug    = self._slugify(f"{qual}-{svc}-{city}")
                        _try_add(title, slug, "service", city, keyword, f"{matrix_key}:qualifier")
                    if pages_added >= max_this_run or self.should_stop():
                        break
                if pages_added >= max_this_run or self.should_stop():
                    break

            self.log(f"Qualifier pages: {pages_added} added so far", "info")

            # certification + "certified repair" + city
            for cert in certifications:
                for city in all_cities:
                    if pages_added >= max_this_run or self.should_stop():
                        break
                    keyword = f"{cert} certified repair {city}"
                    title   = f"{cert} Certified Collision Repair in {city}"
                    slug    = self._slugify(f"{cert}-certified-repair-{city}")
                    _try_add(title, slug, "service", city, keyword, f"{matrix_key}:certification")
                if pages_added >= max_this_run or self.should_stop():
                    break

        # ── DENTAL ────────────────────────────────────────────────────────────
        elif matrix_key == "dental":
            procedures = matrix_config.get("procedures", [])
            conditions = matrix_config.get("conditions", [])
            qualifiers = matrix_config.get("qualifiers", [])

            for proc in procedures:
                for city in all_cities:
                    if pages_added >= max_this_run or self.should_stop():
                        break
                    keyword = f"{proc} {city}"
                    title   = f"{proc.title()} in {city}"
                    slug    = self._slugify(f"{proc}-{city}")
                    _try_add(title, slug, "service", city, keyword, "dental:procedure")
                if pages_added >= max_this_run or self.should_stop():
                    break

            for cond in conditions:
                for city in all_cities:
                    if pages_added >= max_this_run or self.should_stop():
                        break
                    keyword = f"{cond} dentist {city}"
                    title   = f"{cond.title()} Dentist in {city}"
                    slug    = self._slugify(f"{cond}-dentist-{city}")
                    _try_add(title, slug, "service", city, keyword, "dental:condition")
                if pages_added >= max_this_run or self.should_stop():
                    break

            top_qualifiers = qualifiers[:3]
            for qual in top_qualifiers:
                for city in all_cities:
                    if pages_added >= max_this_run or self.should_stop():
                        break
                    keyword = f"{qual} dentist {city}"
                    title   = f"{qual.title()} Dentist in {city}"
                    slug    = self._slugify(f"{qual}-dentist-{city}")
                    _try_add(title, slug, "service", city, keyword, "dental:qualifier")
                if pages_added >= max_this_run or self.should_stop():
                    break

        # ── LAW FIRM ──────────────────────────────────────────────────────────
        elif matrix_key == "law_firm":
            practice_areas = matrix_config.get("practice_areas", [])
            case_types     = matrix_config.get("case_types", [])
            qualifiers     = matrix_config.get("qualifiers", [])

            for area in practice_areas:
                for city in all_cities:
                    if pages_added >= max_this_run or self.should_stop():
                        break
                    keyword = f"{area} attorney {city}"
                    title   = f"{area.title()} Attorney in {city}"
                    slug    = self._slugify(f"{area}-attorney-{city}")
                    _try_add(title, slug, "service", city, keyword, "law_firm:practice_area")
                if pages_added >= max_this_run or self.should_stop():
                    break

            for case_type in case_types:
                for city in all_cities:
                    if pages_added >= max_this_run or self.should_stop():
                        break
                    keyword = f"{case_type} lawyer {city}"
                    title   = f"{case_type.title()} Lawyer in {city}"
                    slug    = self._slugify(f"{case_type}-lawyer-{city}")
                    _try_add(title, slug, "service", city, keyword, "law_firm:case_type")
                if pages_added >= max_this_run or self.should_stop():
                    break

        # ── HVAC ──────────────────────────────────────────────────────────────
        elif matrix_key == "hvac":
            services   = matrix_config.get("services", [])
            seasons    = matrix_config.get("seasons", [])
            qualifiers = matrix_config.get("qualifiers", [])

            for svc in services:
                for city in all_cities:
                    if pages_added >= max_this_run or self.should_stop():
                        break
                    keyword = f"{svc} {city}"
                    title   = f"{svc.title()} in {city}"
                    slug    = self._slugify(f"{svc}-{city}")
                    _try_add(title, slug, "service", city, keyword, "hvac:service")
                if pages_added >= max_this_run or self.should_stop():
                    break

            top_qualifiers = qualifiers[:3]
            top_services   = services[:3]
            for qual in top_qualifiers:
                for svc in top_services:
                    for city in all_cities:
                        if pages_added >= max_this_run or self.should_stop():
                            break
                        keyword = f"{qual} {svc} {city}"
                        title   = f"{qual.title()} {svc.title()} in {city}"
                        slug    = self._slugify(f"{qual}-{svc}-{city}")
                        _try_add(title, slug, "service", city, keyword, "hvac:qualifier")
                    if pages_added >= max_this_run or self.should_stop():
                        break
                if pages_added >= max_this_run or self.should_stop():
                    break

        # ── PLUMBING ──────────────────────────────────────────────────────────
        elif matrix_key == "plumbing":
            services   = matrix_config.get("services", [])
            qualifiers = matrix_config.get("qualifiers", [])

            for svc in services:
                for city in all_cities:
                    if pages_added >= max_this_run or self.should_stop():
                        break
                    keyword = f"{svc} {city}"
                    title   = f"{svc.title()} in {city}"
                    slug    = self._slugify(f"{svc}-{city}")
                    _try_add(title, slug, "service", city, keyword, "plumbing:service")
                if pages_added >= max_this_run or self.should_stop():
                    break

            top_qualifiers = qualifiers[:3]
            top_services   = services[:3]
            for qual in top_qualifiers:
                for svc in top_services:
                    for city in all_cities:
                        if pages_added >= max_this_run or self.should_stop():
                            break
                        keyword = f"{qual} {svc} {city}"
                        title   = f"{qual.title()} {svc.title()} in {city}"
                        slug    = self._slugify(f"{qual}-{svc}-{city}")
                        _try_add(title, slug, "service", city, keyword, "plumbing:qualifier")
                    if pages_added >= max_this_run or self.should_stop():
                        break
                if pages_added >= max_this_run or self.should_stop():
                    break

        # ── AUTO DEALERSHIP ───────────────────────────────────────────────────
        elif matrix_key == "auto_dealership":
            makes         = matrix_config.get("makes", [])
            vehicle_types = matrix_config.get("vehicle_types", [])
            financing_kws = matrix_config.get("financing_keywords", [])
            service_kws   = matrix_config.get("service_keywords", [])
            qualifiers    = matrix_config.get("qualifiers", [])

            # 1. Make + vehicle_type + city — highest commercial intent
            for make in makes:
                for vt in vehicle_types:
                    for city in all_cities:
                        if pages_added >= max_this_run or self.should_stop():
                            break
                        keyword = f"{vt} {make} {city}"
                        title   = f"{vt.title()} {make} in {city}"
                        slug    = self._slugify(f"{vt}-{make}-{city}")
                        _try_add(title, slug, "inventory", city, keyword,
                                 "auto_dealership:make_type")
                    if pages_added >= max_this_run or self.should_stop():
                        break
                if pages_added >= max_this_run or self.should_stop():
                    break

            self.log(f"Make+type pages: {pages_added} added so far", "info")

            # 2. Financing + city — high-intent shoppers with credit needs
            for fin in financing_kws:
                for city in all_cities:
                    if pages_added >= max_this_run or self.should_stop():
                        break
                    keyword = f"{fin} {city}"
                    title   = f"{fin.title()} in {city}"
                    slug    = self._slugify(f"{fin}-{city}")
                    _try_add(title, slug, "financing", city, keyword,
                             "auto_dealership:financing")
                if pages_added >= max_this_run or self.should_stop():
                    break

            self.log(f"After financing pages: {pages_added} added", "info")

            # 3. Make + service + city — captures service-bay revenue
            top_makes_for_service = makes[:8]
            for svc in service_kws:
                for make in top_makes_for_service:
                    for city in all_cities:
                        if pages_added >= max_this_run or self.should_stop():
                            break
                        keyword = f"{make} {svc} {city}"
                        title   = f"{make} {svc.title()} in {city}"
                        slug    = self._slugify(f"{make}-{svc}-{city}")
                        _try_add(title, slug, "service", city, keyword,
                                 "auto_dealership:service")
                    if pages_added >= max_this_run or self.should_stop():
                        break
                if pages_added >= max_this_run or self.should_stop():
                    break

            self.log(f"After service pages: {pages_added} added", "info")

            # 4. Qualifier + make + dealer + city — brand differentiation
            top_qualifiers     = qualifiers[:3]
            top_makes_for_qual = makes[:5]
            for qual in top_qualifiers:
                for make in top_makes_for_qual:
                    for city in all_cities:
                        if pages_added >= max_this_run or self.should_stop():
                            break
                        keyword = f"{qual} {make} dealer {city}"
                        title   = f"{qual.title()} {make} Dealer in {city}"
                        slug    = self._slugify(f"{qual}-{make}-dealer-{city}")
                        _try_add(title, slug, "dealer", city, keyword,
                                 "auto_dealership:qualifier")
                    if pages_added >= max_this_run or self.should_stop():
                        break
                if pages_added >= max_this_run or self.should_stop():
                    break

        # ── RESTAURANT ────────────────────────────────────────────────────────
        elif matrix_key == "restaurant":
            occasions  = matrix_config.get("occasions", [])
            dietary    = matrix_config.get("dietary", [])
            qualifiers = matrix_config.get("qualifiers", [])

            for occ in occasions:
                for city in all_cities:
                    if pages_added >= max_this_run or self.should_stop():
                        break
                    keyword = f"{occ} restaurant {city}"
                    title   = f"{occ.title()} Restaurant in {city}"
                    slug    = self._slugify(f"{occ}-restaurant-{city}")
                    _try_add(title, slug, "service", city, keyword, "restaurant:occasion")
                if pages_added >= max_this_run or self.should_stop():
                    break

            for diet in dietary:
                for city in all_cities:
                    if pages_added >= max_this_run or self.should_stop():
                        break
                    keyword = f"{diet} restaurant {city}"
                    title   = f"{diet.title()} Restaurant in {city}"
                    slug    = self._slugify(f"{diet}-restaurant-{city}")
                    _try_add(title, slug, "service", city, keyword, "restaurant:dietary")
                if pages_added >= max_this_run or self.should_stop():
                    break

        # ── GENERAL FALLBACK ──────────────────────────────────────────────────
        else:
            generic_modifiers = matrix_config.get("generic_modifiers", [])
            top_services      = ctx.services[:5] if ctx.services else ["service"]

            for mod in generic_modifiers:
                for svc in top_services:
                    for city in all_cities:
                        if pages_added >= max_this_run or self.should_stop():
                            break
                        keyword = f"{mod} {svc} {city}"
                        title   = f"{mod.title()} {svc.title()} in {city}"
                        slug    = self._slugify(f"{mod}-{svc}-{city}")
                        _try_add(title, slug, "service", city, keyword, "general:modifier")
                    if pages_added >= max_this_run or self.should_stop():
                        break
                if pages_added >= max_this_run or self.should_stop():
                    break

        # ── Final status ──────────────────────────────────────────────────────
        self.log(
            f"Vertical Matrix complete: {pages_added} pages added, "
            f"{pages_skipped} skipped (already exist). "
            f"Matrix: {matrix_key} | Vertical: {ctx.industry_vertical}",
            "success",
        )
        self.set_status(
            "done",
            f"{pages_added} matrix pages added ({matrix_key}) | {pages_skipped} skipped",
        )
