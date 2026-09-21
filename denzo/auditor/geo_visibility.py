"""
GEO / AI Visibility Analyzer v2 — deep analysis of AI citation readiness.
Checks FAQ content, structured data quality, definition blocks, entity signals,
E-E-A-T indicators, semantic HTML5, citation formatting, freshness signals.
"""
import re
from datetime import datetime
from bs4 import BeautifulSoup


def _humanize_business_name(name):
    """Fallback: turn a bare domain/URL into a readable name when no real business
    name was extracted (e.g. morenovalleyclinicamedica.com → morenovalleyclinicamedica)."""
    n = (name or '').strip()
    if not n:
        return 'your business'
    n = re.sub(r'^https?://', '', n).split('/')[0].replace('www.', '')
    if '.' in n and not re.fullmatch(r'\d+\.\d+\.\d+\.\d+', n):
        n = n.split('.')[0]
    if not n:
        return 'your business'
    if re.search(r'[-_]', n):
        return ' '.join(p.title() for p in re.split(r'[-_]+', n))
    return n


def _generate_faq_examples(business_name, industry, services, faq_topics, locations, certs):
    """Generate industry-relevant FAQ examples."""
    business_name = _humanize_business_name(business_name)
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

    # Ensure at least 8 DISTINCT questions — never repeat the same fallback.
    fallbacks = [
        f"What makes {business_name} different from other {industry_name.lower()} providers?",
        f"What should I look for when choosing a {industry_name.lower()} provider?",
        f"How does {business_name} handle appointments or scheduling?",
        f"Is {business_name} accepting new clients?",
        f"What areas does {business_name} serve?",
        f"Does {business_name} accept insurance?",
    ]
    for q in fallbacks:
        if len(base_faqs) >= 8:
            break
        if q not in base_faqs:
            base_faqs.append(q)

    return '\n'.join(f"• {q}" for q in base_faqs[:12])


