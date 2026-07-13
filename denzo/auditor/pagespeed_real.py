"""
PageSpeed Insights Real — calls Google's PSI API for actual Core Web Vitals data.
Uses CrUX field data when available, falls back to lab data (Lighthouse).
"""
import os
import time
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Cache PSI results for 1 hour to avoid hitting rate limits (25K requests/day free tier)
_psi_cache: dict = {}
CACHE_TTL = 3600  # 1 hour


def get_real_performance(url: str) -> dict:
    """
    Call PageSpeed Insights API for real CWV data.
    Returns structured results or None if API key not configured or call fails.
    """
    api_key = os.getenv('PAGESPEED_API_KEY', '')
    if not api_key:
        return None

    cache_key = url.rstrip('/')
    now = time.time()
    if cache_key in _psi_cache:
        cached = _psi_cache[cache_key]
        if now - cached['_ts'] < CACHE_TTL:
            return cached

    api_url = f'https://www.googleapis.com/pagespeedonline/v5/runPagespeed?url={url}&key={api_key}&strategy=mobile&category=performance&category=seo'

    try:
        import urllib.request
        import json
        req = urllib.request.Request(api_url)
        req.add_header('Accept', 'application/json')
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())

        result = _parse_psi_response(data)
        result['_ts'] = now
        _psi_cache[cache_key] = result
        return result
    except Exception as e:
        logger.warning(f"PageSpeed Insights failed for {url}: {e}")
        return None


def _parse_psi_response(data: dict) -> dict:
    """Extract key metrics from PSI API response."""
    result = {
        'score': 0,
        'cwv': {},
        'lab_data': {},
        'field_data': {},
        'opportunities': [],
        'diagnostics': [],
    }

    # Overall score
    lighthouse = data.get('lighthouseResult', {})
    categories = lighthouse.get('categories', {})
    perf = categories.get('performance', {})
    result['score'] = perf.get('score', 0)  # 0-100

    # Field data (CrUX — real user metrics)
    loading_experience = data.get('loadingExperience', {})
    if loading_experience:
        metrics = loading_experience.get('metrics', {})
        for key in ['LARGEST_CONTENTFUL_PAINT_MS', 'CUMULATIVE_LAYOUT_SHIFT_SCORE', 'INTERACTION_TO_NEXT_PAINT_MS', 'FIRST_INPUT_DELAY_MS', 'EXPERIMENTAL_TIME_TO_FIRST_BYTE']:
            if key in metrics:
                short_key = key.lower().replace('_ms','').replace('experimental_','')
                result['field_data'][short_key] = {
                    'percentile': metrics[key].get('percentile', 0),
                    'category': metrics[key].get('category', 'unknown'),
                }

    # Lab data (Lighthouse)
    audits = lighthouse.get('audits', {})
    for audit_key, short_name in [
        ('largest-contentful-paint', 'lcp'),
        ('cumulative-layout-shift', 'cls'),
        ('total-blocking-time', 'tbt'),
        ('first-contentful-paint', 'fcp'),
        ('speed-index', 'speed_index'),
        ('interactive', 'tti'),
    ]:
        audit = audits.get(audit_key, {})
        if audit:
            result['lab_data'][short_name] = {
                'display_value': audit.get('displayValue', ''),
                'score': audit.get('score', 0),
                'numeric_value': audit.get('numericValue', 0),
            }

    # Opportunities (actionable fixes)
    for audit_key in ['render-blocking-resources', 'unused-css-rules', 'unused-javascript',
                      'offscreen-images', 'uses-webp-images', 'uses-optimized-images',
                      'server-response-time', 'uses-text-compression', 'uses-responsive-images',
                      'efficient-animated-content', 'dom-size', 'total-byte-weight']:
        audit = audits.get(audit_key, {})
        if audit and audit.get('score', 1) < 0.9:
            result['opportunities'].append({
                'title': audit.get('title', audit_key),
                'description': audit.get('description', ''),
                'display_value': audit.get('displayValue', ''),
                'details': _summarize_details(audit.get('details', {})),
            })

    return result


def _summarize_details(details: dict) -> dict:
    """Extract summary from PSI audit details."""
    summary = {}
    if 'overallSavingsMs' in details:
        summary['potential_savings_ms'] = details['overallSavingsMs']
    if 'overallSavingsBytes' in details:
        summary['potential_savings_bytes'] = details['overallSavingsBytes']
    items = details.get('items', [])
    if items:
        summary['top_items'] = []
        for item in items[:3]:
            summary['top_items'].append({
                'url': item.get('url', '')[:100],
                'wasted_ms': item.get('wastedMs', 0),
                'wasted_bytes': item.get('wastedBytes', 0),
            })
    return summary
