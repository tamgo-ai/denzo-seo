"""Versioned screening rubric, not a Google ranking or traffic prediction."""
import math

METHODOLOGY_VERSION = 'droppin-audit-v3'
BASE_WEIGHTS = {'technical': 40, 'geo': 0, 'performance': 35, 'sitemap': 5,
                'robots': 10, 'images': 5, 'content': 5, 'local_seo': 0,
                'keywords': 0, 'llms': 0, 'ai_citations': 0, 'keyword_research': 0, 'indexation': 0}


def valid_score(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 100


def score_results(results, is_local=False):
    weights = dict(BASE_WEIGHTS)
    scores, statuses = {}, {}
    for name in weights:
        module = results.get(name) or {}
        valid = valid_score(module.get('score')) and not module.get('error') and module.get('status', 'completed') == 'completed'
        if name == 'performance':
            valid = valid and module.get('source') == 'pagespeed_insights_api'
        scores[name] = module.get('score') if valid else None
        statuses[name] = 'completed' if valid else 'unavailable'
    coverage = sum(weight for name, weight in weights.items() if scores[name] is not None)
    overall = math.floor(sum((scores[name] or 0) * weight for name, weight in weights.items()) / coverage + 0.5) if coverage else None
    ready = coverage == 100
    return {'methodology_version': METHODOLOGY_VERSION, 'scoring_weights': weights,
            'module_scores': scores, 'module_status': statuses, 'coverage': coverage,
            'overall_score': overall, 'commercial_ready': ready,
            'status': 'completed' if ready else 'partial',
            'scope': 'Automated homepage website-health screening. Not a Google SEO score or ranking prediction.'}
