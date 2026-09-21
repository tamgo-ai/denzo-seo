"""Versioned screening rubric, not a Google ranking or traffic prediction."""
import math

METHODOLOGY_VERSION = 'droppin-audit-v3'
BASE_WEIGHTS = {'technical': 40, 'geo': 10, 'performance': 35, 'sitemap': 5,
                'robots': 10, 'images': 5, 'content': 5, 'local_seo': 0,
                'geo_visibility': 15, 'authority': 10, 'e_e_a_t': 10,
                'keywords': 0, 'llms': 0, 'ai_citations': 0, 'keyword_research': 0, 'indexation': 0}


def valid_score(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 100


def score_results(results, is_local=False):
    weights = dict(BASE_WEIGHTS)
    # Local businesses get a weighted local-SEO signal; national brands/SaaS don't.
    if is_local:
        weights['local_seo'] = 10
    scores, statuses = {}, {}
    for name in weights:
        module = results.get(name) or {}
        valid = valid_score(module.get('score')) and not module.get('error') and module.get('status', 'completed') == 'completed'
        if name == 'performance':
            valid = valid and module.get('source') == 'lighthouse_local'
        scores[name] = module.get('score') if valid else None
        statuses[name] = 'completed' if valid else 'unavailable'
    denominator = sum(weight for name, weight in weights.items() if scores[name] is not None)
    overall = math.floor(sum((scores[name] or 0) * weight for name, weight in weights.items()) / denominator + 0.5) if denominator else None
    # commercial_ready = every REQUIRED module produced a valid score. `authority`
    # is an optional off-page enhancement: if the Ahrefs API is down or the plan
    # quota is exhausted, the audit must still complete and only report authority
    # as "not measured" — it must not block the whole audit.
    required_total = sum(w for n, w in weights.items() if w > 0 and n != 'authority')
    required_coverage = sum(w for n, w in weights.items() if w > 0 and n != 'authority' and scores[n] is not None)
    ready = required_total > 0 and required_coverage == required_total
    # `coverage` is a 0-100 completeness percentage (100 = every required module
    # measured), NOT the raw weight sum. Droppin's audit contract requires 100.
    coverage = round(100 * required_coverage / required_total) if required_total else 0
    return {'methodology_version': METHODOLOGY_VERSION, 'scoring_weights': weights,
            'module_scores': scores, 'module_status': statuses, 'coverage': coverage,
            'overall_score': overall, 'commercial_ready': ready,
            'status': 'completed' if ready else 'partial',
            'scope': 'Automated homepage website-health screening. Not a Google SEO score or ranking prediction.'}
