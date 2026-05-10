"""
Public SaaS funnel — the full acquisition wizard.

Flow:
  POST /wizard/start        → runs analysis → session → redirect to /wizard/analyzing
  GET  /wizard/analyzing    → loading screen (3s fake progress) → redirect to /wizard/1
  GET  /wizard/<step>       → render that step (1-5)
  POST /wizard/<step>       → save step data → advance
  POST /wizard/complete     → create user + tenant → login → redirect to Lite
  GET  /upgrade             → trial/pricing page
  POST /upgrade/activate    → mock-activate trial
"""
import json
import logging
import re
import uuid
import os
from datetime import datetime, timedelta
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from flask import (
    Blueprint, render_template, request, redirect,
    url_for, session, flash, jsonify
)
from werkzeug.security import generate_password_hash

from denzo.db import get_db, slugify
from denzo.auth import login_required
from denzo.agents.registry import AGENT_REGISTRY, DEFAULT_AGENTS

logger = logging.getLogger(__name__)

bp = Blueprint("public", __name__)


# ── Website Analyzer ──────────────────────────────────────────────────────────

def _normalize_url(raw: str) -> str:
    raw = raw.strip()
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    return raw


# ── Word-boundary industry detection (bilingual EN/ES) ────────────────────────
# Each entry: (industry_key, [regex patterns using \b word boundaries])
# Ordered from most-specific to most-generic to avoid false positives.
_INDUSTRY_PATTERNS = [
    ("medical_imaging", [
        r"\b(rayos[\s\-]?x|x[\s\-]?ray|radiolog[íi]a|radiolog[yY]|ultrasonido|ultrasound|"
        r"mamograf[íi]a|mammograph|resonancia|mri|tomograf[íi]a|tomograph|ecograf[íi]a|"
        r"imagenolog[íi]a|imaging center|diagnostic imaging|radiol[oó]g[oi])\b"
    ]),
    ("dental", [
        r"\b(dental|dentist[ae]?|orthodontic|ortodoncia|implant[eo]s?|tooth|teeth|"
        r"ortodo[nt]|endodoncia|blanqueamiento|whitening)\b"
    ]),
    ("medical", [
        r"\b(m[eé]dic[oa]|cl[íi]nica|clinic|doctor|physician|hospital|healthcare|"
        r"salud|health|paciente|patient|consulta m[eé]dica)\b"
    ]),
    ("auto_body", [
        r"\b(collision repair|auto body|body shop|frame straightening|"
        r"paintless dent|car repair|carrocer[íi]a|colisi[oó]n)\b"
    ]),
    ("legal", [
        r"\b(attorney|lawyer|abogad[oa]|law firm|despacho legal|"
        r"legal services|litigat|litigation)\b"
    ]),
    ("real_estate", [
        r"\b(real estate|bienes ra[íi]ces|realtor|homes for sale|"
        r"inmobiliaria|propiedad|listing[s]?)\b"
    ]),
    ("restaurant", [
        r"\b(restaurant[e]?|men[úu]|dining|cocina|cuisine|reservat|cater)\b"
    ]),
    ("ecommerce", [
        r"\b(shop|tienda|cart|carrito|checkout|buy now|add to cart|e[-\s]?commerce)\b"
    ]),
    ("saas", [
        r"\b(software|platform|dashboard|saas|api|subscription|suscripci[oó]n|"
        r"cloud|automation|automatizaci[oó]n)\b"
    ]),
    ("education", [
        r"\b(school|escuela|universidad|university|course[s]?|curso[s]?|"
        r"learning|aprendizaje|diploma|degree|certificat)\b"
    ]),
    ("beauty", [
        r"\b(salon|spa|beauty|belleza|hair|pelo|nail|u[ñn]as|makeup|est[eé]tica)\b"
    ]),
]

