"""
GEO / AI Visibility Analyzer v2 — deep analysis of AI citation readiness.
Checks FAQ content, structured data quality, definition blocks, entity signals,
E-E-A-T indicators, semantic HTML5, citation formatting, freshness signals.
"""
import re
from datetime import datetime
from bs4 import BeautifulSoup


def _generate_faq_examples(business_name, industry, services, faq_topics, locations, certs):
    """Generate industry-relevant FAQ examples."""
    industry_name = industry.replace('_', ' ').title()

    base_faqs = [
        f"What services does {business_name} offer?",
        f"How can I contact {business_name}?",
    ]
    if services:
        base_faqs.insert(0, f"How much does {' / '.join(services[:2])} cost?")
    if locations:
        base_faqs.append(f"Does {business_name} serve my area?")
    if certs:
        base_faqs.append(f"Is {business_name} certified or licensed?")
    if faq_topics:
        for topic in faq_topics[:5]:
            base_faqs.append(topic if '?' in topic else f"What is {business_name}'s approach to {topic.lower()}?")

    # Ensure at least 8 questions
    while len(base_faqs) < 8:
        q = f"What makes {business_name} different from other {industry_name.lower()} providers?"
        if q not in base_faqs:
            base_faqs.append(q)
        else:
            base_faqs.append(f"What should I look for when choosing a {industry_name.lower()} provider?")

    return '\n'.join(f"• {q}" for q in base_faqs[:12])