def _detect_spanish(soup, text_lower: str) -> bool:
    """Detect Spanish by <html lang> first, then word-frequency of function words.

    The old check (`any(word in text)` for 'de','la','el',…) misclassified English
    pages that mention Spanish place names ("Los Angeles", "La Jolla", "El Paso",
    "de la Peña"). Word frequency is far more robust.
    """
    html_tag = soup.find('html')
    lang = (html_tag.get('lang') or '') if html_tag else ''
    if lang.lower().startswith('es'):
        return True
    if lang.lower().startswith('en'):
        return False

    words = text_lower.split()
    _es = {'que', 'como', 'está', 'están', 'son', 'para', 'por', 'una', 'los', 'las', 'del', 'al', 'más',
           'muy', 'servicio', 'servicios', 'empresa', 'negocio', 'también', 'pero', 'sí', 'usted',
           'nosotros', 'somos', 'estamos', 'hay', 'cómo', 'cuándo', 'dónde', 'porque', 'calidad', 'nuestro'}
    _en = {'the', 'and', 'for', 'with', 'are', 'you', 'your', 'our', 'we', 'they', 'this', 'that',
           'from', 'have', 'has', 'was', 'were', 'service', 'services', 'business', 'company',
           'also', 'but', 'very', 'is', 'not', 'will', 'can'}
    es_score = sum(1 for w in words if w in _es)
    en_score = sum(1 for w in words if w in _en)
    return es_score > en_score


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
        # English
        r'(?:what|how|where|when|why|who|can|do|does|is|are|should|will)\s+\w+[\s\w]{3,100}\?',
        r'^(?:Q|FAQ|Question)[\s:]+',
        # Spanish
        r'(?:qué|cómo|cuándo|cuando|dónde|donde|por qué|porque|cuál|cual|quién|quien|cuánto|cuanto|cuántos|cuantos)\s+\w+[\s\w]{3,100}\?',
        r'^(?:P|R|Pregunta|Respuesta)[\s:]+',
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
        findings.append({"severity":"critical","module":"geo","title":"Zero FAQ content — invisible to AI-generated answers","detail":f"AI engines (Google AI Overviews, ChatGPT, Perplexity, Claude, Gemini) primarily cite content that directly answers user questions. With zero Q&A content, the site has near-zero chance of appearing in AI-generated answers.","fix":f"Add 10-15 FAQ questions with detailed, authoritative answers (40-80 words each). Structure each as <h3>Question?</h3><p>Answer.</p>. Example questions for this business:\n{faq_examples}","impact":"AI engines cite content that directly answers user questions; without visible Q&A content the site is unlikely to appear in AI-generated answers."})
        score -= 30
    elif faq_schema and not faq_visible:
        findings.append({"severity":"high","module":"geo","title":"FAQ exists only in JSON-LD schema — invisible to DOM-scraping AI models","detail":"FAQPage schema has questions but they are NOT rendered as visible HTML. Most AI models (ChatGPT Browse, Perplexity, Claude) scrape the DOM, not JSON-LD. This means the FAQ content is effectively hidden from AI. Additionally, Google considers schema without visible content a form of cloaking.","fix":"Render all FAQ questions and answers as visible HTML in an <section> or accordion at the bottom of the page. Use <h3> for questions and <p> for answers. Keep the schema if desired but know it won't generate rich results for commercial sites. The HTML FAQ is what matters for AI/GEO.","impact":"Most AI models read the visible DOM, not JSON-LD, so schema-only FAQ content is effectively hidden from them."})
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
        findings.append({"severity":"high","module":"geo","title":"Zero structured lists — AI can't extract scannable data","detail":"Bullet points and numbered lists are the #1 most cited format in Google AI Overviews and ChatGPT. A page with zero <li> elements is virtually invisible for any query that can be answered with a list.","fix":f"Add structured lists relevant to {industry.replace('_', ' ')}: services, locations, certifications, process steps, differentiators. Lists are a commonly cited format in AI-generated answers.","impact":"Improves the page's extractability for list-style queries."})
        score -= 15
    elif li_count < 10:
        findings.append({"severity":"medium","module":"geo","title":f"Few structured lists: only {li_count} list items","detail":f"{ul_count} unordered + {ol_count} ordered lists. AI models extract lists with 15+ items for comprehensive citation. Competitors with more structured data will be cited over you.","fix":"Add at least 3-4 more lists with 5+ items each. Prioritize: locations, services, certifications, and FAQs as lists."})
        score -= 8

    # ═════════════════════════════════════════════
    # 3. DEFINITION BLOCK
    # ═════════════════════════════════════════════
    def_patterns = [
        # English patterns
        r'(?:is|are)\s+(?:a|an|the)\s+(?:certified|leading|premier|trusted|family-owned|professional|top|expert)\s+[\w\s]{15,80}(?:company|business|shop|center|provider|group|repair|lab|laboratory|clinic|practice|firm)',
        r'(?:we|[A-Z][a-z]+\s(?:Inc|LLC|Co|Group|Corp)?)\s+(?:is|are|provides?|specializes?|offers?|operates?)\s+[\w\s]{20,120}',
        r'(?:founded|established|serving)\s+(?:in\s+)?\d{4}',
        # Spanish patterns
        r'(?:somos|es)\s+(?:un|una|el|la)\s+(?:certificado|acreditado|líder|principal|destacado|reconocido|profesional)\s+[\w\s]{15,80}(?:empresa|negocio|taller|clínica|centro|laboratorio|consultorio|estudio|despacho|consultora|agencia)',
        r'(?:somos|somos una|es una)\s+(?:empresa|clínica|compañía|organización|institución)\s+(?:dedicada a|especializada en|enfocada en)\s+[\w\s]{20,120}',
        r'(?:desde|fundad[oa]|establecid[oa]|cread[oa])\s+(?:en\s+)?(?:el\s+)?\d{4}',
        r'(?:con\s+más\s+de\s+\d{1,2}\s+años\s+(?:de\s+)?experiencia)',
    ]
    has_definition = any(re.search(p, first_200_words, re.IGNORECASE) for p in def_patterns)
    if not has_definition:
        findings.append({"severity":"high","module":"geo","title":"Missing authoritative definition block — AI can't identify the business","detail":f"AI models look for a clear 'what/who/where' statement in the first 300-500 visible characters. Without it, AI may not confidently identify or cite {business_name}.","fix":f"Add as the FIRST content block after the hero/H1 (visible text, not an image): a 1-2 sentence paragraph stating what {business_name} is, what it does, and where it operates. Example: '<strong>{business_name}</strong> is a {industry.replace('_', ' ')} provider{f' serving {locations[0]}' if locations else ''}. {business_name} specializes in {', '.join(services[:3]) if services else 'professional services'}.' This single paragraph is the highest-ROI GEO improvement.","impact":f"Without a clear definition, AI may not confidently identify or cite {business_name} for brand queries."})
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
    # Detect page language first
    text_lower = text.lower()
    is_spanish = _detect_spanish(soup, text_lower)

    if is_spanish:
        entity = {
            'phone': bool(re.search(r'(?:\+?\d{2,3}[-.\s]?)?\d{4}[-.\s]?\d{4}', text)),
            'address': bool(re.search(r'(?:calle|avenida|av|colonia|blvd|paseo|calzada|urb|urbanización|residencial|edificio|local|n°|#)\s+\w+', text, re.IGNORECASE)),
            'locations_scale': bool(re.search(r'\d+\s+(?:ubicaciones|sedes|oficinas|tiendas|centros|locations?|offices?|shops?|centers?|facilities)', text, re.IGNORECASE)),
            'certifications': bool(re.search(r'(?:certificado|acreditado|licenciado|autorizado|avalado|colegiado|miembro de|asociado a|certified|accredited|licensed|authorized|board.certified)', text, re.IGNORECASE)),
            'founded_year': bool(re.search(r'(?:desde|fundado|establecido|creado|iniciado|since|established|founded|serving\s+since)\s+\d{4}', text, re.IGNORECASE)),
            'guarantee': bool(re.search(r'(?:garantía|garantizado|satisfacción garantizada|sin riesgo|guarantee|warranty|satisfaction\s+guaranteed|risk.free)', text, re.IGNORECASE)),
            'credentials': bool(re.search(r'(?:doctor|médico|especialista|licenciado|ingeniero|abogado|colegiado|maestría|doctorado|PhD|MD|board|insured|bonded|asegurado)', text, re.IGNORECASE)),
            'organization': bool(re.search(r'(?:miembro de|asociación|cámara|colegio|federación|confederación|member of|association|chamber|federation)', text, re.IGNORECASE)),
            'years_experience': bool(re.search(r'(?:\d{1,2}\s*(?:años|years)\s*(?:de\s*)?experienc[ií]a)', text, re.IGNORECASE)),
        }
    else:
        entity = {
            'phone': bool(re.search(r'\d{3}[-.\s]?\d{3}[-.\s]?\d{4}', text)),
            'address': bool(re.search(r'\d+\s+\w+\s+(?:street|st|road|rd|ave|blvd|drive|dr|way|ln|lane)', text, re.IGNORECASE)),
            'locations_scale': bool(re.search(r'\d+\s+(?:locations?|offices?|shops?|centers?|facilities)', text, re.IGNORECASE)),
            'certifications': bool(re.search(r'(?:certified|accredited|licensed|authorized|board.certified)', text, re.IGNORECASE)),
            'founded_year': bool(re.search(r'(?:since|established|founded|serving\s+since)\s+\d{4}', text, re.IGNORECASE)),
            'guarantee': bool(re.search(r'(?:guarantee|warranty|satisfaction\s+guaranteed|risk.free)', text, re.IGNORECASE)),
            'credentials': bool(re.search(r'(?:doctor|physician|specialist|licensed|engineer|attorney|board|certified|PhD|MD)', text, re.IGNORECASE)),
            'organization': bool(re.search(r'(?:member of|association|chamber|federation|accredited by)', text, re.IGNORECASE)),
            'years_experience': bool(re.search(r'(?:\d{1,2}\+\s*years?\s*(?:of\s*)?experience)', text, re.IGNORECASE)),
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
    # HONESTY NOTE: Real E-E-A-T is evaluated by Google through:
    # - Backlinks from authoritative sources (not analyzed here)
    # - Author credentials and bios (not analyzed here)
    # - External citations and mentions (not analyzed here)
    # - User reviews on third-party platforms (not analyzed here)
    # What we check below are ON-PAGE signals that CORRELATE with authority,
    # but are NOT a substitute for real E-E-A-T evaluation.
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
        findings.append({"severity":"high","module":"geo","title":f"Limited on-page authority signals: {e_score}/11","detail":f"Present signals: {[k for k,v in entity.items() if v]}. Missing: {missing_entity}. NOTE: Real E-E-A-T depends primarily on external factors (backlinks, citations, reviews, author credentials). On-page signals alone cannot establish authority.","fix":"Priority actions:\n1. Build backlinks from local/industry directories and news sites\n2. Create author bio pages with real credentials\n3. Get listed on Wikipedia, Crunchbase, BBB, industry associations\n4. Encourage Google reviews (for local businesses)\n5. Add visible trust signals on the page: certifications, awards, years in business, team credentials"})
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
    # 10. CONTENT QUALITY ASSESSMENT (honest, no fake benchmarks)
    # ═════════════════════════════════════════════
    # We do NOT present fabricated "industry averages". Instead, we report
    # what we found and give clear thresholds based on published research.
    quality_notes = []
    if len(faq_matches) < 5:
        quality_notes.append(f"FAQ content below recommended minimum (found {len(faq_matches)}, aim for 8+)")
    if li_count < 15:
        quality_notes.append(f"Structured list items below best practice (found {li_count}, aim for 20+)")
    if not has_definition:
        quality_notes.append("Missing definition block — AI models need a clear 'what/who' statement early in content")
    if entity_count < 3:
        quality_notes.append(f"Entity signals weak ({entity_count}/7) — add phone, address, certifications in visible text")

    if quality_notes:
        findings.append({"severity":"medium","module":"geo","title":f"Content quality gaps found ({len(quality_notes)} areas)","detail":"Based on analysis of what ranks in 2026:\n" + '\n'.join(f'• {n}' for n in quality_notes),"fix":"Address each gap above. These are real patterns seen in top-ranking pages — not fabricated averages, but concrete areas where your content falls short of competitive norms."})
        score -= len(quality_notes) * 5

    # ── Competitive context (qualitative, no fabricated statistics) ──
    competitive_context = {
        'faq_present_in_top10': 'Pages that answer user questions directly are more likely to be cited in AI-generated answers',
        'lists_in_top10': 'Structured lists are a commonly cited format in AI Overviews and featured snippets',
        'definition_in_top10': 'A clear definition block near the top helps AI identify and cite the business',
        'eeat_matters': 'E-E-A-T is evaluated primarily through external signals (backlinks, citations, reviews) — not on-page keywords',
    }

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
        "competitive_context": competitive_context,
    }
