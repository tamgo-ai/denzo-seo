"""
Site Analyzer — orchestrates all 5 analysis modules in parallel.
Collects results, computes weighted overall score, generates structured report.
"""
import re
import time
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
from typing import Callable

# Import the shared HTTP fetcher
from denzo.agents.utils.stealth_fetch import fetch_html

# Import industry detector
from denzo.auditor.industry_detector import quick_detect, deep_detect

# Import analysis modules
from denzo.auditor.sitemap_analyzer import analyze_sitemap
from denzo.auditor.robots_analyzer import analyze_robots
from denzo.auditor.llms_analyzer import analyze_llms
from denzo.auditor.technical_scanner import scan_technical
from denzo.auditor.geo_visibility import analyze_geo_visibility
from denzo.auditor.llms_generator import generate_llms_txt
from denzo.auditor.image_auditor import deep_image_audit
from denzo.auditor.performance_estimator import estimate_performance


# Weight distribution for overall score
MODULE_WEIGHTS = {
    'geo': 25,
    'technical': 20,
    'images': 15,
    'performance': 15,
    'llms': 10,
    'sitemap': 10,
    'robots': 5,
}

assert sum(MODULE_WEIGHTS.values()) == 100, f"MODULE_WEIGHTS must sum to 100, got {sum(MODULE_WEIGHTS.values())}"


class SiteAnalyzer:
    """Orchestrates parallel SEO+GEO analysis of a single URL."""

    def __init__(self, url: str, domain: str, progress_callback: Callable = None):
        self.url = url
        self.domain = domain
        self.progress = progress_callback or (lambda p, step: None)

    def run_full_analysis(self) -> dict:
        """Run all 5 modules in parallel, compute scores, return complete results dict."""
        start = time.time()

        # Phase 1: Fetch the page
        self.progress(5, 'Fetching page HTML...')
        html = None
        fetch_method = 'unknown'
        http_headers = {}
        page_status = 0

        try:
            result = fetch_html(self.url)
            if result and result.get('ok') and result.get('html') and len(result['html']) > 500:
                html = result['html']
                fetch_method = result.get('method', 'curl')
                page_status = result.get('status', 200)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"fetch_html failed for {self.url}: {e}")

        if not html:
            return {
                "error": "Could not fetch page HTML after multiple attempts",
                "overall_score": 0,
                "module_scores": {},
                "findings": [],
            }

        html_size_kb = round(len(html) / 1024)

        # HTTP headers are optional — not fetched separately for speed

        # Extract page title
        title_match = re.search(r'<title>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
        page_title = title_match.group(1).strip()[:200] if title_match else ''

        # Phase 1.5: Detect industry (fast keyword-based first, then Claude deep detection)
        self.progress(12, 'Detecting industry...')
        industry_profile = quick_detect(html) or {}
        try:
            import asyncio
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    industry_profile = pool.submit(lambda: asyncio.run(deep_detect(html, self.url))).result(timeout=30)
            else:
                industry_profile = asyncio.run(deep_detect(html, self.url))
        except Exception:
            import logging
            logging.getLogger(__name__).warning(f"Industry deep_detect failed for {self.url}", exc_info=True)

        # Phase 2: Run all analysis modules in parallel (with industry context)
        self.progress(15, 'Analyzing sitemap.xml...')
        industry = industry_profile
        modules = {
            'sitemap': lambda: analyze_sitemap(self.url, html, self.domain),
            'robots': lambda: analyze_robots(self.url, html, self.domain),
            'llms': lambda: analyze_llms(self.url, html, self.domain),
            'technical': lambda: scan_technical(self.url, html, self.domain, http_headers),
            'geo': lambda: analyze_geo_visibility(self.url, html, self.domain, industry),
            'images': lambda: deep_image_audit(self.url, html, self.domain, base_page_url=self.url),
            'performance': lambda: estimate_performance(self.url, html, self.domain, None, 0),
        }

        results = {'_industry': industry_profile}
        completed = 0

        with ThreadPoolExecutor(max_workers=7) as executor:
            futures = {executor.submit(fn): name for name, fn in modules.items()}

            for future in as_completed(futures):
                name = futures[future]
                try:
                    results[name] = future.result()
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).error(f"Module '{name}' failed for {self.url}: {e}", exc_info=True)
                    results[name] = {"score": 0, "findings": [{
                        "severity": "critical",
                        "module": name,
                        "title": f"Analysis module '{name}' failed",
                        "detail": str(e),
                        "fix": "Retry the analysis. If the error persists, the site may be blocking automated analysis."
                    }]}

                completed += 1
                progress_pct = 15 + (completed * 11)  # 7 modules, 15 to 92
                self.progress(progress_pct, f'Analyzing {name}...')

        # Phase 3: Generate optimized llms.txt from site content
        self.progress(92, 'Generating llms.txt...')
        llms_gen = {}
        try:
            llms_gen = generate_llms_txt(self.url, html, self.domain, {'results': results}, industry_profile)
        except Exception:
            pass

        # Phase 4: Compute overall score
        overall = 0
        for module, weight in MODULE_WEIGHTS.items():
            if module in results:
                overall += results[module].get('score', 0) * (weight / 100)

        overall = round(overall)
        module_scores = {m: results[m].get('score', 0) for m in MODULE_WEIGHTS if m in results}

        # Phase 4: Collect all findings
        all_findings = []
        for module_name in ['sitemap', 'robots', 'llms', 'technical', 'geo', 'images', 'performance']:
            if module_name in results:
                for f in results[module_name].get('findings', []):
                    f['module'] = module_name
                    all_findings.append(f)

        # Sort by severity
        severity_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3, 'info': 4, 'pass': 5}
        all_findings.sort(key=lambda f: severity_order.get(f.get('severity', 'info'), 5))

        elapsed = time.time() - start
        self.progress(100, 'Report generated')

        return {
            "url": self.url,
            "domain": self.domain,
            "overall_score": overall,
            "module_scores": module_scores,
            "results": results,
            "findings": all_findings,
            "fetch_method": fetch_method,
            "page_title": page_title,
            "page_status": page_status,
            "html_size_kb": html_size_kb,
            "word_count": results.get('technical', {}).get('word_count', 0),
            "text_html_ratio": results.get('technical', {}).get('text_html_ratio', 0),
            "image_count": results.get('technical', {}).get('image_count', 0),
            "schema_blocks": results.get('technical', {}).get('schema_blocks', 0),
            "schema_types": results.get('technical', {}).get('schema_types', []),
            "sitemap_url": results.get('sitemap', {}).get('sitemap_url'),
            "sitemap_total_urls": results.get('sitemap', {}).get('total_urls', 0),
            "llms_status": results.get('llms', {}).get('llms_status'),
            "faq_count": results.get('geo', {}).get('faq_count', 0),
            "li_count": results.get('geo', {}).get('li_count', 0),
            "internal_links": results.get('technical', {}).get('internal_links', 0),
            "llms_generated": llms_gen,
        }