_INDUSTRY_SERVICES = {
    "medical_imaging": ["Rayos X / X-Ray", "Ultrasonido", "Mamografía", "Resonancia Magnética",
                        "Tomografía Computarizada", "Densitometría Ósea"],
    "auto_body":       ["Collision Repair", "Auto Body Paint", "Frame Straightening",
                        "Paintless Dent Repair (PDR)", "Glass Replacement", "Bumper Repair"],
    "dental":          ["General Dentistry", "Teeth Whitening", "Orthodontics",
                        "Dental Implants", "Root Canal", "Veneers"],
    "medical":         ["Primary Care", "Urgent Care", "Telehealth",
                        "Preventive Care", "Specialist Referrals"],
    "legal":           ["Personal Injury", "Criminal Defense", "Family Law",
                        "Immigration", "Estate Planning", "Business Law"],
    "real_estate":     ["Home Buying", "Home Selling", "Property Management",
                        "Investment Properties", "Luxury Homes", "Commercial Real Estate"],
    "restaurant":      ["Dine-In", "Takeout", "Catering", "Private Events", "Delivery"],
    "ecommerce":       ["Online Store", "Product Catalog", "Free Shipping",
                        "Returns", "Customer Support"],
    "saas":            ["Dashboard", "API Access", "Integrations",
                        "Analytics", "Team Collaboration", "Onboarding"],
    "education":       ["Online Courses", "In-Person Classes", "Certification Programs",
                        "Workshops", "Corporate Training"],
    "beauty":          ["Haircuts & Styling", "Color & Highlights", "Nail Services",
                        "Facials", "Waxing", "Makeup"],
    "general":         [],  # will be extracted from page or left empty for user to fill
}

_INDUSTRY_DONT_SELL = {
    "auto_body":   ["Routine Mechanical Maintenance", "Vehicle Sales", "Fuel & Tires"],
    "dental":      ["Cosmetic Surgery", "Medical Prescriptions"],
    "legal":       ["Accounting", "Financial Advice"],
    "real_estate": ["Property Insurance", "Mortgage Lending"],
    "restaurant":  ["Grocery Delivery", "Cooking Classes"],
    "general":     ["Wholesale", "Franchising"],
}

_INDUSTRY_COMPETITORS = {
    "auto_body":   ["Caliber Collision", "Service King", "Gerber Collision",
                    "Fix Auto", "CARSTAR"],
    "dental":      ["Aspen Dental", "Western Dental", "Smile Direct Club"],
    "legal":       ["LegalZoom", "Avvo", "FindLaw"],
    "real_estate": ["Zillow", "Redfin", "Compass", "Coldwell Banker"],
    "restaurant":  ["DoorDash", "Grubhub", "Local chain competitors"],
    "general":     ["Industry leader 1", "Industry leader 2"],
}

_INDUSTRY_KEYWORDS = {
    "auto_body": [
        "auto body shop near me",
        "collision repair {city}",
        "certified body shop {city}",
        "bumper repair {city}",
        "car accident repair near me",
    ],
    "dental": [
        "dentist near me",
        "teeth whitening {city}",
        "dental implants {city}",
        "emergency dentist near me",
        "affordable dentist {city}",
    ],
    "legal": [
        "personal injury lawyer {city}",
        "attorney near me",
        "free legal consultation {city}",
        "best lawyer {city}",
        "law firm {city}",
    ],
    "real_estate": [
        "homes for sale {city}",
        "realtor near me",
        "buy a house {city}",
        "real estate agent {city}",
        "houses for sale {city}",
    ],
    "general": [
        "{business} near me",
        "best {business} in {city}",
        "{business} {city}",
        "affordable {business} {city}",
        "{business} reviews {city}",
    ],
}