def analyze_geo_visibility(url: str, html: str, domain: str, industry_profile: dict = None) -> dict:
    findings = []
    score = 100
    soup = BeautifulSoup(html, 'html.parser')
    text = soup.get_text(separator=' ')
    words = len(text.split())
    first_200_words = ' '.join(text.split()[:200])

    # ── Industry context ────────────────────────────────────────────────
    profile = industry_profile or {}
    industry = profile.get('industry', 'general_business')
    business_name = profile.get('business_name', domain)
    services = profile.get('services', [])
    locations = profile.get('locations', [])
    is_local = profile.get('is_local_business', False)
    faq_topics = profile.get('relevant_faq_topics', [])
    keywords = profile.get('industry_keywords', [])
    usp_list = profile.get('unique_selling_points', [])
    certs = profile.get('certifications_or_specialties', [])

    # ═════════════════════════════════════════════
    # 1. FAQ / Q&A CONTENT
    # ═════════════════════════════════════════════
    question_patterns = [
        r'(?:what|how|where|when|why|who|can|do|does|is|are|should|will)\s+\w+[\s\w]{3,100}\?',
        r'^(?:Q|FAQ|Question)[\s:]+',
    ]
    faq_matches = []
    for p in question_patterns:
        faq_matches.extend(re.findall(p, text, re.IGNORECASE | re.MULTILINE))
    faq_matches = list(set(faq_matches))  # dedupe

    faq_schema = 'FAQPage' in html
    faq_visible = len(faq_matches) >= 2

    if not faq_visible and not faq_schema:
        # Generate industry-relevant FAQ examples from profile
        faq_examples = _generate_faq_examples(business_name, industry, services, faq_topics, locations, certs)
        findings.append({"severity":"critical","module":"geo","title":"Zero FAQ content — invisible to AI-generated answers","detail":f"AI engines (Google AI Overviews, ChatGPT, Perplexity, Claude, Gemini) primarily cite content that directly answers user questions. With zero Q&A content, the site has near-zero chance of appearing in AI-generated answers.","fix":f"Add 10-15 FAQ questions with detailed, authoritative answers (40-80 words each). Structure each as <h3>Question?</h3><p>Answer.</p>. Example questions for this business:\n{faq_examples}","impact":"AI citation rate estimated at 5-15% without FAQ content vs 40-60% with well-structured FAQ. Missing out on 25-45% of potential AI-driven traffic."})
        score -= 30
    elif faq_schema and not faq_visible:
        findings.append({"severity":"high","module":"geo","title":"FAQ exists only in JSON-LD schema — invisible to DOM-scraping AI models","detail":"FAQPage schema has questions but they are NOT rendered as visible HTML. Most AI models (ChatGPT Browse, Perplexity, Claude) scrape the DOM, not JSON-LD. This means the FAQ content is effectively hidden from AI. Additionally, Google considers schema without visible content a form of cloaking.","fix":"Render all FAQ questions and answers as visible HTML in an <section> or accordion at the bottom of the page. Use <h3> for questions and <p> for answers. Keep the schema if desired but know it won't generate rich results for commercial sites. The HTML FAQ is what matters for AI/GEO.","impact":"FAQ content invisible to ~70% of AI extraction methods. Wasted content investment."})
        score -= 20
    elif faq_visible and len(faq_matches) < 5:
        findings.append({"severity":"medium","module":"geo","title":f"Moderate FAQ content: {len(faq_matches)} questions — need more","detail":f"Questions found: {faq_matches[:5]}. 10-15 questions is the competitive benchmark.","fix":f"Expand to 10-15 questions relevant to {industry.replace('_', ' ')}. Cover: services, pricing, availability, locations, certifications, process, guarantees, and comparisons vs alternatives."})
        score -= 10
    elif faq_visible:
        findings.append({"severity":"pass","module":"geo","title":f"Strong FAQ content: {len(faq_matches)} question patterns detected","detail":"This is optimal for AI citation. Ensure each answer is 40-80 words, authoritative, and includes specific details (names, numbers, certifications) rather than generic statements.","fix":None})

    # ═════════════════════════════════════════════
    # 2. STRUCTURED LISTS (AI Overview gold)
    # ═════════════════════════════════════════════
    ul_count = len(soup.find_all('ul'))
    ol_count = len(soup.find_all('ol'))
    li_count = len(soup.find_all('li'))

    if li_count == 0:
        findings.append({"severity":"high","module":"geo","title":"Zero structured lists — AI can't extract scannable data","detail":"Bullet points and numbered lists are the #1 most cited format in Google AI Overviews and ChatGPT. A page with zero <li> elements is virtually invisible for any query that can be answered with a list.","fix":f"Add structured lists relevant to {industry.replace('_', ' ')}: services, locations, certifications, process steps, differentiators. Lists appear in 40%+ of AI Overviews.","impact":"Directly impacts AI Overview visibility. Estimated traffic opportunity: 15-25%."})
        score -= 15
    elif li_count < 10:
        findings.append({"severity":"medium","module":"geo","title":f"Few structured lists: only {li_count} list items","detail":f"{ul_count} unordered + {ol_count} ordered lists. AI models extract lists with 15+ items for comprehensive citation. Competitors with more structured data will be cited over you.","fix":"Add at least 3-4 more lists with 5+ items each. Prioritize: locations, services, certifications, and FAQs as lists."})
        score -= 8

    # ═════════════════════════════════════════════
    # 3. DEFINITION BLOCK
    # ═════════════════════════════════════════════
    def_patterns = [
        r'(?:is|are)\s+(?:a|an|the)\s+(?:certified|leading|premier|trusted|family-owned|professional|top|expert)\s+[\w\s]{15,80}(?:company|business|shop|center|provider|group|repair)',
        r'(?:we|[A-Z][a-z]+\s(?:Inc|LLC|Co|Group|Corp)?)\s+(?:is|are|provides?|specializes?|offers?|operates?)\s+[\w\s]{20,120}',
        r'(?:founded|established|serving)\s+(?:in\s+)?\d{4}',
    ]
    has_definition = any(re.search(p, first_200_words, re.IGNORECASE) for p in def_patterns)
    if not has_definition:
        findings.append({"severity":"high","module":"geo","title":"Missing authoritative definition block — AI can't identify the business","detail":f"AI models look for a clear 'what/who/where' statement in the first 300-500 visible characters. Without it, AI may not confidently identify or cite {business_name}.","fix":f"Add as the FIRST content block after the hero/H1 (visible text, not an image): a 1-2 sentence paragraph stating what {business_name} is, what it does, and where it operates. Example: '<strong>{business_name}</strong> is a {industry.replace('_', ' ')} provider{f' serving {locations[0]}' if locations else ''}. {business_name} specializes in {', '.join(services[:3]) if services else 'professional services'}.' This single paragraph is the highest-ROI GEO improvement.","impact":f"Without this, AI may refuse to cite {business_name} for brand queries. Estimated AI citation improvement: +20-40%."})
        score -= 15

    # ═════════════════════════════════════════════
    # 4. SEMANTIC HTML5
    # ═════════════════════════════════════════════
    semantic = {t: len(soup.find_all(t)) for t in ['article','section','aside','nav','header','footer','main']}
    total_semantic = sum(semantic.values())
    if total_semantic == 0:
        findings.append({"severity":"medium","module":"geo","title":"Zero semantic HTML5 elements — poor AI content extraction","detail":"Semantic tags (<main>, <article>, <section>, <nav>, <header>, <footer>) help AI models identify content regions. Without them, AI must guess what is content vs. navigation vs. boilerplate.","fix":"Wrap main content in <main>, use <section> for content blocks (services, locations, about, FAQ), <nav> for navigation menus, <article> for blog posts or detailed content pieces."})
        score -= 8
    if semantic['article'] == 0:
        findings.append({"severity":"low","module":"geo","title":"No <article> tags — missing self-contained content markers","detail":"<article> tags tell AI models that content is a complete, self-contained piece suitable for citation. Blog posts, service descriptions, and location profiles benefit from <article> wrapping."})

    # ═════════════════════════════════════════════
    # 5. CITATION-READY ELEMENTS
    # ═════════════════════════════════════════════
    blockquote = len(soup.find_all('blockquote'))
    cite_el = len(soup.find_all('cite'))
    dfn_el = len(soup.find_all('dfn'))
    citation_rich = blockquote + cite_el + dfn_el
    if citation_rich == 0:
        findings.append({"severity":"low","module":"geo","title":"No citation-oriented HTML (blockquote, cite, dfn)","detail":"These elements signal quotable/authoritative content. Customer testimonials in <blockquote> with <cite> attribution are particularly valuable for AI citation.","fix":"Add 2-3 customer testimonials using <blockquote><p>\"...</p><footer>— <cite>Customer Name, City</cite></footer></blockquote>. Use <dfn> for industry term definitions."})

    # ═════════════════════════════════════════════
    # 6. ENTITY SIGNALS — scale, authority, trust
    # ═════════════════════════════════════════════
    entity = {
        'phone': bool(re.search(r'\d{3}[-.\s]?\d{3}[-.\s]?\d{4}', text)),
        'locations_scale': bool(re.search(r'\d+\s+(?:locations?|offices?|shops?|centers?|facilities)', text, re.IGNORECASE)),
        'certifications': bool(re.search(r'(?:certified|authorized|factory.trained|manufacturer.certified)', text, re.IGNORECASE)),
        'founded_year': bool(re.search(r'(?:since|established|founded|serving\s+since)\s+\d{4}', text, re.IGNORECASE)),
        'address': bool(re.search(r'\d+\s+\w+\s+(?:street|st|road|rd|ave|blvd|drive|dr|way|ln|lane)', text, re.IGNORECASE)),
        'guarantee': bool(re.search(r'(?:guarantee|warranty|satisfaction\s+guaranteed|risk.free)', text, re.IGNORECASE)),
        'credentials': bool(re.search(r'(?:licensed|insured|bonded|accredited)', text, re.IGNORECASE)),
    }
    # Which signals are *expected* depends on the business type. Local businesses
    # are expected to expose address/phone/scale; national brands & SaaS are not,
    # so we don't penalise them for missing physical-location signals.
    if is_local:
        considered_keys = list(entity.keys())
    else:
        considered_keys = [k for k in entity.keys() if k not in ('address','locations_scale')]
    considered = {k: entity[k] for k in considered_keys}
    entity_count = sum(considered.values())
    total_considered = len(considered)
    missing_entity = [k for k,v in considered.items() if not v]
    pass_threshold = max(3, total_considered - 2)

    if entity_count >= pass_threshold:
        findings.append({"severity":"pass","module":"geo","title":f"Strong entity signals: {entity_count}/{total_considered} present","detail":f"Present: {[k for k,v in considered.items() if v]}. AI models have multiple confidence signals to identify and cite this business.","fix":None})
    elif entity_count < max(2, total_considered // 2):
        _hint = "phone, certifications/credentials, founding year, guarantees" + (", number of locations, physical address" if is_local else "")
        findings.append({"severity":"high","module":"geo","title":f"Weak entity signals: only {entity_count}/{total_considered} — AI may not trust this entity","detail":f"Missing: {missing_entity}. AI models need clear, structured signals to confidently cite an organization.","fix":f"Ensure these appear in visible body text (not only schema or footer): {_hint}."})
        score -= 15

    # ═════════════════════════════════════════════
    # 7. AI CRAWLER CONTENT FRESHNESS
    # ═════════════════════════════════════════════
    current_year = str(datetime.now().year)
    has_current_year = current_year in text
    if not has_current_year:
        findings.append({"severity":"low","module":"geo","title":"No current year visible — AI may perceive content as stale","detail":"AI models use date signals to assess content freshness. Content appearing outdated is deprioritized for citation, especially for 'current' or 'best' queries.","fix":"Add the current year in footer copyright or a 'serving since 2007 — 19 years of excellence' statement."})

    # ═════════════════════════════════════════════
    # 8. E-E-A-T SCORING
    # ═════════════════════════════════════════════
    e_score = 0
    e_score += (2 if entity['certifications'] else 0)
    e_score += (1 if entity['founded_year'] else 0)
    e_score += (1 if entity['guarantee'] else 0)
    e_score += (1 if faq_visible else 0)
    e_score += (2 if entity['credentials'] else 0)
    e_score += (2 if has_definition else 0)
    # Scale signal counts for local/multi-location businesses; for national brands
    # and SaaS, authorship/definition depth already carries the weight.
    if is_local:
        e_score += (2 if entity['locations_scale'] else 0)
    else:
        e_score += (2 if entity['phone'] else 0)

    if e_score < 4:
        findings.append({"severity":"high","module":"geo","title":f"Low E-E-A-T signals score: {e_score}/11","detail":"E-E-A-T (Experience, Expertise, Authoritativeness, Trustworthiness) directly influences AI citation priority.","fix":"Priority fixes for E-E-A-T:\n1. Add definition block with founding year and credentials (+3)\n2. Add visible guarantees/certifications (+3)\n3. Add FAQ content (+1)\n4. Ensure phone + address in body text (+2)"})
        score -= 12
    elif e_score >= 8:
        findings.append({"severity":"pass","module":"geo","title":f"Strong E-E-A-T score: {e_score}/11","detail":"The site has robust entity signals for AI citation confidence.","fix":None})

    # ═════════════════════════════════════════════
    # 9. TABLES (AI loves tabular data)
    # ═════════════════════════════════════════════
    table_count = len(soup.find_all('table'))
    if table_count == 0 and entity['locations_scale']:
        findings.append({"severity":"low","module":"geo","title":"No HTML tables — missed AI citation format","detail":f"AI models heavily cite tabular data for comparison queries. For a {industry.replace('_', ' ')} business, a table of {'locations with contact info' if is_local else 'services with descriptions'} would be highly citable.","fix":"Consider adding a table with structured data relevant to your business type."})

    # ═════════════════════════════════════════════
    # 10. GEO INDUSTRY BENCHMARKS
    # ═════════════════════════════════════════════
    # Industry benchmarks — generic baseline for local service sites
    benchmarks = {
        'faq_questions': {'avg': 8, 'best': 15, 'yours': len(faq_matches), 'label': 'FAQ Questions'},
        'list_items': {'avg': 35, 'best': 80, 'yours': li_count, 'label': 'Structured List Items'},
        'eeat_score': {'avg': 6, 'best': 11, 'yours': e_score, 'label': 'E-E-A-T Signals'},
        'entity_signals': {'avg': 4, 'best': 7, 'yours': entity_count, 'label': 'Entity Signals'},
        'definition_block': {'avg': 1, 'best': 1, 'yours': 1 if has_definition else 0, 'label': 'Definition Block'},
        'semantic_tags': {'avg': 3, 'best': 7, 'yours': total_semantic, 'label': 'Semantic HTML5 Tags'},
        'tables': {'avg': 1, 'best': 5, 'yours': table_count, 'label': 'Data Tables'},
    }
    benchmark_lines = []
    for key, b in benchmarks.items():
        yours = b['yours']
        avg = b['avg']
        status = '✓' if yours >= avg else '⚠' if yours >= avg * 0.5 else '✗'
        benchmark_lines.append(f"{status} {b['label']}: {yours} (avg: {avg}, best: {b['best']})")

    if any(b['yours'] < b['avg'] for b in benchmarks.values()):
        below = [b['label'] for b in benchmarks.values() if b['yours'] < b['avg']]
        findings.append({"severity":"medium","module":"geo","title":f"Below industry average in {len(below)}/{len(benchmarks)} GEO metrics","detail":f"Benchmark comparison for {industry.replace('_', ' ')} sites:\n" + '\n'.join(benchmark_lines),"fix":"Prioritize metrics marked with ✗ or ⚠. Highest-ROI: FAQ content, structured lists, definition block."})
        score -= 10

    return {
        "score": max(0, score),
        "findings": findings,
        "faq_count": len(faq_matches), "faq_visible": faq_visible, "faq_schema_present": faq_schema,
        "ul_count": ul_count, "ol_count": ol_count, "li_count": li_count,
        "has_definition": has_definition,
        "semantic_tags": semantic, "total_semantic": total_semantic,
        "entity_signals": entity_count, "missing_entity": missing_entity,
        "eeat_score": e_score, "citation_elements": citation_rich,
        "table_count": table_count,
        "benchmarks": benchmarks, "benchmark_lines": benchmark_lines,
    }
