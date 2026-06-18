import ipaddress
import json
import os
import re
import socket
import anthropic
import requests as http_requests
from bs4 import BeautifulSoup
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from denzo.auth import tenant_access_required
from denzo.db import get_db, slugify
from denzo.agents.registry import DEFAULT_AGENTS, AGENT_REGISTRY
from denzo.agents.utils.stealth_fetch import fetch_html

_SAFE_TABLE_NAMES = frozenset([
    "activity", "agents", "keywords", "pages", "competitors",
    "geo_queries", "site_images", "pipeline_runs", "settings",
    "client_context", "locations", "cannibalization_risks",
    "geo_query_bank",
])


def _is_safe_url(url: str) -> bool:
    """Return False if the URL resolves to a private/internal IP (SSRF guard)."""
    try:
        parsed = __import__("urllib.parse", fromlist=["urlparse"]).urlparse(url)
        host = parsed.hostname
        if not host:
            return False
        ip = socket.gethostbyname(host)
        addr = ipaddress.ip_address(ip)
        return not (addr.is_private or addr.is_loopback or addr.is_link_local
                    or addr.is_reserved or addr.is_multicast)
    except Exception:
        return False

bp = Blueprint("clients", __name__, url_prefix="/clients")


def _get_all_clients_slim():
    """Lightweight list for sidebar rendering."""
    db = get_db()
    rows = db.execute("""
        SELECT c.tenant_id, c.name, ag.name AS active_agent_name
        FROM clients c
        LEFT JOIN agents ag ON ag.tenant_id = c.tenant_id AND ag.status = 'working'
        GROUP BY c.tenant_id
        ORDER BY c.name
    """).fetchall()
    clients = [
        {"tenant_id": r["tenant_id"], "name": r["name"], "active_agent": r["active_agent_name"]}
        for r in rows
    ]
    db.close()
    return clients


@bp.route("/")
@tenant_access_required
def list_clients():
    db = get_db()
    rows = db.execute("""
        SELECT c.tenant_id, c.name, c.business_type, c.website_url, c.status, c.created_at,
               COUNT(DISTINCT k.id) AS keyword_count,
               COUNT(DISTINCT p.id) AS page_count
        FROM clients c
        LEFT JOIN keywords k ON k.tenant_id = c.tenant_id
        LEFT JOIN pages    p ON p.tenant_id = c.tenant_id
        GROUP BY c.tenant_id
        ORDER BY c.name
    """).fetchall()
    clients_slim = _get_all_clients_slim()
    db.close()
    return render_template("clients/list.html",
                           all_clients=rows,
                           clients=clients_slim)


@bp.route("/new")
@tenant_access_required
def new_client():
    clients = _get_all_clients_slim()
    return render_template("clients/new.html", clients=clients)


