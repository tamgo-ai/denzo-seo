"""
Robots.txt Analyzer — fetches and analyzes robots.txt directives.
Checks AI crawler accessibility, sitemap references, and crawl rules.
"""
import re
from urllib.parse import urljoin, urlparse
from denzo.auditor.safe_fetch import fetch_html


# AI crawlers to check for
AI_CRAWLERS = {
    'GPTBot': 'OpenAI / ChatGPT',
    'CCBot': 'Common Crawl (LLM training data)',
    'Claude-Web': 'Anthropic Claude',
    'anthropic-ai': 'Anthropic Claude (alt)',
    'PerplexityBot': 'Perplexity AI',
    'Google-Extended': 'Google model training controls (not Search indexing)',
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
        # Raw plain-text — never Jina, which rewraps it in <pre> HTML.
        res = fetch_html(robots_url, allow_jina=False)
        robots_text = res.get('html', '') if res and res.get('ok') else None
    except Exception:
        return {"score": None, "status": "unavailable", "findings": [], "error": "Could not verify robots.txt"}

    # Defense-in-depth: strip any HTML wrapping (e.g. a proxy/renderer wrapping
    # the plain text in <pre>…</pre>) so a Sitemap: URL is never corrupted.
    if robots_text:
        robots_text = re.sub(r'<[^>]+>', '', robots_text)

    if not robots_text:
        return {"score": 100, "findings": [{"severity": "info", "title": "No robots.txt directives found",
                "detail": "A robots.txt file is optional. Its absence does not by itself prevent indexing.", "fix": None}],
                "robots_url": robots_url, "sitemap_refs": [], "ai_crawlers_blocked": [], "ai_crawlers_allowed": [], "total_rules": 0}

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
        # Informational crawl configuration; no unsupported search penalty.

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

    if ai_blocked:
        findings.append({"severity": "info", "module": "robots", "title": f"AI crawler policy: {len(ai_blocked)} restricted",
                         "detail": "These directives may intentionally restrict AI training or automated access. They are not evidence of a Google Search ranking problem.",
                         "fix": "Keep or change these settings according to the website owner's policy."})

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

