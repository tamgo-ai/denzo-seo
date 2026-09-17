"""Off-page authority via Ahrefs API — measured, no fabricated benchmarks.

Domain Rating (DR) is Ahrefs' own 0-100 logarithmic authority metric; we report
it directly as the score rather than inventing a "you need DR X" threshold.
Requires AHREFS_API_KEY in the environment (Authorization: Bearer).
"""
import os
import time
from datetime import datetime, timezone

AHREFS_API = "https://api.ahrefs.com/v3/site-explorer"
_CACHE_TTL = 48 * 3600
_cache: dict = {}


def _key():
    return os.environ.get('AHREFS_API_KEY', '')


def _call(endpoint, params):
    import requests
    key = _key()
    if not key:
        return None
    try:
        params = dict(params)
        params['date'] = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        r = requests.get(f'{AHREFS_API}/{endpoint}', params=params,
                         headers={'Authorization': f'Bearer {key}'}, timeout=20)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def analyze_authority(url, domain):
    if not _key():
        return {'score': None, 'status': 'unavailable', 'findings': [],
                'error': 'AHREFS_API_KEY is not configured'}

    now = time.time()
    cached = _cache.get(domain)
    if cached and cached[0] > now:
        return cached[1]

    stats = _call('backlinks-stats', {'target': domain, 'mode': 'domain'})
    rating = _call('domain-rating', {'target': domain})

    metrics = (stats or {}).get('metrics', {})
    live = metrics.get('live') or 0
    refdomains = metrics.get('live_refdomains') or 0
    all_time = metrics.get('all_time') or 0
    all_time_refdomains = metrics.get('all_time_refdomains') or 0

    dr = None
    if isinstance(rating, dict):
        node = rating.get('domain_rating')
        if isinstance(node, dict):
            dr = node.get('domain_rating')

    score = round(float(dr), 1) if isinstance(dr, (int, float)) else None

    findings = []
    if score is not None:
        if score < 5:
            sev, title = 'high', f'Very low domain authority: DR {score:.1f}/100'
        elif score < 20:
            sev, title = 'medium', f'Low domain authority: DR {score:.1f}/100'
        else:
            sev, title = 'info', f'Domain authority: DR {score:.1f}/100'
        findings.append({
            'severity': sev, 'module': 'authority', 'title': title,
            'detail': (f'Measured via Ahrefs: {live} live backlinks from {refdomains} '
                       f'referring domains ({all_time} backlinks / {all_time_refdomains} '
                       f'domains all-time). Domain Rating is an off-page authority metric, '
                       f'not a Google score.'),
            'fix': 'Authority is built over time through backlinks: local directories, industry associations, press coverage, and content worth linking to.',
            'deduction': 0,
        })

    result = {'score': score, 'status': 'completed' if score is not None else 'unavailable',
              'findings': findings, 'domain_rating': score, 'live_backlinks': live,
              'live_refdomains': refdomains, 'all_time_backlinks': all_time,
              'all_time_refdomains': all_time_refdomains}
    _cache[domain] = (now + _CACHE_TTL, result)
    return result
