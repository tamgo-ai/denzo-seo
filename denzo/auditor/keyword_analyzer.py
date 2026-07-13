"""
Keyword & Search Intent Analyzer — checks if the page actually targets a keyword
and whether content matches search intent.
"""
import re
from bs4 import BeautifulSoup
from collections import Counter


def analyze_keyword_targeting(url: str, html: str, domain: str) -> dict:
    """
    Analyze what keyword(s) the page is targeting and how well.
    Extracts signals from title, H1, headings, and body content.
    """
    findings = []
    score = 100
    soup = BeautifulSoup(html, 'html.parser')
    text = soup.get_text(separator=' ')
    words = [w.lower().strip('.,!?:;()[]{}"\'') for w in text.split() if len(w) > 2]
    word_freq = Counter(words)

    # Extract key page elements
    title_tag = soup.find('title')
    title = title_tag.get_text(strip=True) if title_tag else ''
    h1_tag = soup.find('h1')
    h1 = h1_tag.get_text(strip=True) if h1_tag else ''
    h2_tags = [h.get_text(strip=True) for h in soup.find_all('h2')]
    meta_desc = ''
    desc_tag = soup.find('meta', attrs={'name': 'description'})
    if desc_tag and desc_tag.get('content'):
        meta_desc = desc_tag['content'].strip()

    # ── Extract likely primary keyword from title ──
    # Remove brand name (text after | or — or -)
    kw_candidate = title.split('|')[0].split('—')[0].split(' - ')[0].strip().lower()
    primary_kw = kw_candidate if len(kw_candidate) > 3 else title.lower()

    # ── Check keyword presence across critical elements ──
    kw_words = set(primary_kw.split())
    kw_in_h1 = any(w in h1.lower() for w in kw_words if len(w) > 2)
    kw_in_first_p = False
    first_p = soup.find('p')
    if first_p:
        first_p_text = first_p.get_text(strip=True).lower()
        kw_in_first_p = any(w in first_p_text for w in kw_words if len(w) > 2)
    kw_in_h2s = any(any(w in h2.lower() for w in kw_words if len(w) > 2) for h2 in h2_tags[:3])

    url_words = set(re.findall(r'[a-z0-9]+', url.lower().split('://')[1].split('/')[-1] if '/' in url.split('://')[1] else ''))
    kw_in_url = bool(kw_words & url_words)

    # ── Title optimization ──
    title_len = len(title)
    kw_at_start = title.lower().startswith(primary_kw[:10])

    if not title:
        findings.append({"severity":"critical","module":"keywords","title":"No title tag — cannot optimize for any keyword","detail":"Without a title tag, the page has zero chance of ranking for target keywords. This is the #1 on-page factor.","fix":f"Include your primary keyword early in the title. Format: 'Primary Keyword — Secondary | Brand Name'. Example for this page: '[Your main service] | [City] | [Business]'"})
        score -= 30
    else:
        if title_len < 30:
            findings.append({"severity":"high","module":"keywords","title":f"Title too short ({title_len} chars) — limited keyword coverage","detail":f"Title: '{title[:100]}'. At {title_len} chars, you're using less than half the available SERP space. You can target 2-3 related keywords in a 50-65 char title.","fix":f"Expand title to 50-65 chars. Include primary keyword + secondary keyword + brand/location. Example structure: '[Primary Service] | [Secondary Benefit] — [Brand]'"})
            score -= 10
        elif not kw_at_start:
            findings.append({"severity":"medium","module":"keywords","title":"Primary keyword not at the START of the title","detail":f"Title: '{title[:120]}'. Google gives more weight to keywords appearing at the beginning of the title tag.","fix":f"Move the primary keyword closer to the start. Current first words: '{title[:40]}...'"})
            score -= 6

    # ── H1 optimization ──
    if not h1:
        findings.append({"severity":"high","module":"keywords","title":"No H1 tag — missing primary topic signal","detail":"The H1 is the second most important on-page element after the title tag. It tells Google what the page is about.","fix":f"Add an H1 that includes your primary keyword: '<h1>{primary_kw.title()}</h1>' or similar."})
        score -= 15
    elif not kw_in_h1:
        findings.append({"severity":"medium","module":"keywords","title":f"Primary keyword not found in H1","detail":f"H1: '{h1[:120]}'. Primary keyword '{primary_kw}' is missing. The H1 should reinforce the title's topic.","fix":f"Include '{primary_kw}' naturally in the H1 heading."})
        score -= 8

    # ── Search Intent Classification ──
    intent_signals = {
        'transactional': ['buy', 'price', 'cost', 'quote', 'estimate', 'order', 'purchase', 'hire', 'book', 'schedule', 'appointment', 'free consultation', 'get started', 'sign up'],
        'commercial': ['best', 'top', 'review', 'compare', 'vs', 'versus', 'comparison', 'rating', 'rated', 'recommended', 'guide', 'how to choose'],
        'informational': ['what is', 'how to', 'guide', 'learn', 'why', 'when', 'tutorial', 'tips', 'resources', 'blog', 'article', 'definition', 'meaning', 'examples'],
        'navigational': ['login', 'contact', 'about', 'location', 'directions', 'hours', 'phone', 'address'],
    }

    combined_text = f"{title} {h1} {meta_desc} {' '.join(text.split()[:500])}".lower()
    intent_scores = {}
    for intent, signals in intent_signals.items():
        score_i = sum(1 for s in signals if s in combined_text)
        intent_scores[intent] = score_i

    dominant_intent = max(intent_scores, key=intent_scores.get)
    total_signals = sum(intent_scores.values())

    if total_signals < 1:
        findings.append({"severity":"high","module":"keywords","title":"Unclear search intent — content doesn't match any known intent pattern","detail":f"The page doesn't have clear signals for transactional, commercial, or informational intent. Google needs to understand WHY someone would want this page.","fix":"Add clear intent signals:\n• For service pages: 'Get a quote', 'Book now', 'Free estimate', pricing info\n• For informational content: 'How to...', 'Guide to...', clear educational structure\n• For product/comparison: features, pricing, pros/cons, 'vs' comparisons"})
        score -= 12
    elif total_signals < 3:
        findings.append({"severity":"low","module":"keywords","title":f"Subtle search intent: only {total_signals} clear intent signal(s) detected","detail":f"Dominant intent appears to be '{dominant_intent}' but signals are subtle. Stronger intent signals help Google confidently match your page to the right queries.","fix":"Strengthen intent signals:\n• Transactional: add 'Book now', 'Get a quote', pricing, 'Schedule appointment'\n• Commercial: add 'Compare', 'Best', 'Top-rated', 'Reviews'\n• Informational: add 'Guide', 'How to', 'What is', 'Learn'"})
        score -= 4
    else:
        intent_labels = {
            'transactional': '🛒 Transactional — user wants to buy/hire/book',
            'commercial': '🔍 Commercial — user is researching options before buying',
            'informational': '📚 Informational — user wants to learn/understand',
            'navigational': '🏢 Navigational — user wants to find a specific business',
        }
        findings.append({"severity":"pass","module":"keywords","title":f"Search intent detected: {intent_labels.get(dominant_intent, dominant_intent)}","detail":f"Intent signals found: {', '.join(f'{k}={v}' for k,v in sorted(intent_scores.items(), key=lambda x: -x[1]) if v > 0)}. This helps Google match your page to the right queries.","fix":None})

    # ── Keyword density / relevance ──
    kw_count = sum(1 for w in words if w in kw_words)
    kw_density = round(kw_count / max(len(words), 1) * 100, 1)

    if kw_density < 0.5 and len(primary_kw) > 3:
        findings.append({"severity":"medium","module":"keywords","title":f"Very low keyword density ({kw_density}%) for primary term","detail":f"Primary keyword '{primary_kw}' appears only {kw_count} times in {len(words):,} words. Google needs to see the topic reinforced throughout the content.","fix":f"Naturally include related terms to '{primary_kw}' across the page. Use variations and synonyms in H2s, body text, and image alt text. Target: the primary keyword should appear naturally every 200-300 words."})
        score -= 6

    return {
        "score": max(0, score),
        "findings": findings,
        "primary_keyword": primary_kw,
        "title_length": title_len,
        "kw_in_h1": kw_in_h1,
        "kw_in_first_p": kw_in_first_p,
        "kw_in_h2s": kw_in_h2s,
        "kw_in_url": kw_in_url,
        "dominant_intent": dominant_intent,
        "intent_scores": intent_scores,
        "kw_density": kw_density,
        "word_frequency_top": [w for w, c in word_freq.most_common(20) if len(w) > 3 and w not in ('that', 'this', 'with', 'from', 'your', 'have', 'more', 'they', 'them', 'about')][:10],
    }