_ARTICLE_TEMPLATES = {
    "auto_body": [
        "How Much Does Collision Repair Cost in {city}?",
        "Choosing a Certified Auto Body Shop in {city}: What You Need to Know",
        "What to Do After a Car Accident in {city}: A Step-by-Step Guide",
        "OEM vs Aftermarket Parts: Why It Matters for Your Repair",
        "How to File an Insurance Claim for Auto Body Repair in {city}",
    ],
    "dental": [
        "How Much Do Dental Implants Cost in {city}?",
        "Best Teeth Whitening Options in {city}: What Dentists Recommend",
        "Finding an Emergency Dentist in {city}: What to Expect",
        "Invisalign vs Braces: Which Is Right for You in {city}?",
        "How Often Should You Visit the Dentist? A Guide for {city} Residents",
    ],
    "general": [
        "Why {city} Residents Choose {business} for Their Needs",
        "How to Choose the Best {business} in {city}",
        "Top Questions to Ask Your {business} in {city}",
        "{business} in {city}: What to Expect and How to Prepare",
        "The Complete Guide to {business} Services in {city}",
    ],
}


def _analyze_website(url: str) -> dict:
    """
    Fetch a URL and run a comprehensive SEO audit.
    Scores are intentionally critical — most sites score 20-50.
    """
    domain = urlparse(url).netloc
    result = {
        "url": url, "domain": domain, "reachable": False,
        "business_name": "", "description": "", "city": "", "state": "", "phone": "",
        "title": "", "meta_description": "", "h1": "",
        "h1_count": 0, "h2_count": 0, "image_count": 0, "images_missing_alt": 0,
        "has_schema": False, "has_canonical": False, "noindex": False,
        "internal_links": 0, "page_size_kb": 0, "word_count": 0,
        "has_sitemap": False, "has_blog": False, "is_https": url.startswith("https"),
        "has_og": False, "has_viewport": False,
        "industry_guess": "general",
        "services": [], "dont_sell": [], "competitors": [],
        "keywords": [], "article_titles": [],
        "issues": [], "score": 0,
    }

    soup = None
    body_text = ""

    try:
        resp = requests.get(
            url, timeout=8,
            headers={"User-Agent": "Mozilla/5.0 (compatible; DenzoSEOBot/1.0)"},
            allow_redirects=True
        )
        result["reachable"] = resp.status_code < 400
        result["page_size_kb"] = round(len(resp.content) / 1024, 1)
        soup = BeautifulSoup(resp.text, "lxml")
        body_text = (soup.get_text(" ", strip=True) or "")
        result["word_count"] = len(body_text.split())
        body_lower = body_text.lower()

        # ── Title & meta ──────────────────────────────────────────────────────
        title_tag = soup.find("title")
        og_title  = soup.find("meta", attrs={"property": "og:title"})
        result["title"] = (
            (og_title.get("content", "").strip() if og_title else "") or
            (title_tag.get_text(strip=True) if title_tag else "")
        )
        meta_desc = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
        result["meta_description"] = meta_desc.get("content", "").strip() if meta_desc else ""

        # ── Business name ─────────────────────────────────────────────────────
        # Split on common title separators; pick the part that looks most like a brand.
        # Convention is "Keyword | Brand" (brand last) OR "Brand | Tagline" (brand first).
        # Heuristic: if the FIRST part is short (<= 20 chars) AND the last part is different,
        # the first part is likely a generic keyword/service — prefer the last part.
        title_parts = [p.strip() for p in re.split(r"\s*[\|–—]\s*|\s+[-]\s+", result["title"]) if p.strip()]
        if len(title_parts) >= 2 and len(title_parts[0]) <= 20:
            biz_name = title_parts[-1]
        else:
            biz_name = title_parts[0] if title_parts else ""
        # OG site name overrides title-derived name when available
        og_site = soup.find("meta", attrs={"property": "og:site_name"})
        if og_site and og_site.get("content", "").strip():
            biz_name = og_site["content"].strip()
        result["business_name"] = biz_name or domain.replace("www.", "").split(".")[0].title()
        result["description"] = result["meta_description"]

        # ── Headings ─────────────────────────────────────────────────────────
        h1s = soup.find_all("h1")
        result["h1"] = h1s[0].get_text(strip=True) if h1s else ""
        result["h1_count"] = len(h1s)
        result["h2_count"] = len(soup.find_all("h2"))

        # ── Images ───────────────────────────────────────────────────────────
        imgs = soup.find_all("img")
        result["image_count"] = len(imgs)
        result["images_missing_alt"] = sum(1 for i in imgs if not i.get("alt", "").strip())

        # ── Technical signals ─────────────────────────────────────────────────
        result["has_schema"]    = bool(soup.find("script", attrs={"type": "application/ld+json"}))
        result["has_canonical"] = bool(soup.find("link",   attrs={"rel": "canonical"}))
        result["has_og"]        = bool(soup.find("meta",   attrs={"property": re.compile(r"^og:")}))
        result["has_viewport"]  = bool(soup.find("meta",   attrs={"name": re.compile(r"^viewport$", re.I)}))
        robots_meta = soup.find("meta", attrs={"name": re.compile(r"^robots$", re.I)})
        if robots_meta:
            result["noindex"] = "noindex" in robots_meta.get("content", "").lower()

        # ── Internal links ────────────────────────────────────────────────────
        all_links = soup.find_all("a", href=True)
        result["internal_links"] = sum(
            1 for a in all_links if domain in a["href"] or a["href"].startswith("/")
        )

        # ── Blog detection (look for /blog, /news, /articles in links) ────────
        link_hrefs = [a.get("href", "").lower() for a in all_links]
        result["has_blog"] = any(
            any(kw in h for kw in ["/blog", "/news", "/articles", "/post", "/articulo", "/noticias"])
            for h in link_hrefs
        )

        # ── Location from Schema.org ──────────────────────────────────────────
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            try:
                data = json.loads(script.string or "{}")
                if isinstance(data, list): data = data[0]
                addr = data.get("address", {})
                result["city"]  = result["city"]  or addr.get("addressLocality", "")
                result["state"] = result["state"] or addr.get("addressRegion", "")
                result["phone"] = result["phone"] or data.get("telephone", "")
            except Exception:
                pass

        # ── Phone from body ───────────────────────────────────────────────────
        if not result["phone"]:
            pm = re.search(r"[\+]?[\(]?\d{3}[\)]?[\s\.\-]?\d{3,4}[\s\.\-]?\d{4}", body_text)
            if pm:
                result["phone"] = pm.group()

        # ── Industry detection (word-boundary regex, bilingual) ───────────────
        for industry_key, patterns in _INDUSTRY_PATTERNS:
            combined = "|".join(patterns)
            if re.search(combined, body_lower, re.I):
                result["industry_guess"] = industry_key
                break

        # ── Extract services from H2/H3 headings ─────────────────────────────
        # Use structural headings only (no li — too often nav items)
        _NAV_WORDS = {"inicio", "home", "about", "contact", "contacto", "conócenos",
                      "conocenos", "menu", "menú", "privacy", "services", "servicios",
                      "blog", "news", "noticias", "más", "mas", "info",
                      "aseguradoras", "insurance", "contáctanos", "contáctenos",
                      "gallery", "galería", "faq", "testimonials", "testimonios",
                      "pricing", "precios", "team", "equipo", "careers", "empleo"}
        page_services = []
        for tag in soup.find_all(["h2", "h3"]):
            text = tag.get_text(strip=True)
            # Service headings: meaningful phrases, not nav words, right length
            if 4 < len(text) < 70 and not text.endswith(("?", ".", "!")):
                if text.lower() not in _NAV_WORDS:
                    page_services.append(text)
        # Use page services if we found enough; else use industry defaults
        if len(page_services) >= 3:
            result["services"] = page_services[:8]
        else:
            result["services"] = _INDUSTRY_SERVICES.get(
                result["industry_guess"], []
            )[:6]

    except Exception:
        pass

    # ── Sitemap check ─────────────────────────────────────────────────────────
    try:
        clean_domain = domain.lstrip("https://").lstrip("http://").strip("/")
        sm = requests.get(
            f"https://{clean_domain}/sitemap.xml",
            timeout=4, headers={"User-Agent": "DenzoSEOBot/1.0"}
        )
        result["has_sitemap"] = sm.status_code < 400
    except Exception as e:
        logger.warning("sitemap check error: %s", e)

    industry = result["industry_guess"]
    city     = result["city"] or "your city"
    biz      = result["business_name"] or "your business"

    # ── Fill dont_sell from industry defaults ──────────────────────────────────
    result["dont_sell"]  = _INDUSTRY_DONT_SELL.get(industry, _INDUSTRY_DONT_SELL["general"])
    result["competitors"] = _INDUSTRY_COMPETITORS.get(industry, _INDUSTRY_COMPETITORS["general"])

    # ── Keywords ──────────────────────────────────────────────────────────────
    svc0 = result["services"][0].lower() if result["services"] else "service"
    kw_templates = _INDUSTRY_KEYWORDS.get(industry, _INDUSTRY_KEYWORDS["general"])
    result["keywords"] = [
        k.format(city=city, business=biz.lower(), service=svc0)
        for k in kw_templates
    ]

    # ── Article titles ────────────────────────────────────────────────────────
    svc0_title = result["services"][0] if result["services"] else "Service"
    art_templates = _ARTICLE_TEMPLATES.get(industry, _ARTICLE_TEMPLATES["general"])
    result["article_titles"] = [
        t.format(city=city, business=biz, service=svc0_title)
        for t in art_templates
    ]

    # ── Issues (comprehensive, bilingual labels) ──────────────────────────────
    issues = []

    if not result["reachable"]:
        issues.append({"sev": "critical", "text": "Website couldn't be reached — check the URL"})
        result["issues"] = issues
        result["score"] = 5
        return result

    # Technical
    if not result["is_https"]:
        issues.append({"sev": "critical", "text": "Site not on HTTPS — Google penalizes non-secure sites"})
    if result["noindex"]:
        issues.append({"sev": "critical", "text": "Noindex tag found — Google is actively blocked from indexing this page!"})
    if not result["has_viewport"]:
        issues.append({"sev": "critical", "text": "No mobile viewport meta tag — fails Google mobile-first indexing"})

    # On-page
    if not result["title"]:
        issues.append({"sev": "critical", "text": "No page title — your #1 ranking signal is missing"})
    elif len(result["title"]) > 65:
        issues.append({"sev": "warning", "text": f"Title too long ({len(result['title'])} chars) — gets cut off in Google results"})
    elif len(result["title"]) < 20:
        issues.append({"sev": "warning", "text": "Title too short — not using your full keyword space"})

    if not result["meta_description"]:
        issues.append({"sev": "critical", "text": "Missing meta description — Google writes its own (usually bad)"})
    elif len(result["meta_description"]) > 160:
        issues.append({"sev": "warning", "text": f"Meta description too long ({len(result['meta_description'])} chars) — truncated in SERP"})

    if result["h1_count"] == 0:
        issues.append({"sev": "critical", "text": "No H1 heading — primary keyword signal missing from your page"})
    elif result["h1_count"] > 1:
        issues.append({"sev": "warning", "text": f"{result['h1_count']} H1 tags found — should be exactly 1"})

    if result["h2_count"] < 3:
        issues.append({"sev": "warning", "text": f"Only {result['h2_count']} H2 headings — page lacks content structure"})

    # Content depth
    if result["word_count"] < 300:
        issues.append({"sev": "critical", "text": f"Homepage has only {result['word_count']} words — Google considers this thin content"})
    elif result["word_count"] < 600:
        issues.append({"sev": "warning", "text": f"Homepage has {result['word_count']} words — more content would improve rankings"})

    # Content strategy (the big ones)
    if not result["has_blog"]:
        issues.append({"sev": "critical", "text": "No blog or content section detected — you're missing 80% of organic traffic potential"})

    # Schema
    if not result["has_schema"]:
        issues.append({"sev": "warning", "text": "No Schema.org structured data — missing eligibility for rich results in Google"})

    # Technical SEO
    if not result["has_canonical"]:
        issues.append({"sev": "warning", "text": "No canonical tag — risk of duplicate content penalties"})

    if not result["has_og"]:
        issues.append({"sev": "warning", "text": "No Open Graph tags — links shared on social media won't preview correctly"})

    if result["images_missing_alt"] > 0:
        issues.append({"sev": "warning", "text": f"{result['images_missing_alt']} image{'s' if result['images_missing_alt']>1 else ''} missing alt text — hurts image SEO & accessibility"})

    if result["internal_links"] < 5:
        issues.append({"sev": "warning", "text": f"Only {result['internal_links']} internal links — poor link architecture limits page authority"})

    result["issues"] = issues

    # ── Score: starts at 100, deductions per issue ────────────────────────────
    # Starts lower for a homepage-only site (no content strategy = structural cap)
    base = 60 if not result["has_blog"] else 85

    penalties = {
        "critical": -12,
        "warning":  -5,
    }
    score = base
    for issue in issues:
        score += penalties.get(issue["sev"], 0)

    result["score"] = max(8, min(score, 95))
    return result


