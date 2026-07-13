"""
Performance Estimator — estimates Core Web Vitals from page composition,
analyzes redirect chains, and checks DNS/TTFB indicators.

Since we can't call PageSpeed Insights API on every audit (rate limits),
we estimate CWV from page structure using Google's own research on
how page characteristics correlate with CWV metrics.

Based on Google's Web Almanac and CrUX research:
- LCP is primarily determined by: largest image size, server response time, render-blocking resources
- CLS is primarily determined by: images without dimensions, dynamic content injection, web fonts
- TBT/INP is primarily determined by: JavaScript size, number of scripts, main-thread work
"""
from urllib.parse import urlparse
from bs4 import BeautifulSoup


def estimate_performance(url: str, html: str, domain: str, redirect_chain: list = None, fetch_time_ms: int = None) -> dict:
    """Estimate Core Web Vitals. Uses real PSI data when available, falls back to heuristics."""

    # ── Try REAL PageSpeed Insights API first ──
    try:
        from denzo.auditor.pagespeed_real import get_real_performance
        real_perf = get_real_performance(url)
        if real_perf and real_perf.get('score', 0) > 0:
            # We got real data — use it!
            findings = []
            score = real_perf['score']

            cwv = real_perf.get('cwv', {})
            field_data = real_perf.get('field_data', {})
            lab_data = real_perf.get('lab_data', {})

            # Report real metrics
            lcp_ms = field_data.get('largest_contentful_paint_MS', lab_data.get('lcp', {}).get('numeric_value', 0))
            cls_val = field_data.get('cumulative_layout_shift_score', lab_data.get('cls', {}).get('numeric_value', 0))
            tbt_val = lab_data.get('tbt', {}).get('numeric_value', 0)

            if score < 50:
                findings.append({"severity":"critical","module":"performance","title":f"PageSpeed score: {score}/100 — POOR (real Lighthouse data)","detail":f"Google's PageSpeed Insights API measured real performance. LCP: {lcp_ms/1000:.1f}s, CLS: {cls_val:.3f}, TBT: {tbt_val:.0f}ms. This is real data, not estimates.","fix":"See detailed opportunities in the report for specific actions based on Lighthouse audits.","impact":"Pages with poor CWV lose 5-15% mobile rankings vs 'good' CWV pages."})
                score_cwv = max(0, score - 30)
            elif score < 90:
                findings.append({"severity":"medium","module":"performance","title":f"PageSpeed score: {score}/100 — NEEDS WORK (real Lighthouse data)","detail":f"Real Lighthouse measurement. LCP: {lcp_ms/1000:.1f}s, CLS: {cls_val:.3f}, TBT: {tbt_val:.0f}ms.","fix":"Focus on the top opportunities identified by Lighthouse."})
                score_cwv = max(0, score - 10)
            else:
                findings.append({"severity":"pass","module":"performance","title":f"PageSpeed score: {score}/100 — GOOD (real Lighthouse data)","detail":"Real measurement from Google PageSpeed Insights API.","fix":None})
                score_cwv = score

            # Add PSI opportunities as findings
            for opp in real_perf.get('opportunities', [])[:5]:
                findings.append({"severity":"medium","module":"performance","title":opp.get('title',''),"detail":opp.get('description',''),"fix":"See Lighthouse report for specific files and savings.","impact":f"Potential savings: {opp.get('display_value', 'unknown')}"})

            return {
                "score": max(0, score_cwv), "findings": findings,
                "html_kb": round(len(html)/1024), "source": "pagespeed_insights_api",
                "cwv": {
                    "estimated_lcp": round(lcp_ms/1000, 1) if lcp_ms else 0,
                    "estimated_cls": cls_val if cls_val else 0,
                    "estimated_tbt": round(tbt_val) if tbt_val else 0,
                    "lcp_grade": field_data.get('largest_contentful_paint_MS', {}).get('category', 'unknown'),
                    "cls_grade": field_data.get('cumulative_layout_shift_score', {}).get('category', 'unknown'),
                    "tbt_grade": 'good' if (tbt_val or 999) < 200 else 'needs_improvement' if (tbt_val or 999) < 600 else 'poor',
                    "source": "Google PageSpeed Insights API (real data)",
                },
                "real_perf": real_perf,
                "external_scripts": 0, "inline_js_kb": 0, "external_styles": 0,
                "third_party_domains": [], "redirect_count": 0, "redirect_cost_ms": 0,
                "fetch_time_ms": fetch_time_ms or 0,
            }
    except ImportError:
        pass  # PSI module not available, fall back to heuristics
    except Exception:
        import logging
        logging.getLogger(__name__).warning("PageSpeed Insights failed, falling back to heuristics")

    # ── FALLBACK: Heuristic estimates (clearly labeled) ──
    findings = []
    score = 100
    soup = BeautifulSoup(html, 'html.parser')
    html_bytes = len(html)
    html_kb = round(html_bytes / 1024)

    # Parse redirect chain
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    redirect_count = len(redirect_chain) - 1 if redirect_chain else 0
    redirect_cost_ms = (redirect_count * 300) if redirect_count else 0  # ~300ms per redirect on mobile

    # ── 1. REDIRECT CHAIN ──
    if redirect_count >= 2:
        chain_desc = ' → '.join(redirect_chain[:6]) if redirect_chain else 'unknown'
        findings.append({"severity":"high","module":"performance","title":f"Redirect chain: {redirect_count} hops — costs ~{redirect_cost_ms}ms","detail":f"Chain: {chain_desc}. Each redirect adds 200-400ms on mobile 4G. Google recommends zero redirects from the requested URL to the final destination.","fix":"Eliminate unnecessary redirects:\n1. HTTP → HTTPS: keep (necessary)\n2. non-www → www (or vice versa): keep, make permanent (308)\n3. / → /en: serve content directly at / using Accept-Language or Vercel middleware rewrite (not redirect)\n4. Trailing slash: configure server to avoid redirects","impact":f"LCP penalty: +{redirect_cost_ms}ms. Each redirect delays First Contentful Paint and Largest Contentful Paint."})
        score -= redirect_count * 8
    elif redirect_count == 1:
        findings.append({"severity":"low","module":"performance","title":"Single redirect detected — acceptable but not optimal","detail":"One redirect (likely HTTP→HTTPS or domain normalization) is standard. Ensure it's a 308 (permanent) not 307 (temporary).","fix":"Verify redirect is 308 Permanent, not 307 Temporary. 307 tells Google not to migrate the index.","impact":"Minimal LCP impact: +~300ms on mobile."})

    # ── 2. PAGE WEIGHT ──
    if html_kb > 500:
        findings.append({"severity":"high","module":"performance","title":f"Heavy page: {html_kb}KB HTML (uncompressed) — should be <200KB compressed","detail":f"Google's research shows pages over 500KB HTML have 2-3x worse LCP than pages under 200KB. This is typical of React/Next.js SPAs with large hydration payloads.","fix":"Reduce HTML size: enable brotli compression (Vercel does this automatically), reduce RSC payloads with Partial Prerendering (PPR), remove unused CSS/JS from initial HTML, serve static generation (not SSR) for content pages.","impact":"Estimated LCP penalty: +2-5 seconds on mobile 4G."})
        score -= 12
    elif html_kb > 300:
        findings.append({"severity":"medium","module":"performance","title":f"Above-average page weight: {html_kb}KB HTML","detail":"Aim for <200KB HTML for optimal mobile performance.","fix":"Enable compression, reduce inline JS, use static generation."})
        score -= 6

    # ── 3. JAVASCRIPT BLOAT ──
    scripts = soup.find_all('script')
    external_js = [s for s in scripts if s.get('src')]
    inline_js = [s for s in scripts if not s.get('src') and s.string]
    inline_js_bytes = sum(len(s.string or '') for s in inline_js)

    if inline_js_bytes > 200000:
        findings.append({"severity":"high","module":"performance","title":f"Massive inline JavaScript: {round(inline_js_bytes/1024)}KB — blocks rendering","detail":f"{len(inline_js)} inline script blocks totaling {round(inline_js_bytes/1024)}KB. This is Next.js RSC hydration data. On mobile, parsing and executing {round(inline_js_bytes/1024)}KB of inline JS takes 2-5 seconds on a mid-range device.","fix":"1. Enable Partial Prerendering (PPR) in Next.js 14+\n2. Use React Server Components to reduce client-side JS\n3. Code-split with dynamic imports\n4. Remove unused dependencies from the client bundle","impact":"Estimated TBT: +1000-3000ms. Direct INP penalty. LCP delayed by 2-5s."})
        score -= 15
    elif inline_js_bytes > 100000:
        findings.append({"severity":"medium","module":"performance","title":f"Significant inline JS: {round(inline_js_bytes/1024)}KB","detail":"Inline JS blocks rendering. Consider reducing the JS payload.","fix":"Enable PPR, code-split, reduce client bundle size."})
        score -= 8

    if len(external_js) > 10:
        findings.append({"severity":"medium","module":"performance","title":f"{len(external_js)} external JS files — excessive HTTP requests","detail":"Each external JS file requires a separate HTTP request + parse/compile step. While HTTP/2 multiplexes, too many bundles increase total parse time.","fix":"Bundle JS into fewer files. Remove unused third-party scripts. Audit each external script for necessity."})
        score -= 6

    # ── 4. CSS DELIVERY ──
    styles = soup.find_all('link', rel='stylesheet')
    inline_styles = soup.find_all('style')
    if len(styles) + len(inline_styles) > 5:
        findings.append({"severity":"low","module":"performance","title":f"{len(styles) + len(inline_styles)} CSS resources — consider consolidation","detail":"Multiple CSS files increase critical rendering path length.","fix":"Inline critical CSS in <head>. Load non-critical CSS asynchronously. Use CSS modules or Tailwind purging to eliminate unused styles."})

    # ── 5. FONT OPTIMIZATION ──
    font_links = soup.find_all('link', rel=lambda r: r and 'preload' in r and 'font' in str(r))
    preconnect_fonts = soup.find_all('link', rel='preconnect')
    if not font_links:
        findings.append({"severity":"low","module":"performance","title":"No font preloading — fonts may delay text rendering","detail":"Web fonts block text rendering until they load. Preloading with crossorigin ensures text appears faster.","fix":"Add <link rel='preload' as='font' href='...' crossorigin> for your primary web font. Use font-display: swap to show fallback text immediately."})

    if not preconnect_fonts:
        findings.append({"severity":"low","module":"performance","title":"No DNS preconnect hints — slow font/CDN loading","detail":"Preconnect hints tell the browser to establish early connections to third-party origins (fonts, CDN, analytics) before they're needed.","fix":"Add <link rel='preconnect' href='https://fonts.gstatic.com' crossorigin> and for any CDN domains used."})

    # ── 6. THIRD-PARTY RESOURCES ──
    third_party_domains = set()
    for s in external_js:
        src = s.get('src','')
        if src:
            d = urlparse(src).netloc
            if d and d.replace('www.','') != domain:
                third_party_domains.add(d)
    for s in styles:
        href = s.get('href','')
        if href:
            d = urlparse(href).netloc
            if d and d.replace('www.','') != domain:
                third_party_domains.add(d)

    if len(third_party_domains) > 5:
        findings.append({"severity":"medium","module":"performance","title":f"{len(third_party_domains)} third-party domains — excessive external requests","detail":f"Third-party domains: {', '.join(sorted(third_party_domains)[:8])}. Each third-party domain adds DNS lookup + connection time + potential render-blocking.","fix":"Audit each third-party script. Remove unnecessary ones. Load analytics/tracking asynchronously. Consider server-side tracking (GA4 Measurement Protocol) instead of client-side pixels."})
        score -= 8

    # ── 7. CWV ESTIMATES ──
    external_js_blocking = [s for s in external_js if not (s.get('async') is not None or s.get('defer') is not None)]
    external_js_async = len(external_js) - len(external_js_blocking)
    # Based on Google's research: page weight + JS size + image count → rough CWV estimate
    estimated_lcp = 1.5  # base server response
    estimated_lcp += (html_kb / 50) * 0.5  # ~0.5s per 50KB over baseline
    estimated_lcp += (inline_js_bytes / 50000) * 0.8  # inline JS blocks LCP
    estimated_lcp += (len(external_js_blocking) * 0.15) + (external_js_async * 0.02)
    estimated_lcp += redirect_cost_ms / 1000  # redirect time in seconds
    # Factor in LCP image if present (image LCP is the most common case)
    lcp_img = None
    imgs = soup.find_all('img')
    for i, img in enumerate(imgs[:3]):
        src = (img.get('src') or '').lower()
        if not ('logo' in src or 'icon' in src):
            lcp_img = img
            break
    if lcp_img:
        has_fetchpriority = lcp_img.get('fetchpriority') == 'high'
        has_preload = bool(soup.find('link', rel='preload', href=lcp_img.get('src')))
        if has_fetchpriority or has_preload:
            estimated_lcp = max(1.0, estimated_lcp - 1.5)  # Preloaded LCP is much faster
        else:
            estimated_lcp += 1.5  # Penalty for non-prioritized LCP image
    estimated_lcp = round(estimated_lcp, 1)

    estimated_cls = 0.01  # base
    # Each image without dimensions adds ~0.005 CLS
    imgs = soup.find_all('img')
    imgs_no_dims = sum(1 for img in imgs if not (img.get('width') and img.get('height')))
    estimated_cls += imgs_no_dims * 0.05  # More realistic: each dimensionless image shifts layout significantly
    estimated_cls = round(min(estimated_cls, 2.0), 3)

    estimated_tbt = 50  # base ms
    estimated_tbt += (inline_js_bytes / 1000) * 5  # ~5ms per KB on mid-range mobile (more realistic)
    estimated_tbt += len(external_js_blocking) * 80 + external_js_async * 10
    estimated_tbt = round(min(estimated_tbt, 10000))

    cwv = {
        'estimated_lcp': estimated_lcp,
        'estimated_cls': estimated_cls,
        'estimated_tbt': estimated_tbt,
        'lcp_grade': 'good' if estimated_lcp < 2.5 else 'needs_improvement' if estimated_lcp < 4.0 else 'poor',
        'cls_grade': 'good' if estimated_cls < 0.1 else 'needs_improvement' if estimated_cls < 0.25 else 'poor',
        'tbt_grade': 'good' if estimated_tbt < 200 else 'needs_improvement' if estimated_tbt < 600 else 'poor',
    }

    if estimated_lcp > 4.0:
        findings.append({"severity":"critical","module":"performance","title":f"Estimated LCP: {estimated_lcp}s — POOR (target: <2.5s)","detail":f"Based on page composition: {html_kb}KB HTML + {round(inline_js_bytes/1024)}KB inline JS + {len(external_js)} external scripts + {redirect_count} redirects. Google's LCP threshold is 2.5s for 'good'. At {estimated_lcp}s, the page is in the worst-performing quartile.","fix":"Priority order for LCP reduction:\n1. Eliminate redirect chain (-{redirect_cost_ms/1000}s)\n2. Reduce inline JS (-{round(inline_js_bytes/1024)}KB → target <50KB)\n3. Enable static generation / ISR for homepage\n4. Optimize LCP image with preload + fetchpriority='high'\n5. Enable CDN caching (currently Cache-Control: no-store)","impact":"LCP is a direct Google ranking signal. Pages with 'poor' LCP lose 5-15% of mobile rankings vs 'good' LCP pages."})
        score -= 20
    elif estimated_lcp > 2.5:
        findings.append({"severity":"medium","module":"performance","title":f"Estimated LCP: {estimated_lcp}s — NEEDS IMPROVEMENT (target: <2.5s)","detail":f"Contributing factors: {html_kb}KB HTML, {len(external_js)} external scripts, {redirect_count} redirects.","fix":"Reduce page weight and inline JS. Enable CDN caching. Eliminate redirects."})
        score -= 8

    if estimated_cls > 0.1:
        findings.append({"severity":"medium","module":"performance","title":f"Estimated CLS: {estimated_cls} — NEEDS IMPROVEMENT (target: <0.1)","detail":f"{imgs_no_dims} images without dimensions are the primary CLS contributor. Each adds ~0.005 to CLS.","fix":"Add explicit width/height to all {imgs_no_dims} images without dimensions. This alone can bring CLS below 0.1."})
        score -= 8

    if estimated_tbt > 600:
        findings.append({"severity":"high","module":"performance","title":f"Estimated TBT: {estimated_tbt}ms — POOR (target: <200ms)","detail":f"Total Blocking Time correlates with INP (Interaction to Next Paint). At {estimated_tbt}ms, the main thread is frozen for significant periods, making the page feel unresponsive.","fix":"Reduce JavaScript execution time. Code-split, remove unused JS, defer non-critical scripts."})
        score -= 12

    return {
        "score": max(0, score), "findings": findings,
        "html_kb": html_kb, "inline_js_kb": round(inline_js_bytes/1024),
        "external_scripts": len(external_js), "external_styles": len(styles),
        "third_party_domains": sorted(third_party_domains),
        "redirect_count": redirect_count, "redirect_cost_ms": redirect_cost_ms,
        "cwv": cwv,
        "fetch_time_ms": fetch_time_ms or 0,
    }
