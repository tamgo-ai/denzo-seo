"""
AI Citation Checker — verifies if the site gets cited by AI platforms.
Uses Perplexity API to check real AI search visibility.
"""
import os
import json
import re
from urllib.parse import urlparse


def check_ai_citations(url: str, html: str, domain: str, industry_profile: dict = None) -> dict:
    """
    Check if the business gets cited by AI platforms for relevant queries.
    Tests 3 queries (brand, main service, service+city) and checks if the domain
    appears in AI-generated answers.
    """
    findings = []
    score = 100

    api_key = os.getenv('PERPLEXITY_API_KEY', '')
    if not api_key:
        return {
            "score": 100,
            "findings": [],
            "citations_found": 0,
            "queries_checked": 0,
            "note": "Perplexity API key not configured — skip AI citation check"
        }

    profile = industry_profile or {}
    business_name = profile.get('business_name', domain.split('.')[0].replace('-', ' ').title())
    services = profile.get('services', [])
    locations = profile.get('locations', [])

    # Build test queries
    queries = []
    if business_name:
        queries.append(('branded', f'"{business_name}"'))
    if services:
        queries.append(('service', f'{services[0]} servicio'))
    if services and locations:
        queries.append(('local', f'{services[0]} en {locations[0]}'))
    if not queries:
        queries = [('generic', f'servicios en {domain}')]

    citations_found = 0
    checked = 0
    errors = 0
    results = []

    for qtype, query in queries[:3]:
        try:
            import urllib.request
            req = urllib.request.Request(
                'https://api.perplexity.ai/chat/completions',
                data=json.dumps({
                    'model': 'sonar',
                    'messages': [{
                        'role': 'user',
                        'content': f'Search query: {query}\n\nDoes the website {url} appear in results or get cited? Answer ONLY with the format: CITED:{url} or NOT_FOUND. If the site appears anywhere in your knowledge, say CITED.'
                    }]
                }).encode(),
                headers={
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {api_key}',
                    'Accept': 'application/json',
                },
            )
            resp = json.loads(urllib.request.urlopen(req, timeout=30.0).read())
            answer = resp['choices'][0]['message']['content'] if resp.get('choices') else ''

            cited = 'CITED' in answer.upper() and domain.lower() in answer.lower()
            if cited:
                citations_found += 1
            checked += 1

            results.append({
                'query': query,
                'type': qtype,
                'cited': cited,
            })
        except Exception as e:
            errors += 1
            results.append({
                'query': query,
                'type': qtype,
                'cited': None,
                'error': str(e)[:100],
            })

    total = len(queries)

    # If every query errored, we did not actually check anything — do NOT
    # report a false "invisible to AI search" finding.
    if checked == 0:
        return {
            "score": 100,
            "findings": [],
            "citations_found": 0,
            "queries_checked": 0,
            "results": results,
            "note": "AI citation check skipped — Perplexity API unavailable",
        }

    # Informational module (0 weight in the overall score). Severity kept at
    # "low" so a high overall SEO score isn't contradicted by a scary finding.
    if citations_found == 0:
        score -= 20
        findings.append({
            "severity": "low",
            "module": "ai_citations",
            "title": f"Zero AI citations: 0/{checked} queries — not yet visible in AI search",
            "detail": f"Your site was not cited by Perplexity AI for any of {checked} checked quer{'y' if checked == 1 else 'ies'}. AI visibility is tracked separately from the SEO score — it reflects mentions and citations outside your own site.",
            "fix": "To get cited by AI platforms:\n"
                   "1. Build brand mentions on Wikipedia, Crunchbase, BBB, and industry directories\n"
                   "2. Create authoritative content with unique data and statistics\n"
                   "3. Get cited by journalists and bloggers in your industry\n"
                   "4. Ensure your llms.txt and llms-full.txt are deployed\n"
                   "5. Build a strong backlink profile from .edu, .gov, and news domains",
            "impact": "AI search is a growing channel — but this is informational and does not affect your SEO score."
        })
    elif citations_found < checked:
        score -= 10
        findings.append({
            "severity": "low",
            "module": "ai_citations",
            "title": f"Partial AI visibility: {citations_found}/{checked} queries cited",
            "detail": f"Cited for: {', '.join(r['query'] for r in results if r['cited'])}. Not cited for: {', '.join(r['query'] for r in results if r['cited'] is False)}.",
            "fix": "Strengthen content and authority for the queries where you're not being cited."
        })
    else:
        findings.append({
            "severity": "pass",
            "module": "ai_citations",
            "title": f"Strong AI visibility: {citations_found}/{checked} queries cited by Perplexity",
            "detail": f"Your site appears in AI-generated answers for relevant business queries.",
            "fix": None
        })

    return {
        "score": max(0, score),
        "findings": findings,
        "citations_found": citations_found,
        "queries_checked": total,
        "results": results,
    }