def _get_plan(user_id: int) -> str:
    db = get_db()
    row = db.execute("SELECT plan, trial_ends_at FROM users WHERE id=?", (user_id,)).fetchone()
    db.close()
    if not row:
        return "free"
    plan = row["plan"] or "free"
    if plan == "trial" and row["trial_ends_at"]:
        try:
            if datetime.utcnow() > datetime.fromisoformat(row["trial_ends_at"]):
                return "expired"
        except Exception as e:
            logger.warning("Error: %s", e)
    return plan


# ── Public landing ─────────────────────────────────────────────────────────────

@bp.route("/")
@bp.route("/landing")
@bp.route("/home")
def landing():
    return render_template("public/landing.html")


# ── Wizard: start ─────────────────────────────────────────────────────────────

@bp.route("/wizard/start", methods=["POST"])
def wizard_start():
    url_raw = request.form.get("url", "").strip()
    if not url_raw:
        flash("Please enter your website URL.", "error")
        return redirect(url_for("public.landing"))
    url = _normalize_url(url_raw)
    # SSRF guard — reject private/internal IPs
    try:
        import ipaddress, socket as _sock
        from urllib.parse import urlparse as _up
        _host = _up(url).hostname or ""
        _ip = _sock.gethostbyname(_host)
        _addr = ipaddress.ip_address(_ip)
        if _addr.is_private or _addr.is_loopback or _addr.is_link_local or _addr.is_reserved:
            flash("That URL is not allowed.", "error")
            return redirect(url_for("public.landing"))
    except Exception:
        pass
    # Run analysis and store in session — loading page is just cosmetic delay
    data = _analyze_website(url)
    session["wizard_analysis"] = data
    session["wizard_step"] = 1
    # Reset any previous step data
    for k in ["wizard_s1", "wizard_s2", "wizard_s3", "wizard_s4"]:
        session.pop(k, None)
    return redirect(url_for("public.wizard_analyzing"))


