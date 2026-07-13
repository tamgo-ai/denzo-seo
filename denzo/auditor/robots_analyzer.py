"""
Robots.txt Analyzer — fetches and analyzes robots.txt directives.
Checks AI crawler accessibility, sitemap references, and crawl rules.
"""
import re
from urllib.parse import urljoin, urlparse
from denzo.agents.utils.stealth_fetch import fetch_html


# AI crawlers to check for
AI_CRAWLERS = {
    'GPTBot': 'OpenAI / ChatGPT',
    'CCBot': 'Common Crawl (LLM training data)',
    'Claude-Web': 'Anthropic Claude',
    'anthropic-ai': 'Anthropic Claude (alt)',
    'PerplexityBot': 'Perplexity AI',
    'Google-Extended': 'Google Gemini / AI Overviews',
    'GoogleOther': 'Google research crawler',
    'cohere-ai': 'Cohere AI',
    'meta-externalagent': 'Meta AI',
    'Bytespider': 'ByteDance / TikTok AI',
    'omgili': 'Webz.io (LLM data)',
    'Diffbot': 'Diffbot knowledge graph',
    'Applebot-Extended': 'Apple Intelligence',
}


def analyze_robots(url: str, html: str, domain: str) -> dict:
    """Fetch and analyze robots.txt. Returns structured findings."""
    findings = []
    score = 100
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    robots_url = f"{base}/robots.txt"

    # 1. Fetch robots.txt
    robots_text = None
    try:
        res = fetch_html(robots_url)
        robots_text = res.get('html', '') if res and res.get('ok') else None
    except Exception:
        robots_text = None

    if not robots_text:
        findings.append({
            "severity": "high",
            "module": "robots",
            "title": "No robots.txt found or inaccessible",
            "detail": f"Could not fetch {robots_url}. Search engines will crawl with no restrictions, which is generally fine but lacks crawl directives.",
            "fix": "Create a robots.txt at the site root. At minimum, include a sitemap reference."
        })
        score -= 25
        return {"score": max(0, score), "findings": findings, "robots_url": robots_url, "sitemap_refs": [],
                "ai_crawlers_blocked": [], "ai_crawlers_allowed": [], "total_rules": 0}

    # 2. Parse directives
    lines = robots_text.strip().split('\n')
    sitemap_refs = []
    current_ua = None
    rules_by_ua = {}
    total_rules = 0

    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue

        if ':' in line:
            key, _, value = line.partition(':')
            key = key.strip().lower()
            value = value.strip()
            value = value.split('#')[0].strip()

            if key == 'user-agent':
                current_ua = value.lower()
                if current_ua not in rules_by_ua:
                    rules_by_ua[current_ua] = {'allow': [], 'disallow': []}
            elif key == 'sitemap':
                sitemap_refs.append(value)
            elif key in ('disallow', 'allow') and current_ua:
                rules_by_ua[current_ua][key].append(value)
                total_rules += 1
            elif key == 'crawl-delay':
                pass  # informational

    # 3. Analyze
    if not sitemap_refs:
        findings.append({
            "severity": "medium",
            "module": "robots",
            "title": "No sitemap reference in robots.txt",
            "detail": "Adding a sitemap directive helps search engines discover your sitemap quickly.",
            "fix": "Add: Sitemap: https://" + domain + "/sitemap.xml"
        })
        score -= 10

    # Check if root is blocked
    all_bots = rules_by_ua.get('*', {'disallow': []})
    if '/' in all_bots['disallow']:
        findings.append({
            "severity": "critical",
            "module": "robots",
            "title": "Entire site is blocked for all crawlers",
            "detail": "Disallow: / is set for User-agent: *. Search engines cannot crawl the site at all.",
            "fix": "Remove 'Disallow: /' from the * user-agent block immediately."
        })
        score -= 50

    # AI crawler accessibility
    ai_blocked = []
    ai_allowed = []
    for ua_key, ua_name in AI_CRAWLERS.items():
        ua_lower = ua_key.lower()
        # Check if explicitly blocked
        blocked = False
        for rule_ua, rules in rules_by_ua.items():
            if ua_lower in rule_ua or rule_ua in ua_lower:
                if '/' in rules['disallow'] or '/*' in rules['disallow']:
                    blocked = True
                break

        # Also check wildcard
        if not blocked and '*' in rules_by_ua:
            wildcard = rules_by_ua['*']
            if '/' in wildcard['disallow'] or '/*' in wildcard['disallow']:
                blocked = True

        if blocked:
            ai_blocked.append(ua_key)
        else:
            ai_allowed.append(ua_key)

    if len(ai_blocked) >= 8:
        findings.append({
            "severity": "high",
            "module": "robots",
            "title": f"AI crawlers heavily restricted: {len(ai_blocked)} blocked",
            "detail": f"Blocked: {', '.join(ai_blocked[:6])}... This prevents LLMs from discovering and citing your content.",
            "fix": "Consider allowing AI crawlers to improve AI visibility and citation rates."
        })
        score -= 20
    elif len(ai_blocked) >= 3:
        findings.append({
            "severity": "medium",
            "module": "robots",
            "title": f"{len(ai_blocked)} AI crawlers blocked",
            "detail": f"Blocked: {', '.join(ai_blocked)}. Some LLMs cannot access your content.",
            "fix": "Review if intentionally blocking these AI crawlers. For GEO visibility, allowing them is beneficial."
        })
        score -= 10

    if len(ai_allowed) >= 8:
        findings.insert(0, {
            "severity": "pass",
            "module": "robots",
            "title": f"Excellent AI crawler accessibility: {len(ai_allowed)} AI crawlers allowed",
            "detail": "Your content can be discovered and cited by major AI platforms.",
            "fix": None
        })

    if total_rules == 0 and sitemap_refs:
        findings.insert(0, {
            "severity": "pass",
            "module": "robots",
            "title": "Minimal clean robots.txt with sitemap reference",
            "detail": f"Sitemap: {sitemap_refs[0]}. Unrestricted crawling with sitemap discovery is optimal.",
            "fix": None
        })

    return {
        "score": max(0, score),
        "findings": findings,
        "robots_url": robots_url,
        "sitemap_refs": sitemap_refs,
        "ai_crawlers_blocked": ai_blocked,
        "ai_crawlers_allowed": ai_allowed,
        "total_rules": total_rules,
        "has_wildcard": '*' in rules_by_ua,
    }