@bp.route("/analyze", methods=["POST"])
@tenant_access_required
def analyze_website():
    """Scrape a website and use Claude to extract all business context."""
    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400
    if not url.startswith("http"):
        url = "https://" + url
    if not _is_safe_url(url):
        return jsonify({"error": "URL not allowed"}), 400

    # ── 1. Scrape ──────────────────────────────────────────────────────────────
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        resp = http_requests.get(url, timeout=15, headers=headers, allow_redirects=True)
        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove scripts/styles
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        title       = soup.title.string.strip() if soup.title and soup.title.string else ""
        meta_el     = soup.find("meta", {"name": re.compile(r"description", re.I)})
        meta_desc   = meta_el.get("content", "").strip() if meta_el else ""
        headings    = [h.get_text(" ", strip=True) for h in soup.find_all(["h1","h2","h3"])][:30]
        paragraphs  = [p.get_text(" ", strip=True) for p in soup.find_all("p") if len(p.get_text(strip=True)) > 40][:15]
        list_items  = [li.get_text(" ", strip=True) for li in soup.find_all("li") if len(li.get_text(strip=True)) > 5][:40]
        anchor_text = list({a.get_text(" ", strip=True) for a in soup.find_all("a") if a.get_text(strip=True)})[:40]

        scraped = {
            "title": title,
            "meta_description": meta_desc,
            "headings": headings,
            "paragraphs": paragraphs,
            "list_items": list_items,
            "links": anchor_text,
        }
        scrape_error = None
    except Exception as e:
        scraped = {}
        scrape_error = str(e)

    # ── 2. Claude extraction ───────────────────────────────────────────────────
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY not configured"}), 500

    client = anthropic.Anthropic(api_key=api_key, base_url="https://api.anthropic.com")

    context_block = json.dumps(scraped, ensure_ascii=False) if scraped else f"URL: {url} — scrape failed: {scrape_error}"

    prompt = f"""You are an expert at analyzing business websites and extracting structured SEO context.

Analyze the following website content and return a JSON object with ALL fields populated.

URL: {url}
SCRAPED CONTENT:
{context_block}

Return ONLY a valid JSON object — no markdown, no explanation — with exactly these fields:

{{
  "business_name": "Full business name",
  "business_type": "one of: auto_body_shop | automotive_dealership | dental_clinic | medical_clinic | law_firm | insurance_agency | restaurant | real_estate | home_services | veterinary | education_academy | online_courses | coaching | saas_tech | agency | ecommerce | gym | hotel | spa | financial_services | other",
  "tagline": "The brand's main value proposition or slogan",
  "phone": "Phone number if found, else empty string",
  "address": "Street address if found",
  "city": "Main city",
  "state": "State abbreviation e.g. CA",
  "primary_city": "The city where the business is primarily located",
  "service_cities": ["city1", "city2"],
  "services": ["service or product 1", "service 2", "service 3"],
  "certifications": ["cert1", "cert2"],
  "differentiators": ["what makes them unique 1", "unique point 2"],
  "insurance_partners": ["insurer1", "insurer2"],
  "keywords": ["keyword 1", "keyword 2", "keyword 3"],
  "is_multilocation": false,
  "locations": [],
  "brand_tier": "mid",
  "competitors_tier1": [],
  "competitors_tier2": [],
  "suggested_competitors": []
}}

Rules:
- service_cities: list of cities/areas the business serves (not just HQ). Empty array if online-only.
- services: 5-10 specific services/products/programs. Be specific, not generic.
- certifications: manufacturer certs, board certs, accreditations, partnerships.
- differentiators: concrete differentiators (e.g. "OEM parts only", "Bilingual staff", "24/7 support").
- insurance_partners: only if relevant to the business type, else empty array.
- keywords: 10-15 high-value SEO keywords including location-based ones.
- suggested_competitors: 3-5 REAL competitors. Use your knowledge of the industry + location to suggest actual company names and likely URLs. Do not make up fake companies.
- If something cannot be determined from the content, use your best inference based on business type and location.

Additionally, analyze:

1. MULTILOCATION: Is this a multi-location business (franchise, chain, multiple branches)?
   - Set "is_multilocation" to true if yes, false if no.
   - If yes, list ALL locations found on the website (check /locations, /contact, footer, store-locator, etc.)
   - Each location object: {{"name": "...", "address": "...", "city": "...", "state": "...", "zip": "...", "phone": "...", "url": "..."}}
   - If single-location, "locations" must be an empty array [].

2. BRAND_TIER: What tier is this brand? Set "brand_tier" to one of:
   - "budget" = value/economy brands (McDonald's, Midas, Maaco, discount stores)
   - "mid" = standard/mainstream brands (Toyota, Honda, Caliber Collision, Applebee's)
   - "premium" = higher-end brands (BMW, Mercedes, upscale restaurants, boutique services)
   - "luxury" = ultra-premium brands (Rolls Royce, Porsche, 5-star hotels, Michelin restaurants)

3. COMPETITORS_TIER1: If this is a franchise/chain, list other locations of the SAME brand in nearby cities.
   These are Tier 1 franchise rivals (same brand, different location).
   Format: [{{"name": "...", "url": "...", "city": "..."}}]
   If not a franchise or cannot determine, use empty array [].

4. COMPETITORS_TIER2: Category competitors (different brands, same service category, same tier level).
   These are the traditional competitors (same as suggested_competitors but structured).
   Format: [{{"name": "...", "url": "..."}}]
   Always populate with 3-5 real competitors based on your knowledge of the industry and location.
"""

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = message.content[0].text.strip()
        # Strip any accidental markdown fences
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        result = json.loads(raw)
        result["_scraped_ok"] = scrape_error is None

        # ── Backward compatibility: competitors = competitors_tier2 ──────────────
        competitors_tier2 = result.get("competitors_tier2", [])
        if competitors_tier2:
            result["competitors"] = competitors_tier2
        elif "suggested_competitors" in result:
            # Fallback: map suggested_competitors into competitors if tier2 is empty
            result["competitors"] = result.get("suggested_competitors", [])
        else:
            result.setdefault("competitors", [])

        # Ensure new fields always exist with safe defaults
        result.setdefault("is_multilocation", False)
        result.setdefault("locations", [])
        result.setdefault("brand_tier", "mid")
        result.setdefault("competitors_tier1", [])
        result.setdefault("competitors_tier2", [])

        # ── Verify location URLs (lightweight HEAD check) ─────────────────────
        locations = result.get("locations", [])
        if result.get("is_multilocation") and locations:
            verified_locations = []
            main_url_normalized = url.rstrip("/")
            for loc in locations[:5]:
                loc_url = loc.get("url", "").strip()
                if loc_url and loc_url.rstrip("/") != main_url_normalized:
                    fetch_result = fetch_html(loc_url, timeout=10)
                    loc["_url_ok"] = fetch_result.get("ok", False)
                else:
                    loc["_url_ok"] = None  # no URL or same as main — skip
                verified_locations.append(loc)
            # Unverified locations (beyond the first 5) get no _url_ok flag
            result["locations"] = verified_locations + locations[5:]

        return jsonify(result)
    except json.JSONDecodeError as e:
        return jsonify({"error": f"Claude returned invalid JSON: {e}", "raw": raw[:500]}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/create", methods=["POST"])
@tenant_access_required
def create_client():
    # Enforce max_clients limit for non-admin users
    if session.get("role") != "admin":
        from denzo.billing.enforce import get_user_entitlements
        entitlements = get_user_entitlements(session.get("user_id"))
        max_clients = entitlements.get("max_clients", 1)
        db_check = get_db()
        current_count = db_check.execute(
            "SELECT COUNT(*) as n FROM clients WHERE owner_user_id=?", (session["user_id"],)
        ).fetchone()["n"]
        db_check.close()
        if current_count >= max_clients:
            flash(
                f"You've reached the limit of {max_clients} client(s) on your {entitlements['plan_name']} plan. "
                f"Upgrade to add more.",
                "error"
            )
            return redirect(url_for("public.upgrade_page"))

    f = request.form

    name          = f.get("name", "").strip()
    business_type = f.get("business_type", "other")
    website_url   = f.get("website_url", "").strip()
    phone         = f.get("phone", "").strip()
    address       = f.get("address", "").strip()
    city          = f.get("city", "").strip()
    state         = f.get("state", "CA").strip()

    tagline        = f.get("tagline", "").strip()
    primary_city   = f.get("primary_city", "").strip()
    domain         = f.get("domain", website_url).strip()

    # Service cities (local) or target audience (online) — both stored in service_cities
    cities_raw = f.get("service_cities", "") or f.get("target_audience", "")
    service_cities = json.dumps([c.strip() for c in cities_raw.split(",") if c.strip()])

    # Services: prefer services_json (from wizard), fallback to textarea
    services_json_raw = f.get("services_json", "").strip()
    if services_json_raw:
        try:
            svc_list = json.loads(services_json_raw)
            services = json.dumps([str(s).strip() for s in svc_list if str(s).strip()])
        except Exception:
            services = json.dumps([])
    else:
        services_raw = f.get("services", "")
        services = json.dumps([s.strip() for s in services_raw.splitlines() if s.strip()])

    # Don't-sell list
    dont_sell_json_raw = f.get("dont_sell_json", "[]").strip()
    try:
        dont_sell_list = json.loads(dont_sell_json_raw)
        dont_sell = json.dumps([str(s).strip() for s in dont_sell_list if str(s).strip()])
    except Exception:
        dont_sell = json.dumps([])

    # Certifications
    certs_raw = f.get("certifications", "")
    certifications = json.dumps([c.strip() for c in certs_raw.split(",") if c.strip()])

    # Differentiators: one per line
    diff_raw = f.get("differentiators", "")
    differentiators = json.dumps([d.strip() for d in diff_raw.splitlines() if d.strip()])

    # Insurance partners: comma-separated
    ins_raw = f.get("insurance_partners", "")
    insurance_partners = json.dumps([i.strip() for i in ins_raw.split(",") if i.strip()])

    # Competitors: submitted as JSON array from form
    competitors_json = f.get("competitors_json", "[]")
    try:
        comp_list = json.loads(competitors_json)
    except Exception:
        comp_list = []
    competitors = json.dumps(comp_list)

    # Multi-location + brand tier fields
    is_multilocation = f.get("is_multilocation", "false").lower() in ("true", "1", "yes")
    brand_tier = f.get("brand_tier", "mid").strip() or "mid"
    locations_raw = f.get("locations_json", "[]")
    try:
        locations_list = json.loads(locations_raw)
        if not isinstance(locations_list, list):
            locations_list = []
    except Exception:
        locations_list = []
    locations_json_str = json.dumps(locations_list)

    # Publishing config
    publisher_type  = f.get("publisher_type", "github")
    github_repo     = f.get("github_repo", "").strip()
    github_branch   = f.get("github_branch", "main").strip()
    github_token    = f.get("github_token", "").strip()
    wp_url          = f.get("wp_url", "").strip()
    wp_user         = f.get("wp_user", "").strip()
    wp_app_password = f.get("wp_app_password", "").strip()

    if not name:
        flash("Client name is required.", "error")
        return redirect(url_for("clients.new_client"))

    tenant_id = slugify(name)

    db = get_db()
    try:
        # Check uniqueness
        existing = db.execute(
            "SELECT id FROM clients WHERE tenant_id=?", (tenant_id,)
        ).fetchone()
        if existing:
            flash(f"A client with slug '{tenant_id}' already exists.", "error")
            db.close()
            return redirect(url_for("clients.new_client"))

        db.execute("""
            INSERT INTO clients (
                tenant_id, name, business_type, website_url, phone, address, city, state,
                publisher_type, is_multilocation, brand_tier, locations_json
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (tenant_id, name, business_type, website_url, phone, address, city, state,
              publisher_type, 1 if is_multilocation else 0, brand_tier, locations_json_str))

        github_format = f.get("github_format", "html").strip() or "html"
        pages_domain  = f.get("pages_domain", "").strip()

        # Encrypt tokens at rest (Fernet AES-128)
        from denzo.crypto import encrypt_token
        encrypted_gh_token    = encrypt_token(github_token)
        encrypted_wp_password = encrypt_token(wp_app_password)

        db.execute("""
            INSERT INTO client_context (
                tenant_id, tagline, service_cities, primary_city,
                certifications, services, differentiators, competitors,
                insurance_partners, domain, industry_vertical,
                github_repo, github_branch, github_token, github_format,
                wp_url, wp_user, wp_app_password,
                dont_sell, pages_domain, encrypted
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            tenant_id, tagline, service_cities, primary_city,
            certifications, services, differentiators, competitors,
            insurance_partners, domain, business_type,
            github_repo, github_branch, encrypted_gh_token, github_format,
            wp_url, wp_user, encrypted_wp_password,
            dont_sell, pages_domain, 1
        ))

        # Seed agents
        for agent_name in DEFAULT_AGENTS:
            _, _, layer, color = AGENT_REGISTRY[agent_name]
            db.execute("""
                INSERT OR IGNORE INTO agents (tenant_id, name, layer, color, status)
                VALUES (?,?,?,?,'idle')
            """, (tenant_id, agent_name, layer, color))

        # Seed locations table if multilocation
        if is_multilocation and locations_list:
            for loc in locations_list[:20]:  # cap at 20 locations
                db.execute(
                    "INSERT OR IGNORE INTO locations (tenant_id, name, address, city, state, url) VALUES (?,?,?,?,?,?)",
                    (tenant_id, loc.get("name", ""), loc.get("address", ""),
                     loc.get("city", ""), loc.get("state", ""), loc.get("url", ""))
                )

        # Log activity
        multiloc_note = f" Multilocation: {len(locations_list)} branches." if is_multilocation and locations_list else ""
        db.execute(
            "INSERT INTO activity (tenant_id, type, message, agent, level) VALUES (?,?,?,?,?)",
            (tenant_id, "system", f"Client '{name}' created. {len(DEFAULT_AGENTS)} agents seeded.{multiloc_note}", "system", "info")
        )

        db.commit()
    except Exception as e:
        db.rollback()
        db.close()
        flash(f"Error creating client: {e}", "error")
        return redirect(url_for("clients.new_client"))

    db.close()
    return redirect(url_for("pipeline.index", tenant_id=tenant_id))


@bp.route("/<tenant_id>/edit")
@tenant_access_required
def edit_client(tenant_id):
    db = get_db()
    client = db.execute(
        "SELECT * FROM clients WHERE tenant_id=?", (tenant_id,)
    ).fetchone()
    ctx = db.execute(
        "SELECT * FROM client_context WHERE tenant_id=?", (tenant_id,)
    ).fetchone()

    if not client:
        db.close()
        clients = _get_all_clients_slim()
        flash("Client not found.", "error")
        return redirect(url_for("clients.list_clients"))

    # Load tiered competitors from the competitors table
    comp_rows = db.execute(
        "SELECT name, url, location, tier, competitor_score FROM competitors WHERE tenant_id=? ORDER BY tier, competitor_score DESC",
        (tenant_id,)
    ).fetchall()
    competitors_tier1 = [dict(r) for r in comp_rows if r["tier"] == 1]
    competitors_tier2 = [dict(r) for r in comp_rows if r["tier"] in (2, None)]

    db.close()
    clients = _get_all_clients_slim()

    # Pre-parse JSON arrays for template rendering — return actual Python lists
    import json as _json
    def _jlist(val):
        """Parse JSON array string into Python list."""
        if not val:
            return []
        try:
            parsed = _json.loads(val)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            # Fallback: treat as comma-separated string
            return [v.strip() for v in val.split(",") if v.strip()]

    context = {}
    if ctx:
        # Competitors: always a list of dicts
        try:
            competitors = _json.loads(ctx["competitors"]) if ctx["competitors"] else []
            if not isinstance(competitors, list):
                competitors = []
        except Exception:
            competitors = []

        # Services/differentiators: may be JSON array or newline-separated
        def _jlines(val):
            if not val:
                return []
            try:
                parsed = _json.loads(val)
                return parsed if isinstance(parsed, list) else []
            except Exception:
                return [v.strip() for v in val.splitlines() if v.strip()]

        context = {
            "tagline":            ctx["tagline"] or "",
            "service_cities":     _jlist(ctx["service_cities"]),
            "certifications":     _jlist(ctx["certifications"]),
            "insurance_partners": _jlist(ctx["insurance_partners"]),
            "services":           _jlines(ctx["services"]),
            "differentiators":    _jlines(ctx["differentiators"]),
            "competitors":        competitors,
            "competitors_tier1":  competitors_tier1,
            "competitors_tier2":  competitors_tier2,
            "primary_city":       ctx["primary_city"] or "",
            "domain":             ctx["domain"] or "",
            "github_repo":        ctx["github_repo"] or "",
            "github_branch":      ctx["github_branch"] or "main",
            "github_format":      ctx["github_format"] or "html",
            "github_path_prefix": ctx["github_path_prefix"] or "",
            "pages_domain":       ctx["pages_domain"] or "",
            "dont_sell":          _jlist(ctx["dont_sell"]),
            "wp_url":             ctx["wp_url"] or "",
            "wp_user":            ctx["wp_user"] or "",
            "has_github_token":   bool(ctx["github_token"]),
            "has_wp_password":    bool(ctx["wp_app_password"]),
        }

    # ── Google Integrations status (GBP, GSC) ─────────────────────────────────
    from denzo.agents.utils import google_oauth
    google_integrations = {
        "configured": google_oauth.credentials_configured(),
        "providers": {},
    }
    for prov in ("gbp", "gsc"):
        row = google_oauth.get_token_row(tenant_id, prov)
        if row:
            google_integrations["providers"][prov] = {
                "connected":     True,
                "account_email": row.get("account_email") or "",
                "site_url":      row.get("site_url") or "",
                "location_id":   row.get("location_id") or "",
                "updated_at":    row.get("updated_at") or "",
            }
        else:
            google_integrations["providers"][prov] = {"connected": False}

    return render_template("clients/detail.html",
                           client=client,
                           ctx=ctx,
                           context=context,
                           clients=clients,
                           active_tenant=tenant_id,
                           google_integrations=google_integrations)


@bp.route("/<tenant_id>/update", methods=["POST"])
@tenant_access_required
def update_client(tenant_id):
    f = request.form
    db = get_db()

    # Parse new fields if present in request
    _is_ml_raw = f.get("is_multilocation", None)
    _brand_tier = f.get("brand_tier", "").strip()
    _locations_json_raw = f.get("locations_json", "").strip()

    # Build the UPDATE dynamically — only set new fields if submitted
    base_params = [
        f.get("name", "").strip(),
        f.get("business_type", "other"),
        f.get("website_url", "").strip(),
        f.get("phone", "").strip(),
        f.get("address", "").strip(),
        f.get("city", "").strip(),
        f.get("state", "CA").strip(),
        f.get("publisher_type", "github"),
    ]
    extra_sets = []
    extra_params = []

    if _is_ml_raw is not None:
        is_multilocation = _is_ml_raw.lower() in ("true", "1", "yes")
        extra_sets.append("is_multilocation=?")
        extra_params.append(1 if is_multilocation else 0)
    else:
        is_multilocation = None  # not submitted — don't update

    if _brand_tier:
        extra_sets.append("brand_tier=?")
        extra_params.append(_brand_tier)

    if _locations_json_raw:
        try:
            _locs = json.loads(_locations_json_raw)
            if not isinstance(_locs, list):
                _locs = []
        except Exception:
            _locs = []
        extra_sets.append("locations_json=?")
        extra_params.append(json.dumps(_locs))
    else:
        _locs = None

    extra_clause = (", " + ", ".join(extra_sets)) if extra_sets else ""

    # Update clients table
    db.execute(f"""
        UPDATE clients SET
            name=?, business_type=?, website_url=?, phone=?,
            address=?, city=?, state=?, publisher_type=?{extra_clause},
            updated_at=CURRENT_TIMESTAMP
        WHERE tenant_id=?
    """, (*base_params, *extra_params, tenant_id))

    def _parse_tag_field(raw):
        """Parse either a JSON array string or comma/newline separated string into a JSON string."""
        raw = raw.strip()
        if not raw:
            return json.dumps([])
        # Try JSON first (from Alpine tag inputs)
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return json.dumps([str(v).strip() for v in parsed if str(v).strip()])
        except Exception:
            pass
        # Fallback: comma-separated
        return json.dumps([v.strip() for v in raw.replace("\n", ",").split(",") if v.strip()])

    service_cities    = _parse_tag_field(f.get("service_cities", ""))
    services          = _parse_tag_field(f.get("services", ""))
    certifications    = _parse_tag_field(f.get("certifications", ""))
    differentiators   = _parse_tag_field(f.get("differentiators", ""))
    insurance_partners = _parse_tag_field(f.get("insurance_partners", ""))

    try:
        comp_list = json.loads(f.get("competitors", "[]"))
        if not isinstance(comp_list, list):
            comp_list = []
    except Exception:
        comp_list = []

    # Preserve existing token/password if blank submitted ("leave blank to keep")
    from denzo.crypto import encrypt_token
    existing = db.execute(
        "SELECT github_token, wp_app_password, encrypted FROM client_context WHERE tenant_id=?", (tenant_id,)
    ).fetchone()
    github_token    = f.get("github_token", "").strip()
    wp_app_password = f.get("wp_app_password", "").strip()
    # If the form value is already encrypted (Fernet prefix), keep as-is.
    # Otherwise encrypt new plaintext. Blank → preserve existing.
    if github_token:
        if not github_token.startswith("gAAAAAB"):
            github_token = encrypt_token(github_token)
    else:
        github_token = existing["github_token"] if existing else ""
    if wp_app_password:
        if not wp_app_password.startswith("gAAAAAB"):
            wp_app_password = encrypt_token(wp_app_password)
    else:
        wp_app_password = existing["wp_app_password"] if existing else ""

    # Preserve existing dont_sell and github_path_prefix if blank submitted
    existing_ctx = db.execute(
        "SELECT dont_sell, github_path_prefix, pages_domain, github_format FROM client_context WHERE tenant_id=?",
        (tenant_id,)
    ).fetchone()
    dont_sell_raw = f.get("dont_sell_json", "").strip()
    if dont_sell_raw:
        try:
            dont_sell_list = json.loads(dont_sell_raw)
            dont_sell = json.dumps([str(s).strip() for s in dont_sell_list if str(s).strip()])
        except Exception:
            dont_sell = existing_ctx["dont_sell"] if existing_ctx else "[]"
    else:
        dont_sell = existing_ctx["dont_sell"] if existing_ctx else "[]"

    github_format      = f.get("github_format", "").strip() or (existing_ctx["github_format"] if existing_ctx else "html")
    github_path_prefix = f.get("github_path_prefix", "").strip() or (existing_ctx["github_path_prefix"] if existing_ctx else "")
    pages_domain       = f.get("pages_domain", "").strip() or (existing_ctx["pages_domain"] if existing_ctx else "")

    db.execute("""
        UPDATE client_context SET
            tagline=?, service_cities=?, primary_city=?,
            certifications=?, services=?, differentiators=?,
            competitors=?, insurance_partners=?, domain=?,
            github_repo=?, github_branch=?, github_token=?,
            wp_url=?, wp_user=?, wp_app_password=?,
            industry_vertical=?, dont_sell=?,
            github_format=?, github_path_prefix=?, pages_domain=?,
            encrypted=1
        WHERE tenant_id=?
    """, (
        f.get("tagline", "").strip(),
        service_cities,
        f.get("primary_city", "").strip(),
        certifications, services, differentiators,
        json.dumps(comp_list), insurance_partners,
        f.get("domain", "").strip(),
        f.get("github_repo", "").strip(),
        f.get("github_branch", "main").strip(),
        github_token,
        f.get("wp_url", "").strip(),
        f.get("wp_user", "").strip(),
        wp_app_password,
        f.get("business_type", "other"),
        dont_sell, github_format, github_path_prefix, pages_domain,
        tenant_id
    ))

    # Sync locations table if new location data was provided
    if is_multilocation and _locs:
        # Delete existing locations for this tenant and re-insert
        db.execute("DELETE FROM locations WHERE tenant_id=?", (tenant_id,))
        for loc in _locs[:20]:  # cap at 20 locations
            db.execute(
                "INSERT OR IGNORE INTO locations (tenant_id, name, address, city, state, url) VALUES (?,?,?,?,?,?)",
                (tenant_id, loc.get("name", ""), loc.get("address", ""),
                 loc.get("city", ""), loc.get("state", ""), loc.get("url", ""))
            )

    db.execute(
        "INSERT INTO activity (tenant_id, type, message, agent, level) VALUES (?,?,?,?,?)",
        (tenant_id, "system", "Client settings updated.", "system", "info")
    )
    db.commit()
    db.close()

    flash("Client updated successfully.", "success")
    return redirect(url_for("clients.edit_client", tenant_id=tenant_id))


@bp.route("/<tenant_id>/delete", methods=["POST"])
@tenant_access_required
def delete_client(tenant_id):
    db = get_db()
    client = db.execute("SELECT name FROM clients WHERE tenant_id=?", (tenant_id,)).fetchone()
    if not client:
        flash("Client not found.", "error")
        db.close()
        return redirect(url_for("clients.list_clients"))

    # Delete all related data — every table that holds tenant_id
    for table in ["activity", "agents", "keywords", "pages", "competitors",
                  "geo_queries", "site_images", "pipeline_runs", "settings", "client_context",
                  "locations", "cannibalization_risks", "geo_query_bank"]:
        if table not in _SAFE_TABLE_NAMES:
            continue
        db.execute(f"DELETE FROM {table} WHERE tenant_id=?", (tenant_id,))
    db.execute("DELETE FROM clients WHERE tenant_id=?", (tenant_id,))
    db.commit()
    db.close()

    flash(f"Client '{client['name']}' deleted.", "success")
    return redirect(url_for("clients.list_clients"))
