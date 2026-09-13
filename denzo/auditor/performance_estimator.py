"""Measured mobile Lighthouse performance (local Chrome, no Google PSI API)."""
from denzo.auditor.pagespeed_real import get_lighthouse_performance
from denzo.auditor.scoring import valid_score
from urllib.parse import urlsplit


def estimate_performance(url, html, domain, redirect_chain=None, fetch_time_ms=None, framework=None):
    data = get_lighthouse_performance(url)
    if data and data.get('final_url'):
        measured = urlsplit(data['final_url'])
        target = urlsplit(url)
        if measured.hostname and measured.hostname.removeprefix('www.') != (target.hostname or '').removeprefix('www.'):
            data = None
    if not data or not valid_score(data.get('score')):
        return {'score': None, 'status': 'unavailable', 'findings': [], 'cwv': {},
                'source': 'unavailable', 'error': 'Mobile Lighthouse measurement unavailable; retry with a reachable website.'}
    score = data['score']
    lab, field = data.get('lab_data', {}), data.get('field_data', {})
    severity = 'high' if score < 50 else 'medium' if score < 90 else 'pass'
    findings = [{'severity': severity, 'title': f'Mobile Lighthouse performance: {score}/100',
                 'detail': 'A laboratory measurement of this page. Conditions vary between runs; this is not a Google search ranking score.',
                 'evidence': {'source': 'Local Lighthouse (headless Chrome)', 'url': url, 'score': score},
                 'fix': 'Review the measured Lighthouse opportunities below.' if score < 90 else None}]
    for opp in data.get('opportunities', [])[:5]:
        findings.append({'severity': 'medium', 'title': opp['title'], 'detail': opp['description'],
                         'fix': 'Verify these savings in Lighthouse before changing the page.', 'evidence': opp.get('details', {})})
    return {'score': score, 'status': 'completed', 'source': 'lighthouse_local',
            'findings': findings, 'lab_data': lab, 'field_data': field,
            'cwv': {'source': 'Lighthouse lab data measured locally',
                    'field': field, 'lab': lab}, 'real_perf': data}
