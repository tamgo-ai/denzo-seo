"""
llms.txt Analyzer — checks AI crawler accessibility, llms.txt completeness,
and content readiness for LLM citation.
"""
import re
from urllib.parse import urljoin, urlparse
from denzo.agents.utils.stealth_fetch import fetch_html


def analyze_llms(url: str, html: str, domain: str) -> dict:
    """Check llms.txt, llms-full.txt, and AI crawler readiness."""
    findings = []
    score = 100
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    llms_url = f"{base}/llms.txt"
    llms_full_url = f"{base}/llms-full.txt"

    # 1. Check llms.txt
    llms_text = None
    llms_status = None
    try:
        res = fetch_html(llms_url)
        llms_text = res.get('html', '') if res and res.get('ok') else None
        if llms_text and len(llms_text) > 50:
            llms_status = 'present'
        else:
            llms_status = 'empty'
    except Exception:
        llms_status = 'error'

    # 2. Check llms-full.txt
    llms_full_text = None
    llms_full_status = None
    try:
        res = fetch_html(llms_full_url)
        llms_full_text = res.get('html', '') if res and res.get('ok') else None
        if llms_full_text and len(llms_full_text) > 200:
            llms_full_status = 'present'
        else:
            llms_full_status = 'empty'
    except Exception:
        llms_full_status = 'error'

    # 3. Analyze llms.txt content
    if llms_status == 'present':
        sections = re.findall(r'^#{1,4}\s+(.+)$', llms_text, re.MULTILINE)
        link_count = len(re.findall(r'^-\s+\[.+\]\(https?://', llms_text, re.MULTILINE))
        link_count += len(re.findall(r'^-\s+https?://', llms_text, re.MULTILINE))

        findings.insert(0, {
            "severity": "pass",
            "module": "llms",
            "title": f"llms.txt found with {len(sections)} sections and ~{link_count} links",
            "detail": f"URL: {llms_url}. This helps AI platforms understand your site structure.",
            "fix": None
        })

        if len(sections) < 3:
            findings.append({
                "severity": "medium",
                "module": "llms",
                "title": "llms.txt is thin — only " + str(len(sections)) + " sections",
                "detail": "A comprehensive llms.txt should include: site identity, key pages, services, FAQs, and differentiators.",
                "fix": "Expand llms.txt with sections for Core Identity, Key Pages, Services, FAQs, and sitemap references."
            })
            score -= 5

        if link_count < 10:
            findings.append({
                "severity": "medium",
                "module": "llms",
                "title": f"llms.txt has few links ({link_count})",
                "detail": "AI models use these links to discover content. More links = better coverage.",
                "fix": "Add links to all key pages, location pages, service pages, and blog posts."
            })
            score -= 5
    elif llms_status == 'error':
        findings.append({
            "severity": "critical",
            "module": "llms",
            "title": "llms.txt returns an error (500/404/connection refused)",
            "detail": "ChatGPT, Claude, Perplexity, and Gemini use llms.txt to discover site structure. An error blocks ALL AI crawler discovery.",
            "fix": "Create /public/llms.txt (Next.js) or add it at the webroot. Include: identity, key pages, services, FAQs, and sitemap references."
        })
        score -= 40
    else:
        findings.append({
            "severity": "critical",
            "module": "llms",
            "title": "No llms.txt found",
            "detail": "AI platforms use llms.txt (like robots.txt but for LLMs) to understand site structure. Without it, AI discovery relies on web crawl alone.",
            "fix": "Create /public/llms.txt (Next.js) or at webroot. Example: seo.tamgo.ai/static/llms-acg-example.txt"
        })
        score -= 35

    # 4. Analyze llms-full.txt
    if llms_full_status == 'present':
        word_count = len(llms_full_text.split())
        findings.append({
            "severity": "pass" if word_count > 1000 else "medium",
            "module": "llms",
            "title": f"llms-full.txt found ({word_count:,} words)",
            "detail": "Comprehensive content for deep AI understanding. Good for citation accuracy.",
            "fix": None if word_count > 1000 else "Expand to 2,000+ words for best AI citation depth."
        })
        if word_count < 1000:
            score -= 5
    elif llms_full_status == 'error':
        findings.append({
            "severity": "medium",
            "module": "llms",
            "title": "llms-full.txt returns an error",
            "detail": "While optional, this file gives AI models comprehensive context for accurate citations.",
            "fix": "Create /public/llms-full.txt with full page content in markdown format."
        })
        score -= 10
    # llms-full.txt missing entirely = medium, not critical

    # 5. Check HTML for AI crawler hints
    llms_link = re.search(r'<link[^>]*rel=["\']llms\.txt["\']', html, re.IGNORECASE)
    if not llms_link and llms_status == 'present':
        findings.append({
            "severity": "low",
            "module": "llms",
            "title": "No <link rel='llms.txt'> in HTML head",
            "detail": "Adding this helps AI crawlers discover your llms.txt even without trying the default path.",
            "fix": "Add: <link rel='llms.txt' href='/llms.txt'> in <head>."
        })

    return {
        "score": max(0, score),
        "findings": findings,
        "llms_url": llms_url,
        "llms_status": llms_status,
        "llms_full_status": llms_full_status,
        "llms_full_url": llms_full_url,
    }
