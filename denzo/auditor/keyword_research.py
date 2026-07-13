"""
Keyword Research — suggests target keywords based on site content and industry.
Uses Claude to generate intelligent keyword suggestions with intent classification.
"""
import os
import json
import re
from bs4 import BeautifulSoup


def research_keywords(url: str, html: str, domain: str, industry_profile: dict = None) -> dict:
    """
    Analyze current keyword usage and suggest additional target keywords.
    Returns actionable keyword recommendations grouped by intent.
    """
    findings = []
    score = 100
    soup = BeautifulSoup(html, 'html.parser')
    text = soup.get_text(separator=' ')[:3000]

    profile = industry_profile or {}
    industry = profile.get('industry', 'general_business')
    business_name = profile.get('business_name', '')
    services = profile.get('services', [])
    locations = profile.get('locations', [])

    # Extract current keywords from headings and content
    title_tag = soup.find('title')
    title = title_tag.get_text(strip=True) if title_tag else ''
    h1 = ''
    h1_tag = soup.find('h1')
    if h1_tag:
        h1 = h1_tag.get_text(strip=True)
    h2s = [h.get_text(strip=True) for h in soup.find_all('h2')[:5]]

    # Generate keyword suggestions using Claude
    api_key = os.getenv('ANTHROPIC_API_KEY', '')
    keyword_suggestions = []

    if api_key:
        try:
            from anthropic import Anthropic
            client = Anthropic(timeout=20.0)

            prompt = f"""Analyze this business website and suggest 10 target keywords for SEO.

Business: {business_name}
Industry: {industry}
Services: {', '.join(services) if services else 'unknown'}
Locations: {', '.join(locations) if locations else 'unknown'}
Current title: {title}
H1: {h1}
Current H2s: {'; '.join(h2s)}

Return EXACTLY this JSON format:
{{
  "high_priority": [
    {{"keyword": "...", "intent": "transactional|commercial|informational", "monthly_volume_estimate": "estimated range", "difficulty": "low|medium|high", "why": "one line reason"}}
  ],
  "content_gaps": [
    {{"topic": "...", "keyword": "...", "why": "one line reason"}}
  ],
  "local_keywords": [
    {{"keyword": "...", "location": "...", "why": "one line reason"}}
  ]
}}

Be specific. Use real keywords that actual customers would search. For local keywords, include the city/location name."""

            resp = client.messages.create(
                model='claude-haiku-4-5-20251001',
                max_tokens=800,
                thinking={'type': 'disabled'},
                messages=[{'role': 'user', 'content': prompt}],
            )

            # Extract text from response (skip thinking blocks)
            raw = ''
            for block in resp.content:
                if hasattr(block, 'type') and block.type == 'text':
                    raw = block.text
                    break
            if not raw and hasattr(resp.content[0], 'text'):
                raw = resp.content[0].text
            if raw:
                match = re.search(r'\{[\s\S]*\}', raw)
                if match:
                    keyword_suggestions = json.loads(match.group(0))
        except Exception as e:
            keyword_suggestions = {'error': str(e)[:100]}

    # Assess current keyword coverage
    high_priority = keyword_suggestions.get('high_priority', []) if isinstance(keyword_suggestions, dict) else []
    content_gaps = keyword_suggestions.get('content_gaps', []) if isinstance(keyword_suggestions, dict) else []
    local_kws = keyword_suggestions.get('local_keywords', []) if isinstance(keyword_suggestions, dict) else []

    if len(high_priority) < 3 and api_key:
        findings.append({
            "severity": "medium",
            "module": "keyword_research",
            "title": "Limited keyword strategy — AI couldn't identify enough target keywords",
            "detail": "The site content doesn't clearly signal a keyword strategy. Competitors with well-defined keyword targets rank 3-5x better.",
            "fix": "Build a keyword strategy:\n1. Target 10-15 primary keywords across your pages\n2. Each page should target 1 primary + 2-3 secondary keywords\n3. Use keywords naturally in titles, H1s, H2s, and body text\n4. Create dedicated pages for high-value service+city combinations"
        })
        score -= 10

    return {
        "score": max(0, score),
        "findings": findings,
        "current_keywords": {
            "title_keywords": title,
            "h1_keywords": h1,
            "h2_keywords": h2s,
        },
        "suggestions": keyword_suggestions,
        "high_priority_count": len(high_priority),
        "content_gap_count": len(content_gaps),
        "local_keyword_count": len(local_kws),
    }