@bp.route("/wizard/analyzing")
def wizard_analyzing():
    if "wizard_analysis" not in session:
        return redirect(url_for("public.landing"))
    return render_template("public/wizard_analyzing.html",
                           domain=session["wizard_analysis"].get("domain", "your website"))


# ── Wizard: steps 1–4 ─────────────────────────────────────────────────────────

@bp.route("/wizard/step/<int:step>", methods=["GET", "POST"])
def wizard_step(step):
    if "wizard_analysis" not in session:
        return redirect(url_for("public.landing"))
    analysis = session["wizard_analysis"]

    if request.method == "POST":
        # Save step data
        if step == 1:
            session["wizard_s1"] = {
                "business_name": request.form.get("business_name", analysis["business_name"]),
                "city":          request.form.get("city", analysis["city"]),
                "state":         request.form.get("state", "CA"),
                "industry":      request.form.get("industry", analysis["industry_guess"]),
                "description":   request.form.get("description", analysis["description"]),
            }
        elif step == 2:
            services_raw  = request.form.getlist("services[]")
            dont_sell_raw = request.form.getlist("dont_sell[]")
            focus_service = request.form.get("focus_service", services_raw[0] if services_raw else "")
            session["wizard_s2"] = {
                "services":      services_raw or analysis["services"],
                "dont_sell":     dont_sell_raw or analysis["dont_sell"],
                "focus_service": focus_service,
            }
        elif step == 3:
            competitors_raw = request.form.getlist("competitors[]")
            session["wizard_s3"] = {
                "competitors": competitors_raw or analysis["competitors"],
            }
        elif step == 4:
            keywords_raw = request.form.getlist("keywords[]")
            titles_raw   = request.form.getlist("titles[]")
            session["wizard_s4"] = {
                "keywords": keywords_raw or analysis["keywords"],
                "titles":   titles_raw   or analysis["article_titles"],
            }

        next_step = step + 1
        if next_step > 5:
            return redirect(url_for("public.wizard_step", step=5))
        return redirect(url_for("public.wizard_step", step=next_step))

    # Build context for each step
    s1 = session.get("wizard_s1", {})
    s2 = session.get("wizard_s2", {})
    s3 = session.get("wizard_s3", {})
    s4 = session.get("wizard_s4", {})

    ctx = {
        "step":     step,
        "analysis": analysis,
        "s1": s1, "s2": s2, "s3": s3, "s4": s4,
        # Pre-fills using saved data or analysis defaults
        "business_name": s1.get("business_name", analysis["business_name"]),
        "city":          s1.get("city",          analysis["city"]),
        "state":         s1.get("state",         "CA"),
        "industry":      s1.get("industry",      analysis["industry_guess"]),
        "description":   s1.get("description",   analysis["description"]),
        "services":      s2.get("services",      analysis["services"]),
        "dont_sell":     s2.get("dont_sell",     analysis["dont_sell"]),
        "focus_service": s2.get("focus_service", analysis["services"][0] if analysis["services"] else ""),
        "competitors":   s3.get("competitors",   analysis["competitors"]),
        "keywords":      s4.get("keywords",      analysis["keywords"]),
        "titles":        s4.get("titles",        analysis["article_titles"]),
        "score":         analysis.get("score", 60),
        "issues":        analysis.get("issues", []),
    }
    return render_template("public/wizard.html", **ctx)


