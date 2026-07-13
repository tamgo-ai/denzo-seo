"""
Content Quality Analyzer — goes beyond word count to evaluate real content depth,
readability, originality signals, and competitive readiness.
"""
import re
from bs4 import BeautifulSoup


def _count_syllables_approx(text: str) -> int:
    """Rough syllable counter — works for English and Spanish."""
    text = text.lower().strip()
    if not text:
        return 0
    # Count vowel groups as syllables (works reasonably for both EN and ES)
    import re as _re
    # Spanish: a, e, i, o, u (very consistent syllabification)
    # English: rougher approximation
    syllables = len(_re.findall(r'[aeiouáéíóúü]+', text))
    # Subtract silent e patterns (English only — small effect on Spanish)
    syllables -= len(_re.findall(r'[aeiou]le\b', text))
    return max(1, syllables)


def analyze_content_quality(url: str, html: str, domain: str, industry_profile: dict = None) -> dict:
    """
    Analyze content quality beyond word count:
    - Readability (Flesch-Kincaid grade level)
    - Content structure (headings hierarchy, paragraph length)
    - Originality signals (statistics, data points, named entities)
    - Multimedia richness
    - Content freshness indicators
    """
    findings = []
    score = 100
    soup = BeautifulSoup(html, 'html.parser')
    text = soup.get_text(separator=' ')
    words = text.split()
    word_count = len(words)
    paragraphs = [p.get_text(strip=True) for p in soup.find_all('p') if len(p.get_text(strip=True)) > 50]

    # ── 1. Readability (language-aware) ──
    sentences = re.split(r'[.!?]+', text)
    sentences = [s.strip() for s in sentences if len(s.strip().split()) > 3]

    avg_words_per_sentence = round(word_count / max(len(sentences), 1), 1)
    avg_word_length = round(sum(len(w) for w in words) / max(len(words), 1), 1)

    # Detect language for appropriate readability formula
    text_lower = text.lower()
    is_spanish = any(w in text_lower.split() for w in ['de', 'la', 'el', 'los', 'en', 'del', 'por', 'para', 'con', 'una'])
    syllable_count = _count_syllables_approx(text)

    if is_spanish and sentences:
        # Fernandez-Huerta formula for Spanish
        # F-H = 206.84 - 0.60 * (syllables/100words) - 1.02 * (words/sentences)
        syllables_per_100 = (syllable_count / word_count) * 100
        fernandez_huerta = round(206.84 - 0.60 * syllables_per_100 - 1.02 * (word_count / len(sentences)), 1)
        # Convert to approximate grade level for consistency
        readability_score = max(0, min(18, round((100 - fernandez_huerta) / 5.5)))
        readability_method = 'Fernandez-Huerta (Spanish)'
    elif sentences:
        # Flesch-Kincaid for English
        if word_count > 0 and len(sentences) > 0:
            avg_syllables_per_word = syllable_count / max(word_count, 1)
            flesch_kincaid = round(0.39 * (word_count / len(sentences)) + 11.8 * avg_syllables_per_word - 15.59, 1)
            readability_score = max(0, min(18, flesch_kincaid))
            readability_method = 'Flesch-Kincaid (English)'
        else:
            readability_score = 0
            readability_method = 'unknown'
    else:
        readability_score = 0
        readability_method = 'unknown'

    if readability_score > 14:
        findings.append({"severity":"medium","module":"content","title":f"Content very complex: Grade {readability_score} reading level (college+)","detail":f"Readability calculated using {readability_method}. Most web content should target grade 8-10 for broad accessibility. For medical/technical content, grade 11-13 may be appropriate for the target audience.","fix":"Simplify sentences. Break long paragraphs. Use bullet points for complex ideas. Replace jargon with plain language where possible."})
        score -= 6  # Reduced from 8 - complex is OK for medical/legal content
    elif readability_score < 5 and word_count > 300:
        findings.append({"severity":"low","module":"content","title":f"Content very simple: Grade {readability_score} — may lack depth for competitive queries","detail":"While readability is good, very simple content may struggle to demonstrate expertise for competitive queries.","fix":"Add substantive detail: statistics, case studies, specific examples, expert insights."})
        score -= 2

    # ── 2. Content Structure ──
    avg_para_length = round(sum(len(p.split()) for p in paragraphs) / max(len(paragraphs), 1))

    if avg_para_length > 80:
        findings.append({"severity":"medium","module":"content","title":f"Paragraphs too long: avg {avg_para_length} words — hard to scan","detail":"Web readers scan, they don't read. Paragraphs over 80 words cause high bounce rates on mobile. Google measures 'pogo-sticking' (short clicks) as a negative ranking signal.","fix":"Break paragraphs into 2-4 sentences max (40-60 words). Use bullet points, numbered lists, and subheadings to break up text. Each paragraph should cover ONE idea."})
        score -= 7

    # ── 3. Originality Signals ──
    originality_signals = {
        'statistics': len(re.findall(r'\b\d{1,3}(?:,\d{3})*(?:\.\d+)?%?\b', text)),
        'dollar_amounts': len(re.findall(r'\$\d{1,3}(?:,\d{3})*(?:\.\d+)?', text)),
        'named_entities': len(re.findall(r'\b[A-Z][a-z]+ [A-Z][a-z]+\b', text)),  # Proper names
        'source_citations': len(re.findall(r'(?:according to|source|study|research|report|survey|published|data from)', text, re.IGNORECASE)),
        'dates_and_timeframes': len(re.findall(r'\b(?:19|20)\d{2}\b', text)),
        'specific_numbers': len(re.findall(r'\b\d+\s*(?:years|months|days|hours|clients|customers|projects|cases|locations|states|cities|countries)\b', text, re.IGNORECASE)),
    }

    originality_score = sum(originality_signals.values())

    if originality_score < 5:
        findings.append({"severity":"high","module":"content","title":"Content lacks originality signals — appears generic","detail":f"This page has almost no specific data points ({originality_score}/30+ signals). Google's Helpful Content System penalizes content that 'could have been written by anyone'. Pages with original data, statistics, and specific claims consistently outrank generic content.","fix":"Add original elements:\n• Statistics specific to your business or industry\n• Specific numbers (X years, Y clients, Z locations)\n• Named entities (partners, certifications, brands)\n• Source citations and data references\n• Case studies with real results\n• Pricing or timeline specifics"})
        score -= 15
    elif originality_score < 15:
        findings.append({"severity":"medium","module":"content","title":f"Moderate originality: {originality_score} specific data points found","detail":"More specific data would strengthen the page's uniqueness signal for Google.","fix":"Add more statistics, numbers, and specific claims that only your business could make. This is what separates commoditized content from authoritative content."})
        score -= 7

    # ── 4. Content Freshness ──
    current_year = '2026'  # Will be replaced by datetime
    try:
        from datetime import datetime
        current_year = str(datetime.now().year)
    except: pass

    has_current_year = current_year in text
    recent_years = re.findall(r'\b(202[3-6])\b', text)

    if not has_current_year and recent_years:
        latest_year = max(int(y) for y in recent_years)
        if latest_year < int(current_year):
            findings.append({"severity":"medium","module":"content","title":f"Content may be stale — latest date: {latest_year}, current: {current_year}","detail":"Google's Query Deserves Freshness (QDF) algorithm boosts recently updated content for queries where recency matters.","fix":f"Add {current_year} data points, statistics, or references. Update copyright footer to {current_year}. Add 'Last updated: [date]' to show freshness."})
            score -= 8

    # ── 5. Multimedia Richness ──
    images = soup.find_all('img')
    videos = soup.find_all('video')
    tables = soup.find_all('table')
    blockquotes = soup.find_all('blockquote')

    rich_elements = len(images) + len(videos) + len(tables) + len(blockquotes)

    if word_count > 500 and rich_elements < 2:
        findings.append({"severity":"medium","module":"content","title":"Text-heavy with no visual breaks — poor engagement","detail":f"{word_count} words with only {rich_elements} rich elements. Dense text without visual breaks causes high bounce rates.","fix":"Add: relevant images, a data table for comparison, customer quotes in blockquotes, or an embedded video. Visual elements increase time-on-page (a confirmed ranking signal)."})
        score -= 5

    return {
        "score": max(0, score),
        "findings": findings,
        "word_count": word_count,
        "readability_score": readability_score,
        "readability_method": readability_method,
        "avg_words_per_sentence": avg_words_per_sentence,
        "avg_para_length": avg_para_length,
        "paragraph_count": len(paragraphs),
        "originality_signals": originality_signals,
        "originality_score": originality_score,
        "rich_elements": rich_elements,
        "has_current_year": has_current_year,
    }
