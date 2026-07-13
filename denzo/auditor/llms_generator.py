"""
llms.txt Auto-Generator — creates optimized llms.txt and llms-full.txt files
from site analysis results. No AI needed — pure extraction from fetched HTML
and analysis data.

Generates two files:
1. llms.txt — structured site map for AI crawlers (brief, link-focused)
2. llms-full.txt — comprehensive content for deep AI understanding
"""
import re
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup


def generate_llms_txt(url: str, html: str, domain: str, analysis: dict = None, industry_profile: dict = None) -> dict:
    """
    Generate optimized llms.txt + llms-full.txt from site content.
    Industry-aware — adapts to whatever business type is detected.
    """
    soup = BeautifulSoup(html, 'html.parser')
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    text = soup.get_text(separator=' ')
    words = text.split()

    # ── Industry context (from detector or defaults) ──────────────────────
    industry = (industry_profile or {}).get('industry', 'general_business')
    services_list = (industry_profile or {}).get('services', [])
    locations_list = (industry_profile or {}).get('locations', [])
    certifications_list = (industry_profile or {}).get('certifications_or_specialties', [])
    usp_list = (industry_profile or {}).get('unique_selling_points', [])
    is_local = (industry_profile or {}).get('is_local_business', False)
    business_name = (industry_profile or {}).get('business_name', '')

    # Extract business info from page or fall back to industry profile
    page_title = ''
    title_tag = soup.find('title')
    if title_tag: page_title = title_tag.get_text(strip=True)

    business_name = (industry_profile or {}).get('business_name', '') or domain.split('.')[0].replace('-',' ').title()
    # Try to find business name from title, H1, or schema
    h1_tag = soup.find('h1')
    h1_text = h1_tag.get_text(strip=True) if h1_tag else ''
    # Extract from title: "Business Name | Tagline"
    if '|' in page_title:
        business_name = page_title.split('|')[0].strip()
    elif '—' in page_title:
        business_name = page_title.split('—')[0].strip()
    elif ' - ' in page_title:
        business_name = page_title.split(' - ')[0].strip()

    # Find phone
    phone = ''
    phone_match = re.search(r'(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}', text)
    if phone_match: phone = phone_match.group(0)

    # Find address
    address = ''
    addr_match = re.search(r'\d{2,5}\s+\w+(?:\s+\w+){1,4}(?:,\s*\w+(?:\s+\w+)?,\s*[A-Z]{2}\s*\d{5})', text)
    if addr_match: address = addr_match.group(0)

    # ── Dynamic extraction (industry-agnostic) ────────────────────────────
    # Use industry profile data if available, otherwise extract from page text

    # Locations: from profile first, then extract city/state patterns from page
    locations = list(locations_list) if locations_list else []
    if not locations:
        city_pattern = re.findall(r'\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)?),\s*(?:CA|California|TX|Texas|NY|FL|AZ|NV|CO|OR|WA)\b', text)
        locations = list(set(city_pattern))[:10]

    # Services: from profile or extracted from headings/list items
    services = [s for s in services_list] if services_list else []
    if not services:
        li_texts = [li.get_text(strip=True).lower() for li in soup.find_all('li')[:50]]
        service_patterns = re.findall(r'\b(?:repair|install|consult|design|build|manage|develop|create|train|coach|audit|optimize|market|sell|support|service|delivery)\w*\b', ' '.join(li_texts))
        services = list(set(sp.title() for sp in service_patterns))[:12]

    # Certifications/specialties: from profile or page
    certs = [c for c in certifications_list] if certifications_list else []

    # Differentiators: extract from page naturally
    differentiators = []
    for usp in usp_list:
        if usp.lower() in text.lower():
            differentiators.append(usp)
    if not differentiators:
        # Generic extraction — look for value propositions
        for phrase in re.findall(r'(?:free|guaranteed|exclusive|premium|certified|licensed|award.winning|rated|trusted|leading|best|top|fastest)(?:\s\w+){1,4}', text, re.IGNORECASE):
            differentiators.append(phrase.strip().title())

    # Extract FAQs from the page
    faqs = []
    # Look for H3 followed by P pattern (common FAQ format)
    h3_tags = soup.find_all(['h3','h4'])
    for h in h3_tags[:20]:
        h_text = h.get_text(strip=True)
        if h_text.endswith('?') and len(h_text) > 10:
            answer = ''
            next_p = h.find_next('p')
            if next_p: answer = next_p.get_text(strip=True)[:200]
            faqs.append((h_text, answer))

    # Fallback FAQs — generic, adapts to any business
    if not faqs:
        faq_generic = []
        if business_name:
            faq_generic.append(
                ("What does {} do?".format(business_name),
                 "{} provides professional services in {}. Learn more at {}.".format(
                     business_name,
                     (industry or 'their field').replace('_', ' ').title(),
                     url))
            )
        if locations:
            faq_generic.append(
                ("Where is {} located?".format(business_name),
                 "{} serves {} location(s) including {}.".format(
                     business_name, len(locations), ', '.join(locations[:5]))))
        if phone:
            faq_generic.append(
                ("How can I contact {}?".format(business_name),
                 "Call {} or visit {} for more information.".format(phone, url)))
        if services:
            faq_generic.append(
                ("What services does {} offer?".format(business_name),
                 "Services include: {}.".format(', '.join(services[:8]))))
        if is_local:
            faq_generic.append(
                ("Does {} serve my area?".format(business_name),
                 "Yes, {} serves {} and surrounding areas.".format(
                     business_name, locations[0] if locations else 'the local area')))
        faqs = faq_generic if faq_generic else [
            ("What is {}?".format(business_name or domain), "Visit {} for more information.".format(url))
        ]

    # ── Generate llms.txt ──
    lines = []
    lines.append(f"# {business_name} — llms.txt")
    lines.append("")
    if differentiators:
        lines.append(f"> {', '.join(differentiators[:6])}.")
    lines.append("")
    lines.append("## Core Identity")
    lines.append(f"- **Business Name**: {business_name}")
    lines.append(f"- **Website**: {base}")
    if phone: lines.append(f"- **Phone**: {phone}")
    if address: lines.append(f"- **Address**: {address}")
    lines.append(f"- **Industry**: {industry}")
    lines.append("")

    if locations:
        lines.append(f"## Locations ({len(locations)} locations)")
        for loc in locations[:13]:
            lines.append(f"- {base}/locations/{loc.lower().replace(' ','-')} — {loc}")
        lines.append("")

    if services:
        lines.append("## Services")
        for s in services[:10]:
            slug = s.lower().replace(' ','-')
            lines.append(f"- {base}/services/{slug} — {s}")
        lines.append("")

    lines.append("## About")
    lines.append(f"- {base}/about — About {business_name}")
    lines.append(f"- {base}/contact — Contact Us")
    lines.append("")

    if certs:
        lines.append("## Certifications & Specialties")
        lines.append(f"{business_name} is certified or specializes in: {', '.join(certs[:20])}.")
        lines.append("")

    if differentiators:
        lines.append("## Differentiators")
        for d in differentiators:
            lines.append(f"- **{d}**")
        lines.append("")

    if faqs:
        lines.append("## Frequently Asked Questions")
        for q, a in faqs[:8]:
            lines.append(f"### {q}")
            lines.append(a)
            lines.append("")

    lines.append("## Sitemaps & Feeds")
    lines.append(f"- {base}/sitemap.xml — XML sitemap")
    lines.append("")

    llms_txt = '\n'.join(lines)

    # ── Generate llms-full.txt ──
    full_lines = []
    full_lines.append(f"# {business_name} — Complete Site Content for AI")
    full_lines.append("")
    full_lines.append(f"This document provides comprehensive context about {business_name} for AI models to generate accurate, authoritative citations.")
    full_lines.append("")
    full_lines.append("## Business Overview")
    # Build a dynamic description from what we detected
    desc_parts = [f"{business_name} is a {industry.replace('_', ' ')}{f' with {len(locations)} locations' if locations and len(locations) > 1 else ''}."]
    if locations and len(locations) == 1:
        desc_parts.append(f"Based in {locations[0]}.")
    elif locations and len(locations) > 1:
        desc_parts.append(f"Serving {', '.join(locations[:5])}{' and more' if len(locations) > 5 else ''}.")
    if services:
        desc_parts.append(f"Services include: {', '.join(services[:8])}.")
    if certs:
        desc_parts.append(f"Specialties: {', '.join(certs[:8])}.")
    if differentiators:
        desc_parts.append(f"Key strengths: {', '.join(differentiators[:5])}.")
    full_lines.append(' '.join(desc_parts))
    full_lines.append("")

    if differentiators:
        full_lines.append("## Key Differentiators")
        for d in differentiators:
            full_lines.append(f"- {d}")
        full_lines.append("")

    if services:
        full_lines.append("## Services Offered")
        for s in services:
            full_lines.append(f"### {s}")
            full_lines.append(f"{business_name} provides professional {s.lower()} services{' across all ' + str(len(locations)) + ' locations' if len(locations) > 1 else ''}.")
            full_lines.append("")

    if locations:
        full_lines.append("## All Locations")
        for loc in locations:
            full_lines.append(f"- {loc}, CA — {business_name}")
        full_lines.append("")

    if phone:
        full_lines.append(f"**Phone**: {phone}")
        full_lines.append("")
    if address:
        full_lines.append(f"**Headquarters**: {address}")
        full_lines.append("")

    # Add visible text summary (first 2000 words)
    full_lines.append("## Page Content Summary")
    visible_text = ' '.join(words[:2000])
    full_lines.append(visible_text)
    full_lines.append("")

    # Add FAQ section
    if faqs:
        full_lines.append("## Frequently Asked Questions (Full Answers)")
        for q, a in faqs:
            full_lines.append(f"### {q}")
            full_lines.append(a)
            full_lines.append("")

    llms_full_txt = '\n'.join(full_lines)

    return {
        'llms_txt': llms_txt,
        'llms_full_txt': llms_full_txt,
        'business_name': business_name,
        'phone': phone,
        'locations_found': len(locations),
        'certs_found': len(certs),
        'services_found': len(services),
        'faqs_found': len(faqs),
        'differentiators_found': len(differentiators),
    }