# ── Wizard: complete (step 5 = create account) ────────────────────────────────

@bp.route("/wizard/complete", methods=["POST"])
def wizard_complete():
    if "wizard_analysis" not in session:
        return redirect(url_for("public.landing"))

    analysis = session["wizard_analysis"]
    s1 = session.get("wizard_s1", {})
    s2 = session.get("wizard_s2", {})
    s3 = session.get("wizard_s3", {})
    s4 = session.get("wizard_s4", {})

    email    = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "").strip()

    if not email or "@" not in email:
        flash("Valid email required.", "error")
        return redirect(url_for("public.wizard_step", step=5))
    if len(password) < 8:
        flash("Password must be at least 8 characters.", "error")
        return redirect(url_for("public.wizard_step", step=5))

    db = get_db()
    existing = db.execute(
        "SELECT id FROM users WHERE username=? OR email=?", (email, email)
    ).fetchone()
    if existing:
        db.close()
        flash("An account with that email already exists. Sign in instead.", "error")
        return redirect(url_for("public.wizard_step", step=5))

    # Resolve merged data from all steps
    biz_name  = s1.get("business_name", analysis["business_name"]) or analysis["domain"]
    city      = s1.get("city",  analysis["city"])
    state     = s1.get("state", "CA")
    industry  = s1.get("industry", analysis["industry_guess"])
    website   = analysis["url"]
    services  = json.dumps(s2.get("services",  analysis["services"]))
    dont_sell = json.dumps(s2.get("dont_sell", analysis["dont_sell"]))

    try:
        # All inserts in a single transaction — rollback if anything fails
        db.execute(
            "INSERT INTO users (username, email, password_hash, role, plan) VALUES (?,?,?,?,?)",
            (email, email, generate_password_hash(password), "client", "free")
        )
        user_id = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"]

        base_slug = slugify(biz_name)
        tenant_id = base_slug
        suffix = 1
        while db.execute("SELECT id FROM clients WHERE tenant_id=?", (tenant_id,)).fetchone():
            tenant_id = f"{base_slug}-{suffix}"
            suffix += 1

        db.execute("""
            INSERT INTO clients
              (tenant_id, name, business_type, website_url, city, state,
               publisher_type, status, owner_user_id, created_at, updated_at)
            VALUES (?,?,?,?,?,?,'wordpress','active',?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
        """, (tenant_id, biz_name, industry, website, city, state, user_id))

        db.execute("""
            INSERT INTO client_context
              (tenant_id, domain, industry_vertical, service_cities, primary_city,
               certifications, services, differentiators, competitors,
               insurance_partners, dont_sell, description, tagline)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            tenant_id,
            urlparse(_normalize_url(website)).netloc if website else "",
            industry,
            json.dumps([city] if city else []),
            city,
            "[]",
            services,
            "[]",
            json.dumps(s3.get("competitors", analysis["competitors"])),
            "[]",
            dont_sell,
            s1.get("description", ""),
            s1.get("tagline", ""),
        ))

        for name in DEFAULT_AGENTS:
            _, _, layer, color = AGENT_REGISTRY[name]
            db.execute(
                "INSERT OR IGNORE INTO agents (tenant_id,name,layer,color,status) VALUES (?,?,?,?,'idle')",
                (tenant_id, name, layer, color)
            )

        db.commit()
    except Exception:
        db.rollback()
        db.close()
        flash("Account creation failed. Please try again.", "error")
        return redirect(url_for("public.wizard_step", step=1))
    db.close()

    # Log in
    session.clear()
    session["user_id"] = user_id
    session["username"] = email
    session["plan"] = "free"

    return redirect(url_for("public.upgrade_page"))


# ── Upgrade / trial ────────────────────────────────────────────────────────────

@bp.route("/upgrade")
@bp.route("/pricing")
def upgrade_page():
    # Public pricing page — works for both anonymous and logged-in users
    user_id   = session.get("user_id")
    plan      = _get_plan(user_id) if user_id else "free"
    tenant_id = None
    biz_name  = "your business"
    if user_id:
        db = get_db()
        client = db.execute(
            "SELECT tenant_id, name FROM clients WHERE owner_user_id=? LIMIT 1", (user_id,)
        ).fetchone()
        db.close()
        if client:
            tenant_id = client["tenant_id"]
            biz_name  = client["name"]
    return render_template("public/upgrade.html",
                           plan=plan, tenant_id=tenant_id, biz_name=biz_name)


@bp.route("/upgrade/activate", methods=["POST"])
@login_required
def activate_trial():
    user_id    = session["user_id"]
    trial_ends = datetime.utcnow() + timedelta(days=3)
    db = get_db()
    db.execute(
        "UPDATE users SET plan='trial', trial_ends_at=? WHERE id=?",
        (trial_ends.isoformat(), user_id)
    )
    db.commit()

    # Find their tenant to redirect
    client = db.execute(
        "SELECT tenant_id FROM clients WHERE owner_user_id=? LIMIT 1", (user_id,)
    ).fetchone()
    db.close()
    session["plan"] = "trial"

    tenant_id = client["tenant_id"] if client else None
    return jsonify({"ok": True, "plan": "trial",
                    "redirect": url_for("lite.dashboard", tenant_id=tenant_id) if tenant_id else "/app/"})


@bp.route("/upgrade/cancel", methods=["POST"])
@login_required
def cancel_plan():
    db = get_db()
    db.execute("UPDATE users SET plan='cancelled' WHERE id=?", (session["user_id"],))
    db.commit()
    db.close()
    session["plan"] = "cancelled"
    flash("Subscription cancelled.", "info")
    return redirect(url_for("public.upgrade_page"))


# ── Legacy /signup redirect to wizard ─────────────────────────────────────────
@bp.route("/signup")
def signup():
    return redirect(url_for("public.landing"))
