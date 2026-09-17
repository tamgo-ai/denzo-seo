"""Versioned homepage screening from observable HTML and measured PageSpeed data."""
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from denzo.auditor.safe_fetch import fetch_html
from denzo.auditor.scoring import score_results, METHODOLOGY_VERSION, BASE_WEIGHTS
from denzo.auditor.framework_detector import detect_framework
from denzo.auditor.industry_detector import quick_detect, determine_is_local
from denzo.auditor.onpage_evidence import analyze_onpage
from denzo.auditor.robots_analyzer import analyze_robots
from denzo.auditor.sitemap_analyzer import analyze_sitemap
from denzo.auditor.performance_estimator import estimate_performance

MODULE_WEIGHTS = dict(BASE_WEIGHTS)


class SiteAnalyzer:
    def __init__(self, url, domain, progress_callback=None):
        self.url, self.domain = url, domain
        self.progress = progress_callback or (lambda _p, _s: None)

    def run_full_analysis(self):
        started = time.monotonic()
        self.progress(5, 'Fetching page HTML')
        try:
            fetched = fetch_html(self.url, capture_meta=True)
            html = fetched.get('html', '')
            if not fetched.get('ok') or not html:
                raise ValueError('Homepage could not be read')
            content_type = next((v for k,v in fetched.get('headers',{}).items() if k.lower()=='content-type'), '')
            if content_type and not any(t in content_type.lower() for t in ('text/html','application/xhtml+xml')):
                raise ValueError('Homepage response was not HTML')
            lowered = html.lower()
            if any(marker in lowered for marker in ('id="challenge-form"', '/cdn-cgi/challenge-platform/', 'cf-chl-widget')):
                raise ValueError('Homepage is an automated-access challenge')
        except Exception as exc:
            logging.getLogger(__name__).warning('Homepage fetch unavailable (%s): %s', type(exc).__name__, exc)
            return dict(url=self.url, error='The homepage could not be reliably read', overall_score=None,
                        status='failed', commercial_ready=False, methodology_version=METHODOLOGY_VERSION,
                        coverage=0, module_scores={}, findings=[], checked_at=datetime.now(timezone.utc).isoformat())
        # All checks describe the final page, including HTTP→HTTPS and www redirects.
        final_url = fetched.get('final_url') or self.url
        domain = urlsplit(final_url).hostname or self.domain
        headers = fetched.get('headers', {})
        framework = detect_framework(html, headers)
        industry = quick_detect(html) or {}
        is_local = determine_is_local(industry.get('primary_industry',''), industry.get('schema_info',{}).get('schema_types',[]), html)
        industry['is_local_business'] = is_local
        results = analyze_onpage(final_url, html, headers, is_local)
        results['_industry'] = industry
        self.progress(25, 'Checking crawler access, sitemaps and measured mobile performance')
        from denzo.agents.base_agent import _sqlite_local,close_thread_connection
        parent_token=getattr(_sqlite_local,'job_token',None)
        def run(name, fn):
            _sqlite_local.job_token=parent_token
            try: return fn()
            except Exception as exc:
                logging.getLogger(__name__).warning('Audit module %s unavailable (%s): %s', name, type(exc).__name__, exc)
                return dict(score=None, status='unavailable', findings=[], error=f'{name} could not be measured')
            finally:
                _sqlite_local.job_token=None
                close_thread_connection()
        with ThreadPoolExecutor(max_workers=3) as pool:
            robots_future = pool.submit(run, 'robots', lambda: analyze_robots(final_url, html, domain))
            performance_future = pool.submit(run, 'performance', lambda: estimate_performance(final_url, html, domain))
            results['robots'] = robots_future.result()
            sitemap_future = pool.submit(run, 'sitemap', lambda: analyze_sitemap(final_url, html, domain, results['robots']))
            results['performance'] = performance_future.result()
            results['sitemap'] = sitemap_future.result()
        self.progress(90, 'Validating measured evidence')
        technical = results['technical']
        # Avoid giving a client-rendered shell a content/SEO score.
        if framework.get('is_js_framework') and technical.get('word_count',0) < 80:
            results['technical'] = dict(score=None, status='unavailable', findings=[],
                                       error='Rendered content could not be verified for this JavaScript website')
        scoring = score_results(results, bool(is_local))
        findings = [dict(f, module=name) for name,result in results.items() if scoring['module_status'].get(name)=='completed'
                    and scoring['scoring_weights'].get(name,0)>0 for f in result.get('findings',[])]
        severity = {'critical':0,'high':1,'medium':2,'low':3,'info':4,'pass':5}
        findings.sort(key=lambda f: severity.get(f.get('severity'),4))
        soup = BeautifulSoup(html, 'html.parser')
        self.progress(100, 'Report generated')
        return dict(url=self.url, final_url=final_url, domain=domain, **scoring,
            checked_at=datetime.now(timezone.utc).isoformat(), results=results, findings=findings,
            page_status=fetched.get('status'), http_status=fetched.get('status'),
            fetch_method=fetched.get('method','public_http'), rendered=False,
            framework=framework.get('framework'), framework_label=framework.get('label'),
            page_title=soup.title.get_text(' ',strip=True)[:200] if soup.title else '',
            redirect_count=max(0,len(fetched.get('redirect_chain',[]))-1),
            duration_seconds=round(time.monotonic()-started,2), html_size_kb=round(len(html.encode())/1024),
            word_count=technical.get('word_count',0), text_html_ratio=technical.get('text_html_ratio',0),
            image_count=technical.get('image_count',0), schema_blocks=technical.get('schema_blocks',0),
            schema_types=technical.get('schema_types',[]), internal_links=technical.get('internal_links',0),
            sitemap_url=results['sitemap'].get('sitemap_url'), sitemap_total_urls=results['sitemap'].get('total_urls',0),
            sitemap_is_sample=results['sitemap'].get('is_sample',True), llms_generated={},
            weight_explanation={n:f'{w}% of the Droppin homepage screening rubric' for n,w in scoring['scoring_weights'].items()})
