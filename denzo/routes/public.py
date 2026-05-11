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
    ("auto_dealership", [
        r"\b(dealership|car dealer|auto dealer|test drive|new and used|"
        r"used cars?|new cars?|certified pre[-\s]?owned|cpo vehicles?|"
        r"auto financing|car loan|lease deal[s]?|trade[-\s]?in value|"
        r"buy here pay here|concesionario|venta de autos|veh[íi]culos? usados?)\b"
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
    "medical_imaging": ["X-Ray", "Ultrasound", "Mammogram", "MRI",
                        "CT Scan", "Bone Density Scan"],
    "auto_body":       ["Collision Repair", "Auto Body Paint", "Frame Straightening",
                        "Paintless Dent Repair (PDR)", "Glass Replacement", "Bumper Repair"],
    "auto_dealership": ["New Vehicle Sales", "Used Vehicle Sales", "Certified Pre-Owned",
                        "Auto Financing", "Lease Specials", "Trade-In Appraisal",
                        "Service Center", "Parts Department"],
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
    "auto_body":       ["Routine Mechanical Maintenance", "Vehicle Sales", "Fuel & Tires"],
    "auto_dealership": ["Collision Repair", "Body Work", "Paint Jobs"],
    "dental":          ["Cosmetic Surgery", "Medical Prescriptions"],
    "legal":           ["Accounting", "Financial Advice"],
    "real_estate":     ["Property Insurance", "Mortgage Lending"],
    "restaurant":      ["Grocery Delivery", "Cooking Classes"],
    "general":         ["Wholesale", "Franchising"],
}

_INDUSTRY_COMPETITORS = {
    "auto_body":       ["Caliber Collision", "Service King", "Gerber Collision",
                        "Fix Auto", "CARSTAR"],
    "auto_dealership": ["AutoNation", "CarMax", "Carvana", "Vroom", "Local franchise dealers"],
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
    "auto_dealership": [
        "car dealership {city}",
        "used cars {city}",
        "new cars for sale {city}",
        "bad credit auto loans {city}",
        "lease deals {city}",
        "certified pre-owned {city}",
        "trade-in value {city}",
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
    "auto_dealership": [
        "How to Buy a Car With Bad Credit in {city}",
        "New vs Used vs Certified Pre-Owned: Which Is Right for {city} Buyers?",
        "Lease vs Buy: A {city} Driver's Complete Guide",
        "How Much Is My Trade-In Worth in {city}? A Step-by-Step Appraisal Guide",
        "First-Time Car Buyer's Guide for {city}: Financing, Insurance, and Documents",
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
        # Use stealth_fetch — it tries curl → requests → cloudscraper → playwright
        # in that order, transparently bypassing Cloudflare bot protection that
        # was blocking plain `requests` (the old code got 403 on most modern
        # dealership / agency / SaaS sites).
        from denzo.agents.utils.stealth_fetch import fetch_html
        fetched = fetch_html(url, timeout=30)
        if not fetched.get("ok") or not fetched.get("html"):
            raise RuntimeError(f"fetch failed via {fetched.get('method', '?')}: status={fetched.get('status')}")

        html_text = fetched["html"]
        result["reachable"] = True
        result["page_size_kb"] = round(len(html_text) / 1024, 1)
        soup = BeautifulSoup(html_text, "lxml")
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

        # Description priority: meta description → og:description → first
        # meaningful <p>. Avoid leaving the field blank — clients hate filling
        # this manually and Claude generates better content with seed text.
        result["description"] = result["meta_description"]
        if not result["description"]:
            og_desc = soup.find("meta", attrs={"property": "og:description"})
            if og_desc and og_desc.get("content", "").strip():
                result["description"] = og_desc["content"].strip()
        if not result["description"]:
            # Find first <p> with 60+ chars of meaningful text (skip cookie
            # banners, footers, nav menus)
            SKIP_PATTERNS = re.compile(r"(cookie|privacy|accept|menu|navigation|copyright|©)", re.I)
            for p in soup.find_all("p"):
                text = p.get_text(" ", strip=True)
                if 60 <= len(text) <= 400 and not SKIP_PATTERNS.search(text):
                    result["description"] = text
                    break

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

        # ── Location + phone + address from Schema.org JSON-LD ───────────────
        # JSON-LD is the most reliable source — well-tagged sites put their
        # full NAP (Name/Address/Phone) here. We walk all script blocks because
        # many sites put multiple @types and we want LocalBusiness/Org first.
        def _flatten_jsonld(blob):
            """Yield every dict node inside arbitrarily nested JSON-LD."""
            if isinstance(blob, list):
                for item in blob: yield from _flatten_jsonld(item)
            elif isinstance(blob, dict):
                yield blob
                for v in blob.values():
                    if isinstance(v, (list, dict)): yield from _flatten_jsonld(v)

        result["address"] = ""
        result["zip"] = ""
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            try:
                data = json.loads(script.string or "{}")
                for node in _flatten_jsonld(data):
                    addr = node.get("address") if isinstance(node.get("address"), dict) else {}
                    if isinstance(addr, dict):
                        street = (addr.get("streetAddress") or "").strip()
                        city   = (addr.get("addressLocality") or "").strip()
                        state  = (addr.get("addressRegion") or "").strip()
                        zip_c  = (addr.get("postalCode") or "").strip()
                        result["city"]  = result["city"]  or city
                        result["state"] = result["state"] or state
                        result["zip"]   = result["zip"]   or zip_c
                        if street and not result["address"]:
                            result["address"] = ", ".join(filter(None, [street, city, state, zip_c]))
                    tel = (node.get("telephone") or "").strip()
                    if tel and not result["phone"]:
                        result["phone"] = tel
            except Exception:
                pass

        # ── Phone from body — only if JSON-LD didn't have it ──────────────────
        # Stricter regex: must look like a real US/intl phone, not a spec table
        # entry like "0-60: 5.4" or "MPG: 28/35". Require at least one of:
        #   parentheses around area code, "tel:" prefix, or strict 3-3-4 with
        #   hyphen/space separators.
        if not result["phone"]:
            tel_href = soup.find("a", href=re.compile(r"^tel:"))
            if tel_href:
                result["phone"] = tel_href["href"].replace("tel:", "").strip()
        if not result["phone"]:
            patterns = [
                r"\(\d{3}\)\s*\d{3}[\-\.\s]\d{4}",       # (951) 234-5678
                r"\b\d{3}[\-\.]\d{3}[\-\.]\d{4}\b",      # 951-234-5678 / 951.234.5678
                r"\b1[\-\.]?\d{3}[\-\.]\d{3}[\-\.]\d{4}\b",  # 1-833-613-1189
                r"\b\d{1}[\-\.]\d{3}[\-\.]\d{3}[\-\.]\d{4}\b",  # 1-833-613-1189
            ]
            for pat in patterns:
                m = re.search(pat, body_text)
                if m:
                    result["phone"] = m.group().strip()
                    break

        # ── Address from body — fallback if JSON-LD missed it ─────────────────
        if not result["address"]:
            # "123 Main St, City, ST 12345" — strict enough to avoid garbage
            addr_re = re.compile(
                r"\b\d{2,6}\s+[A-Z][A-Za-z0-9\.\s\-]+?"
                r"(?:Street|St\.?|Avenue|Ave\.?|Road|Rd\.?|Boulevard|Blvd\.?|Drive|Dr\.?|"
                r"Way|Lane|Ln\.?|Parkway|Pkwy\.?|Court|Ct\.?|Plaza|Place|Pl\.?|Highway|Hwy\.?)"
                r"\s*,?\s*[A-Z][A-Za-z\s\.]+?,?\s+[A-Z]{2}\s+\d{5}(?:-\d{4})?\b"
            )
            m = addr_re.search(body_text)
            if m:
                result["address"] = m.group().strip()

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

    # ── Stale-analysis guard ──────────────────────────────────────────────────
    # If the session was created by an older build of the analyzer (before
    # phone/address/zip extraction was added), the saved dict won't have those
    # keys and the user sees pre-filled fields as empty inputs. Detect this
    # mismatch and transparently re-run the analyzer on the same URL.
    has_new_keys = "phone" in analysis and "address" in analysis and "zip" in analysis
    if not has_new_keys and analysis.get("url"):
        fresh = _analyze_website(analysis["url"])
        # Preserve any user edits saved in s1/s2/s3/s4
        session["wizard_analysis"] = fresh
        analysis = fresh

    if request.method == "POST":
        # Save step data
        if step == 1:
            session["wizard_s1"] = {
                "business_name": request.form.get("business_name", analysis["business_name"]),
                "city":          request.form.get("city", analysis["city"]),
                "state":         request.form.get("state", "CA"),
                "industry":      request.form.get("industry", analysis["industry_guess"]),
                "description":   request.form.get("description", analysis["description"]),
                "phone":         request.form.get("phone", analysis.get("phone", "")),
                "address":       request.form.get("address", analysis.get("address", "")),
                "zip":           request.form.get("zip", analysis.get("zip", "")),
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

    # Pre-fills: use saved user data UNLESS it's empty, in which case fall back
    # to the freshly-analyzed values. The `or` trick handles the common case
    # where a previous step submission stored an empty list (e.g. user clicked
    # Continue too fast on Services and we saved []), without losing legitimate
    # user edits where they typed something.
    ctx = {
        "step":     step,
        "analysis": analysis,
        "s1": s1, "s2": s2, "s3": s3, "s4": s4,

        # Strings — keep user input even if it's blank (they may have deleted on purpose)
        "business_name": s1.get("business_name") or analysis["business_name"],
        "city":          s1.get("city")          or analysis["city"],
        "state":         s1.get("state")         or analysis.get("state") or "CA",
        "industry":      s1.get("industry")      or analysis["industry_guess"],
        "description":   s1.get("description")   or analysis["description"],
        "phone":         s1.get("phone")         or analysis.get("phone", ""),
        "address":       s1.get("address")       or analysis.get("address", ""),
        "zip":           s1.get("zip")           or analysis.get("zip", ""),

        # Lists — empty list means "nothing saved yet", fall back to analysis
        "services":      s2.get("services")      or analysis["services"],
        "dont_sell":     s2.get("dont_sell")     or analysis["dont_sell"],
        "focus_service": s2.get("focus_service") or (analysis["services"][0] if analysis["services"] else ""),
        "competitors":   s3.get("competitors")   or analysis["competitors"],
        "keywords":      s4.get("keywords")      or analysis["keywords"],
        "titles":        s4.get("titles")        or analysis["article_titles"],

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
              (tenant_id, name, business_type, website_url,
               phone, address, city, state,
               publisher_type, status, owner_user_id, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,'wordpress','active',?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
        """, (
            tenant_id, biz_name, industry, website,
            s1.get("phone", ""), s1.get("address", ""),
            city, state, user_id,
        ))

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
    from denzo.billing.plans import all_priced_plans, stripe_configured, get_plan
    return render_template("public/pricing.html",
                           plan=plan, tenant_id=tenant_id, biz_name=biz_name,
                           current_plan_obj=get_plan(plan),
                           plans=all_priced_plans(),
                           stripe_configured=stripe_configured(),
                           legacy_template_url=url_for("public.upgrade_page_legacy"))


# ── Legal pages ────────────────────────────────────────────────────────────────

@bp.route("/privacy")
def privacy():
    from datetime import datetime
    today = datetime.utcnow().strftime("%B %d, %Y")
    return render_template(
        "public/privacy.html",
        page_title="Privacy Policy",
        page_description="How Denzo SEO handles your data and your Google account information.",
        effective_date=today,
        year=datetime.utcnow().year,
    )


@bp.route("/terms")
def terms():
    from datetime import datetime
    today = datetime.utcnow().strftime("%B %d, %Y")
    return render_template(
        "public/terms.html",
        page_title="Terms of Service",
        page_description="The terms governing your use of Denzo SEO.",
        effective_date=today,
        year=datetime.utcnow().year,
    )


@bp.route("/upgrade-legacy")
def upgrade_page_legacy():
    """Original 10-agent upgrade page kept as a fallback during transition."""
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
