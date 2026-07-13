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
            "severity": "low",
            "module": "llms",
            "title": "llms.txt returns an error (500/404) — optional, emerging standard",
            "detail": "llms.txt is a proposed convention for guiding AI crawlers. It is NOT yet used as a ranking or crawl directive by Google, OpenAI or Anthropic, so a missing/erroring file does not hurt SEO today. Adding it is low-cost future-proofing.",
            "fix": "Optional: create /public/llms.txt (Next.js) or at webroot with identity, key pages, services, FAQs and sitemap references."
        })
        score -= 8
    else:
        findings.append({
            "severity": "low",
            "module": "llms",
            "title": "No llms.txt found — optional, emerging standard",
            "detail": "llms.txt is a proposed convention (like robots.txt but for LLMs). Adoption is early and it is NOT currently a confirmed ranking or crawl signal for Google, OpenAI or Anthropic — its absence does not hurt SEO today. It is low-cost future-proofing worth adding.",
            "fix": "Optional: create /public/llms.txt (Next.js) or at webroot. Example: seo.tamgo.ai/static/llms-acg-example.txt"
        })
        score -= 8

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
            "detail": "Optional companion file that gives AI models comprehensive context. Not a ranking signal; nice-to-have.",
            "fix": "Optional: create /public/llms-full.txt with full page content in markdown format."
        })
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
