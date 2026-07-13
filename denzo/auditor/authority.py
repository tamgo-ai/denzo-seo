"""
Authority Analyzer — off-page / backlink authority signals.

SEO ranking is not only on-page. Domain authority, referring domains and the
backlink profile are among the strongest ranking factors, and the previous
auditor ignored them entirely. This module adds that pillar.

It is OPT-IN: it activates only when an Ahrefs API key is available via the
AHREFS_API_KEY environment variable. Without a key it returns a single
informational finding (and contributes NO score penalty), so audits keep
working unchanged for users who have not connected Ahrefs yet.

Wire-up notes for full activation:
  - Set AHREFS_API_KEY in the environment (.env).
  - Ahrefs API v3 Site Explorer endpoints are used (domain-rating, refdomains).
  - This module is intentionally NOT part of MODULE_WEIGHTS so a missing key
    never distorts the overall score. Promote it to a weighted module once the
    key is provisioned in production.
"""
import os
import datetime
from urllib.parse import urlparse

AHREFS_BASE = "https://api.ahrefs.com/v3"


def _today():
    return datetime.date.today().isoformat()


def _ahrefs_get(path: str, params: dict, api_key: str, timeout: int = 20):
    import requests
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    r = requests.get(f"{AHREFS_BASE}{path}", params=params, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json()


def analyze_authority(url: str, html: str, domain: str, api_key: str = None) -> dict:
    """Fetch off-page authority metrics. Returns findings (non-scored by default)."""
    findings = []
    api_key = api_key or os.getenv("AHREFS_API_KEY")

    if not api_key:
        findings.append({
            "severity": "info", "module": "authority",
            "title": "Off-page authority not measured — connect Ahrefs to enable",
            "detail": "Domain Rating, referring domains and the backlink profile are among the strongest ranking factors but require an external data source. Set AHREFS_API_KEY to activate backlink/authority analysis (Domain Rating, referring domains, broken backlinks, anchor profile).",
            "fix": "Add AHREFS_API_KEY to your environment. The auditor will then report Domain Rating, referring-domain growth and toxic/broken backlinks.",
        })
        return {"score": None, "findings": findings, "enabled": False}

    metrics = {}
    try:
        dr = _ahrefs_get("/site-explorer/domain-rating",
                         {"target": domain, "date": _today()}, api_key)
        # Response shape is defensive-parsed (wrapper keys vary by plan)
        dr_val = None
        if isinstance(dr, dict):
            dr_val = (dr.get("domain_rating", {}) or {}).get("domain_rating") \
                     if isinstance(dr.get("domain_rating"), dict) else dr.get("domain_rating")
        metrics["domain_rating"] = dr_val
    except Exception as e:
        findings.append({
            "severity": "info", "module": "authority",
            "title": "Ahrefs Domain Rating lookup failed",
            "detail": f"Could not retrieve Domain Rating: {str(e)[:160]}",
            "fix": "Verify AHREFS_API_KEY validity and API plan access to Site Explorer.",
        })

    try:
        rd = _ahrefs_get("/site-explorer/refdomains",
                         {"target": domain, "mode": "subdomains",
                          "limit": 1, "date": _today()}, api_key)
        rd_count = None
        if isinstance(rd, dict):
            rd_count = rd.get("total") or (rd.get("refdomains", {}) or {}).get("total")
        metrics["referring_domains"] = rd_count
    except Exception:
        pass

    dr_val = metrics.get("domain_rating")
    if isinstance(dr_val, (int, float)):
        if dr_val < 10:
            findings.append({
                "severity": "high", "module": "authority",
                "title": f"Very low Domain Rating: {dr_val}/100 — weak backlink authority",
                "detail": "A Domain Rating under ~10 means the site has almost no earned link authority. On-page perfection cannot overcome an absent backlink profile for competitive terms.",
                "fix": "Launch a link-earning program: digital PR, quality directory + citation building (for local), guest content, and genuinely linkable assets (data studies, tools, guides).",
                "impact": "Low authority caps rankings for mid/high-difficulty keywords regardless of on-page quality.",
            })
        elif dr_val < 30:
            findings.append({
                "severity": "medium", "module": "authority",
                "title": f"Below-average Domain Rating: {dr_val}/100",
                "detail": "Authority is developing but still below the level needed for competitive terms.",
                "fix": "Sustain referring-domain growth from relevant, authoritative sites. Prioritise quality over quantity.",
            })
        else:
            findings.append({
                "severity": "pass", "module": "authority",
                "title": f"Domain Rating: {dr_val}/100",
                "detail": "Solid earned authority. Keep the referring-domain trend positive.",
                "fix": None,
            })

    rd_count = metrics.get("referring_domains")
    if isinstance(rd_count, int):
        findings.append({
            "severity": "info", "module": "authority",
            "title": f"Referring domains: {rd_count:,}",
            "detail": "Number of unique domains linking to the site (Ahrefs). Growth trend matters more than the absolute number.",
            "fix": None,
        })

    return {"score": None, "findings": findings, "enabled": True, "metrics": metrics}
