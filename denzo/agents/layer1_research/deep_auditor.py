"""
Deep Technical Auditor v3 — enterprise-grade 7-module audit (Layer 1)
Now powered by the same engine as the public Site Auditor tool.
Modules: Technical SEO, Images, GEO/AI, Performance/CWV, Sitemap, Robots.txt, llms.txt
"""
import json, time
from datetime import datetime, timezone
from urllib.parse import urlparse

from denzo.agents.base_agent import TenantAwareBaseAgent, ClientContext, db_write, db_execute

# Reuse the Site Auditor engine
from denzo.auditor.technical_scanner import scan_technical
from denzo.auditor.image_auditor import deep_image_audit
from denzo.auditor.geo_visibility import analyze_geo_visibility
from denzo.auditor.performance_estimator import estimate_performance
from denzo.auditor.sitemap_analyzer import analyze_sitemap
from denzo.auditor.robots_analyzer import analyze_robots
from denzo.auditor.llms_analyzer import analyze_llms
from denzo.auditor.llms_generator import generate_llms_txt

# Module weights matching Site Auditor
MODULE_WEIGHTS = {
    'geo': 25, 'technical': 20, 'images': 15, 'performance': 15,
    'llms': 10, 'sitemap': 10, 'robots': 5,
}


class DeepTechnicalAuditor(TenantAwareBaseAgent):
    """Enterprise-grade SEO auditor — uses 7-module analysis engine."""

    def __init__(self, ctx: ClientContext):
        super().__init__("Technical Auditor", ctx, layer=1, color="gray")

    def _f(self, sev, cat, desc):
        return {"severity": sev, "category": cat, "description": desc}

    def run(self):
        self.log("Starting enterprise 7-module SEO audit...")
        self.set_status("working", "Fetching website")

        url = self.ctx.website_url or self.ctx.domain
        if not url:
            self.log("No website URL configured.", "warning")
            self.set_status("idle", "No website URL")
            return

        if not url.startswith("http"):
            url = "https://" + url

        domain = urlparse(url).netloc.replace('www.', '')

        # ── Fetch website ──
        try:
            from denzo.agents.utils.stealth_fetch import fetch_html
            result = fetch_html(url, timeout=25, log_fn=lambda m: self.log(m, "info"))
            if result["ok"]:
                html = result["html"]
                final_url = result.get("final_url", url)
                status_code = result.get("status", 200)
                fetch_method = result.get("method", "curl")
                self.log(f"Fetched via {fetch_method} — {status_code}", "info")
            else:
                self.log("All fetch methods failed — running AI fallback", "warning")
                self._ai_fallback(url)
                return
        except Exception as e:
            self.log(f"Fetch error: {e}", "error")
            self._ai_fallback(url)
            return

        self.set_status("working", "Running 7-module audit suite")

        # ═══════════════ RUN 7 MODULES ═══════════════
        self.log("1/7 Technical SEO scan...")
        tech = scan_technical(final_url, html, domain, None, status_code)

        self.log("2/7 Image deep audit...")
        images = deep_image_audit(final_url, html, domain)

        self.log("3/7 GEO / AI visibility...")
        geo = analyze_geo_visibility(final_url, html, domain)

        self.log("4/7 Performance estimation...")
        perf = estimate_performance(final_url, html, domain, None, 0)

        self.log("5/7 Sitemap analysis...")
        sitemap = analyze_sitemap(final_url, html, domain)

        self.log("6/7 Robots.txt analysis...")
        robots = analyze_robots(final_url, html, domain)

        self.log("7/7 llms.txt analysis...")
        llms = analyze_llms(final_url, html, domain)

        # ── Generate llms.txt ──
        self.log("Generating optimized llms.txt...")
        llms_gen = {}
        try:
            llms_gen = generate_llms_txt(final_url, html, domain, {'results': {
                'technical': tech, 'images': images, 'geo': geo,
                'performance': perf, 'sitemap': sitemap, 'robots': robots, 'llms': llms,
            }})
        except Exception as e:
            self.log(f"llms generation skipped: {e}", "warning")

        # ── Compute overall score ──
        module_scores = {
            'geo': geo.get('score', 0), 'technical': tech.get('score', 0),
            'images': images.get('score', 0), 'performance': perf.get('score', 0),
            'llms': llms.get('score', 0), 'sitemap': sitemap.get('score', 0),
            'robots': robots.get('score', 0),
        }
        overall = round(sum(module_scores[m] * (MODULE_WEIGHTS[m] / 100) for m in MODULE_WEIGHTS))

        # ── Collect findings ──
        all_findings = []
        for mod_results in [tech, images, geo, perf, sitemap, robots, llms]:
            for f in mod_results.get('findings', []):
                all_findings.append({
                    "severity": f['severity'].upper(),
                    "category": f.get('module', 'technical'),
                    "description": f"{f['title']}\n\n{f.get('detail','')}",
                })

        # ── Build audit_deep (compatible with existing dashboard) ──
        audit = {
            "url": final_url,
            "status_code": status_code,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "overall_score": overall,
            "module_scores": module_scores,
            "scores": {
                "onpage_seo": tech.get('score', 0),
                "schema": 100 if tech.get('schema_blocks', 0) > 0 else 0,
                "performance": perf.get('score', 0),
                "images": images.get('score', 0),
                "geo": geo.get('score', 0),
                "social": 100 if tech.get('has_og_tags') else 0,
                "content": 100 if tech.get('word_count', 0) >= 1500 else (50 if tech.get('word_count', 0) >= 500 else 0),
                "local_seo": 100 if geo.get('entity_signals', 0) >= 3 else 50,
                "security": 100,
            },
            "findings": all_findings,
            "meta": {
                "title": tech.get('title', ''), "title_length": tech.get('title_len', 0),
                "meta_description": tech.get('meta_description', ''), "meta_desc_length": tech.get('meta_description_len', 0),
                "canonical": bool(tech.get('canonical_url')), "h1_count": tech.get('h1_count', 0),
                "h2_count": tech.get('h2_count', 0), "h2_duplicates": [],
            },
            "performance": {
                "html_kb": tech.get('html_size_kb', 0),
                "inline_js_bytes": perf.get('inline_js_kb', 0) * 1024,
                "text_html_ratio": tech.get('text_html_ratio', 0),
                "external_js_count": tech.get('external_scripts', 0),
            },
            "images": {
                "total": images.get('total', 0), "no_alt": images.get('alt_quality_issues', 0),
                "no_dimensions": images.get('missing_dims', 0), "lazy_loaded": images.get('lazy', 0),
                "webp_count": images.get('webp_avif', 0),
            },
            "content": {
                "word_count": tech.get('word_count', 0), "paragraphs": 0,
                "lists": tech.get('ul_count', 0) + tech.get('ol_count', 0),
            },
            "schema": {"count": tech.get('schema_blocks', 0), "types": tech.get('schema_types', [])},
            "social": {"og_title": tech.get('og_missing', []) == [] or 'title' not in tech.get('og_missing', []),
                       "og_desc": True, "og_image": True, "twitter_card": tech.get('has_twitter_card', False)},
            "security": {"https": True},
            # Enriched data
            "cwv_estimates": perf.get('cwv', {}),
            "geo_benchmarks": geo.get('benchmarks', {}),
            "llms_generated": llms_gen,
            "module_scores_detailed": module_scores,
            "fetch_method": fetch_method,
        }

        # ── Save to DB ──
        try:
            db_write(
                "INSERT OR REPLACE INTO settings (tenant_id, key, value, updated_at) VALUES (?,?,?,?)",
                (self.ctx.tenant_id, "audit_deep", json.dumps(audit, ensure_ascii=False),
                 datetime.now(timezone.utc).isoformat())
            )
            self.log(f"Audit complete — score: {overall}/100", "success")
            self.set_status("done", f"Score: {overall}/100")
        except Exception as e:
            self.log(f"Failed to save audit: {e}", "error")
            self.set_status("error", str(e)[:100])

    def _ai_fallback(self, url):
        """Fallback when site blocks crawling — use Claude to estimate audit."""
        self.set_status("working", "AI fallback audit")
        try:
            from denzo.agents.base_agent import call_claude
            prompt = f"""You are an expert SEO auditor. The website {url} could not be crawled (likely Cloudflare/WAF protection). Based on the domain and industry, provide a realistic SEO audit estimate.

Return valid JSON:
{{
  "overall_score": <0-100>,
  "module_scores": {{"geo": 0, "technical": 0, "images": 0, "performance": 0, "llms": 0, "sitemap": 0, "robots": 0}},
  "scores": {{"onpage_seo": 0, "schema": 0, "performance": 0, "images": 0, "geo": 0, "social": 0, "content": 0, "local_seo": 0, "security": 100}},
  "findings": [{{"severity": "CRITICAL", "category": "technical", "description": "Site could not be crawled for automated audit"}}],
  "meta": {{"title": null, "title_length": 0, "meta_description": null, "meta_desc_length": 0, "canonical": true, "h1_count": 0, "h2_count": 0, "h2_duplicates": []}},
  "performance": {{"html_kb": 0, "inline_js_bytes": 0, "text_html_ratio": 0}},
  "images": {{"total": 0, "no_alt": 0, "no_dimensions": 0, "lazy_loaded": 0, "webp_count": 0}},
  "content": {{"word_count": 0, "paragraphs": 0, "lists": 0}},
  "schema": {{"count": 0, "types": []}},
  "social": {{"og_title": true, "og_desc": true, "og_image": true, "twitter_card": true}},
  "security": {{"https": true}},
  "cwv_estimates": {{}},
  "geo_benchmarks": {{}},
  "llms_generated": {{}},
  "url": "{url}", "status_code": 0, "timestamp": "{datetime.now(timezone.utc).isoformat()}"
}}"""
            message = call_claude(prompt, max_tokens=2000)
            data = json.loads(message) if isinstance(message, str) else message
            db_write(
                "INSERT OR REPLACE INTO settings (tenant_id, key, value, updated_at) VALUES (?,?,?,?)",
                (self.ctx.tenant_id, "audit_deep", json.dumps(data, ensure_ascii=False),
                 datetime.now(timezone.utc).isoformat())
            )
            self.log(f"AI fallback audit saved — score: {data.get('overall_score', 'N/A')}", "success")
            self.set_status("done", "AI fallback complete")
        except Exception as e:
            self.log(f"AI fallback failed: {e}", "error")
            self.set_status("error", str(e)[:100])
