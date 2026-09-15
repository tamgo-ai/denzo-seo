"""
PageSpeed Insights Real — calls Google's PSI API for actual Core Web Vitals data.
Uses CrUX field data when available, falls back to lab data (Lighthouse).
"""
import os
import time
import logging
from urllib.parse import urlencode
from denzo.auditor.scoring import valid_score

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

    api_url = 'https://www.googleapis.com/pagespeedonline/v5/runPagespeed?' + urlencode({'url': url, 'key': api_key, 'strategy': 'mobile', 'category': 'performance'})

    try:
        import urllib.request
        import json
        req = urllib.request.Request(api_url)
        req.add_header('Accept', 'application/json')
        resp = urllib.request.urlopen(req, timeout=60)
        data = json.loads(resp.read())

        result = _parse_psi_response(data)
        if not valid_score(result.get('score')):
            return None
        if len(_psi_cache) >= 512:
            _psi_cache.pop(next(iter(_psi_cache)), None)
        result['_ts'] = now
        _psi_cache[cache_key] = result
        return result
    except Exception as e:
        logger.warning("PageSpeed Insights unavailable (%s)", type(e).__name__)
        return None


_LH_CACHE_TTL_HOURS = 24


def _lh_cache_get(url: str):
    """Return a cached Lighthouse result for this URL within the TTL, else None."""
    import json
    from denzo.db import get_db

    db = get_db()
    try:
        db.execute(
            "CREATE TABLE IF NOT EXISTS lighthouse_cache (url TEXT PRIMARY KEY, result_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        row = db.execute(
            "SELECT result_json FROM lighthouse_cache WHERE url=? AND created_at > datetime('now', ?)",
            (url, f"-{_LH_CACHE_TTL_HOURS} hours"),
        ).fetchone()
        return json.loads(row["result_json"]) if row else None
    finally:
        db.close()


def _lh_cache_put(url: str, result: dict):
    import json
    from denzo.db import get_db

    db = get_db()
    try:
        db.execute(
            "CREATE TABLE IF NOT EXISTS lighthouse_cache (url TEXT PRIMARY KEY, result_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        db.execute(
            "INSERT INTO lighthouse_cache(url, result_json, created_at) VALUES (?,?,CURRENT_TIMESTAMP) ON CONFLICT(url) DO UPDATE SET result_json=excluded.result_json, created_at=CURRENT_TIMESTAMP",
            (url, json.dumps(result)),
        )
        db.commit()
    finally:
        db.close()


def get_lighthouse_performance(url: str) -> dict:
    """Run local Lighthouse (headless Chrome) for lab performance.

    Replaces the Google PageSpeed Insights API so the auditor does not depend
    on an enabled GCP project. Returns the same shape as get_real_performance
    but with lab data only (no CrUX field data).

    Results are cached per URL in SQLite for a day so that re-auditing the same
    page returns a stable score instead of a fresh, noisy Lighthouse lab run."""
    cache_key = url.rstrip("/")
    cached = _lh_cache_get(cache_key)
    if cached:
        return cached
    try:
        import json
        import shutil
        lighthouse_bin = shutil.which('lighthouse')
        if not lighthouse_bin:
            logger.warning("lighthouse CLI not available")
            return None
        cmd = [
            lighthouse_bin, url,
            '--output=json', '--output-path=stdout',
            '--chrome-flags=--headless --no-sandbox --disable-gpu --disable-dev-shm-usage',
            '--only-categories=performance',
            '--quiet',
        ]
        from denzo.processes import browser_slot,run_bounded
        with browser_slot():
            # Re-check inside the shared-Chrome lock: a concurrent audit of the
            # same URL may have just cached its Lighthouse run while we waited.
            cached = _lh_cache_get(cache_key)
            if cached:
                return cached
            proc = run_bounded(cmd,timeout=180)
        if proc.returncode != 0:
            logger.warning("lighthouse failed (%s)", proc.returncode)
            return None
        data = json.loads(proc.stdout)
        result = _parse_psi_response({'lighthouseResult': data})
        if not valid_score(result.get('score')):
            return None
        _lh_cache_put(cache_key, result)
        return result
    except Exception as e:
        logger.warning("lighthouse unavailable (%s)", type(e).__name__)
        return None


def _parse_psi_response(data: dict) -> dict:
    """Extract key metrics from PSI API response."""
    result = {
        'score': None,
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
    raw_score = perf.get('score')
    result['score'] = round(raw_score * 100) if valid_score(raw_score) and raw_score <= 1 else None
    result['checked_at'] = lighthouse.get('fetchTime')
    result['final_url'] = lighthouse.get('finalUrl')

    # Field data (CrUX — real user metrics)
    loading_experience = data.get('loadingExperience', {})
    if loading_experience:
        metrics = loading_experience.get('metrics', {})
        for key in ['LARGEST_CONTENTFUL_PAINT_MS', 'CUMULATIVE_LAYOUT_SHIFT_SCORE', 'INTERACTION_TO_NEXT_PAINT_MS', 'FIRST_INPUT_DELAY_MS', 'EXPERIMENTAL_TIME_TO_FIRST_BYTE']:
            if key in metrics:
                short_key = key.lower().replace('_ms','').replace('experimental_','')
                result['field_data'][short_key] = {
                    'percentile': (metrics[key]['percentile'] / 100 if key == 'CUMULATIVE_LAYOUT_SHIFT_SCORE' else metrics[key]['percentile']) if isinstance(metrics[key].get('percentile'), (int, float)) else None,
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
                'numeric_value': audit.get('numericValue'),
            }

    # Opportunities (actionable fixes)
    for audit_key in ['render-blocking-resources', 'unused-css-rules', 'unused-javascript',
                      'offscreen-images', 'uses-webp-images', 'uses-optimized-images',
                      'server-response-time', 'uses-text-compression', 'uses-responsive-images',
                      'efficient-animated-content', 'dom-size', 'total-byte-weight']:
        audit = audits.get(audit_key, {})
        if audit and isinstance(audit.get('score'), (int, float)) and audit['score'] < 0.9:
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
