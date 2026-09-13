"""Measured mobile Lighthouse performance, with CrUX field data kept separate."""
from denzo.auditor.pagespeed_real import get_real_performance
from denzo.auditor.scoring import valid_score


def estimate_performance(url, html, domain, redirect_chain=None, fetch_time_ms=None, framework=None):
    data = get_real_performance(url)
    if not data or not valid_score(data.get('score')):
        return {'score': None, 'status': 'unavailable', 'findings': [], 'cwv': {},
                'source': 'unavailable', 'error': 'Mobile PageSpeed measurement unavailable; retry with a working PAGESPEED_API_KEY.'}
    score = data['score']
    lab, field = data.get('lab_data', {}), data.get('field_data', {})
    severity = 'high' if score < 50 else 'medium' if score < 90 else 'pass'
    findings = [{'severity': severity, 'title': f'Mobile Lighthouse performance: {score}/100',
                 'detail': 'A laboratory measurement of this page. Conditions vary between runs; this is not a Google search ranking score.',
                 'evidence': {'source': 'Google PageSpeed Insights', 'url': url, 'score': score},
                 'fix': 'Review the measured Lighthouse opportunities below.' if score < 90 else None}]
    for opp in data.get('opportunities', [])[:5]:
        findings.append({'severity': 'medium', 'title': opp['title'], 'detail': opp['description'],
                         'fix': 'Verify these savings in Lighthouse before changing the page.', 'evidence': opp.get('details', {})})
    return {'score': score, 'status': 'completed', 'source': 'pagespeed_insights_api',
            'findings': findings, 'lab_data': lab, 'field_data': field,
            'cwv': {'source': 'CrUX field data where available; Lighthouse lab data reported separately',
                    'field': field, 'lab': lab}, 'real_perf': data}
