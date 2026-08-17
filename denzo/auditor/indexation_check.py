"""
Indexation Check — verifies whether a URL is actually indexed in Google.

Uses the Search Console `urlInspection.index:inspect` endpoint with the
org-level service account (denzo-seo-indexer). This is FREE and authoritative,
but it ONLY works for domains the service account has verified in Search
Console (Droppin's managed clients). For third-party domains Google does not
expose indexation — we return not_verified and emit NO finding (we never
fabricate an index status).
"""
import requests


def _norm_domain(domain: str) -> str:
    return (domain or '').replace('https://', '').replace('http://', '').split('/')[0].strip()


def check_indexation(url: str, domain: str) -> dict:
    """Return {'verified','checked','indexed','state'} for a URL.

    verified=False → the service account cannot inspect this domain (third-party
    site, or no credentials). We do not guess in that case.
    """
    norm = _norm_domain(domain)
    if not norm:
        return {'verified': False, 'checked': False, 'indexed': None, 'state': 'invalid'}

    try:
        # Cache-only check — avoids an extra Google API round-trip on every
        # cold audit. Only domains Droppin has verified appear here.
        from denzo.agents.utils.google_verification import _get_credentials, _get_verified_domains
        if norm not in _get_verified_domains():
            return {'verified': False, 'checked': False, 'indexed': None, 'state': 'not_verified'}

        creds = _get_credentials(scopes=['https://www.googleapis.com/auth/webmasters.readonly'])
        if not creds:
            return {'verified': False, 'checked': False, 'indexed': None, 'state': 'no_credentials'}

        resp = requests.post(
            'https://searchconsole.googleapis.com/v1/urlInspection/index:inspect',
            json={'inspectionUrl': url, 'siteUrl': f'sc-domain:{norm}'},
            headers={'Authorization': f'Bearer {creds.token}', 'Content-Type': 'application/json'},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            idx = data.get('inspectionResult', {}).get('indexStatusResult', {})
            state = idx.get('coverageState', '') or idx.get('verdict', '')
            # "Indexed, not submitted in sitemap" / "Submitted and indexed" → indexed.
            # "Discovered - currently not indexed" / "Crawled - currently not indexed" → not.
            indexed = ('indexed' in state.lower()) and ('not indexed' not in state.lower())
            return {'verified': True, 'checked': True, 'indexed': indexed, 'state': state}
        if resp.status_code == 403:
            # Not actually an owner despite cache — treat as not verified.
            return {'verified': False, 'checked': False, 'indexed': None, 'state': 'not_verified'}
        return {'verified': True, 'checked': False, 'indexed': None, 'state': f'error_{resp.status_code}'}
    except Exception:
        return {'verified': True, 'checked': False, 'indexed': None, 'state': 'error'}


def analyze_indexation(url: str, domain: str) -> dict:
    """Module-shaped result for the auditor pipeline (score + findings).

    Informational module — 0 weight. Emits a pass/critical finding only when we
    actually inspected the URL (verified domain). Silent otherwise.
    """
    r = check_indexation(url, domain)

    if r.get('checked') and r.get('indexed') is True:
        return {'score': 100, 'findings': [{
            'severity': 'pass', 'module': 'indexation',
            'title': 'Indexed in Google',
            'detail': f"Coverage state: {r.get('state', '')}",
        }]}

    if r.get('checked') and r.get('indexed') is False:
        return {'score': 0, 'findings': [{
            'severity': 'critical', 'module': 'indexation',
            'title': 'Not indexed in Google — invisible in search',
            'detail': f"Google reports this URL as not indexed ({r.get('state', '')}). The page cannot appear in search results at all.",
            'fix': 'Submit the URL in Google Search Console, ensure robots.txt does not block it, confirm the canonical tag points to the live URL, and request indexing.',
            'impact': '100% of organic traffic lost for this URL — it is invisible in Google.',
        }]}

    # not_verified / error → nothing we can honestly report.
    return {'score': 0, 'findings': []}
