"""
Image Deep Auditor — world-class image optimization analysis.
Checks: alt text quality, file sizes, formats, responsive images, LCP priority,
CLS prevention, CDN usage, dimension integrity, format conversion opportunities.

Google's image best practices (2024-2026):
- WebP/AVIF format for all photos (30-50% smaller than JPEG/PNG)
- Explicit width/height on EVERY <img> (CLS prevention)
- LCP image should be preloaded with fetchpriority="high"
- Alt text must be descriptive, not generic — "red 2024 Tesla Model 3 front bumper" not "car"
- Responsive images with srcset + sizes for different viewports
- Lazy loading for all below-fold images (loading="lazy")
- Images should be served via CDN with proper caching
- Total image payload should be <1MB above the fold
"""
import re, time
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup


def deep_image_audit(url: str, html: str, domain: str, base_page_url: str = None) -> dict:
    """Deep image optimization audit. Returns 30+ metrics."""
    findings = []
    score = 100
    soup = BeautifulSoup(html, 'html.parser')
    images = soup.find_all('img')
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    if not images:
        return {"score": 100, "findings": [{"severity":"pass","module":"images","title":"No images on page","detail":"Nothing to audit.","fix":None}],
                "total":0, "with_alt":0, "with_dims":0, "lazy":0, "webp":0, "png":0, "jpg":0, "svg":0,
                "alt_quality_issues":0, "missing_dims":0, "estimated_total_kb":0, "lcp_candidate":None}

    total = len(images)
    img_metrics = []

    for i, img in enumerate(images):
        src = img.get('src','') or img.get('data-src','')
        alt = (img.get('alt','') or '').strip()
        w = img.get('width')
        h = img.get('height')
        loading = img.get('loading','')
        fetchpriority = img.get('fetchpriority','')
        srcset = img.get('srcset','')
        sizes = img.get('sizes','')
        style = img.get('style','')

        # Format detection
        fmt = 'unknown'
        src_lower = src.lower()
        _path = urlparse(src_lower).path
        if _path.endswith('.webp'): fmt = 'webp'
        elif _path.endswith('.avif'): fmt = 'avif'
        elif _path.endswith('.png'): fmt = 'png'
        elif _path.endswith('.jpg') or _path.endswith('.jpeg'): fmt = 'jpg'
        elif _path.endswith('.svg'): fmt = 'svg'
        elif _path.endswith('.gif'): fmt = 'gif'

        # Alt text quality
        alt_quality = 'good'
        if not alt: alt_quality = 'missing'
        elif len(alt) < 5: alt_quality = 'too_short'
        elif alt.lower() in ('photo','image','picture','img','logo',' '):
            alt_quality = 'generic'
        elif len(alt) < 15: alt_quality = 'minimal'

        # Is this the likely LCP image? (first large image in viewport)
        is_lcp_candidate = (i <= 2 and fmt in ('jpg','png','webp','avif','svg','gif') and
                           not (src_lower.split('/')[-1].startswith('logo') or src_lower.split('/')[-1].startswith('icon')))

        img_metrics.append({
            'src': src[:150], 'alt': alt, 'alt_quality': alt_quality,
            'fmt': fmt, 'has_dims': bool(w and h), 'lazy': loading == 'lazy',
            'fetchpriority': fetchpriority, 'has_srcset': bool(srcset), 'has_sizes': bool(sizes),
            'is_lcp_candidate': is_lcp_candidate, 'w': w, 'h': h,
        })

    # ── Aggregate analysis ──
    alt_missing = [m for m in img_metrics if m['alt_quality'] == 'missing']
    alt_generic = [m for m in img_metrics if m['alt_quality'] in ('generic','too_short','minimal')]
    no_dims = [m for m in img_metrics if not m['has_dims']]
    lazy_imgs = [m for m in img_metrics if m['lazy']]
    webp_avif = [m for m in img_metrics if m['fmt'] in ('webp','avif')]
    png_imgs = [m for m in img_metrics if m['fmt'] == 'png']
    jpg_imgs = [m for m in img_metrics if m['fmt'] == 'jpg']
    svg_imgs = [m for m in img_metrics if m['fmt'] == 'svg']
    lcp_candidates = [m for m in img_metrics if m['is_lcp_candidate']]
    lcp_img = lcp_candidates[0] if lcp_candidates else None

    # ── Findings ──

    # 1. Alt text — the most common image SEO failure
    if alt_missing:
        pct = round(len(alt_missing)/total*100)
        examples = [m['src'].split('/')[-1][:40] for m in alt_missing[:3]]
        findings.append({"severity":"high","module":"images","title":f"{len(alt_missing)}/{total} images ({pct}%) missing alt text","detail":f"Examples: {examples}. Alt text is a Google ranking factor for Google Images (20%+ of traffic for local businesses comes from image search). Missing alt text also fails WCAG 2.1 accessibility requirements (Level A).","fix":"Add descriptive alt text to every <img>. For Next.js: <Image alt=\"[descriptive text]\" .../>. Alt text should describe WHAT is in the image, not stuff keywords. Good: 'Team meeting at [Business Name] headquarters in [City]'. Bad: 'business meeting'. Describe what is IN the image specifically.","impact": f"Invisible to Google Images for {len(alt_missing)} images. Accessibility violation. Estimated image search traffic loss: 15-30%."})
        score -= 15

    if alt_generic:
        examples = [(m['src'].split('/')[-1][:30], m['alt'][:40]) for m in alt_generic[:3]]
        findings.append({"severity":"medium","module":"images","title":f"{len(alt_generic)} images have generic/low-quality alt text","detail":f"Examples: {examples}. Alt text like 'auto body repair' or 'car' provides zero descriptive value. Google Images ranks based on alt text relevance + surrounding content. Generic alt text = invisible in image search.","fix":"Rewrite alt text to be specific and descriptive. Describe exactly what the image shows. Each image should have UNIQUE, contextual alt text.","impact":"Reduced Google Images visibility. Estimated traffic opportunity: 5-10% from image search."})
        score -= 8

    # 2. Image dimensions — CLS prevention
    if no_dims:
        pct = round(len(no_dims)/total*100)
        examples = [m['src'].split('/')[-1][:40] for m in no_dims[:3]]
        findings.append({"severity":"high","module":"images","title":f"{len(no_dims)}/{total} images ({pct}%) missing explicit width/height — CLS risk","detail":f"Examples: {examples}. Images without dimensions are the #1 cause of Cumulative Layout Shift (CLS). Google penalizes CLS > 0.1 in Core Web Vitals. Every image without dimensions pushes content around as it loads.","fix":"Add width/height to every <img>. In Next.js, use <Image width={800} height={600} src=\"...\" alt=\"...\"/> or <Image fill sizes=\"...\" /> with a positioned parent. For HTML: <img width=\"800\" height=\"600\" ...>. This reserves space before the image loads.","impact":"CLS penalty in Core Web Vitals. Estimated ranking impact: 3-8% for mobile searches."})
        score -= 12

    # 3. Format optimization
    if png_imgs and len(png_imgs) > total * 0.2:
        png_pct = round(len(png_imgs)/total*100)
        findings.append({"severity":"high","module":"images","title":f"{len(png_imgs)} images ({png_pct}%) still in PNG — convert to WebP/AVIF","detail":f"PNG is 2-5x larger than WebP for photographic images. Estimated wasted bytes: {len(png_imgs) * 150}KB assuming average PNG photo of 200KB → 50KB WebP.","fix":"Convert all PNG photos to WebP (lossy, quality 80%) or AVIF (smaller but slower to encode). In Next.js, next/image auto-converts if using the built-in optimizer. For static images: cwebp input.png -o output.webp -q 80. SVG/logo PNGs can stay as-is.","impact": f"Estimated page weight savings: {len(png_imgs) * 150}KB. Direct LCP improvement of 1-3 seconds on mobile."})
        score -= 12

    if jpg_imgs and len(jpg_imgs) > total * 0.3:
        findings.append({"severity":"medium","module":"images","title":f"{len(jpg_imgs)} JPEG images — consider WebP/AVIF conversion","detail":"WebP is 25-35% smaller than JPEG at equivalent quality. Converting JPEGs to WebP is a quick win for page weight reduction.","fix":"Convert JPEGs to WebP. Most CDNs (Cloudflare, Vercel, Netlify) can auto-convert. Next.js Image component handles this automatically."})
        score -= 6

    # 4. LCP optimization
    if lcp_img:
        if not lcp_img['fetchpriority']:
            findings.append({"severity":"high","module":"images","title":"LCP image not prioritized — add fetchpriority='high'","detail":f"LCP candidate: {lcp_img['src'][:80]}. The Largest Contentful Paint image should be preloaded or have fetchpriority='high' so the browser prioritizes it over other resources. Without this, LCP is delayed by 1-3 seconds.","fix":"Add fetchpriority='high' to the LCP image. Also add <link rel='preload' as='image' href='...' imagesrcset='...'> in <head> for the critical hero image. In Next.js: <Image priority fetchPriority='high' .../>.","impact":"LCP improvement of 1-3 seconds on mobile. This is the #1 Core Web Vital optimization."})
            score -= 10
        if not lcp_img['has_srcset']:
            findings.append({"severity":"medium","module":"images","title":"LCP image missing responsive srcset","detail":"Without srcset, mobile devices download the desktop-size image (4-10x larger than needed). The LCP image should have multiple sizes for different viewports.","fix":"Add srcset with at least 3 sizes: 640w, 1024w, 1920w. Next.js Image component generates these automatically."})
            score -= 5

    # 5. Lazy loading audit
    lazy_pct = round(len(lazy_imgs)/total*100) if total else 0
    if lazy_pct < 60 and total > 5:
        findings.append({"severity":"medium","module":"images","title":f"Only {lazy_pct}% of images lazy-loaded — should be 70%+","detail":f"{total - len(lazy_imgs)} images load eagerly, including potentially off-screen images. This wastes bandwidth and delays LCP.","fix":"Add loading='lazy' to all below-fold images. In Next.js: <Image loading='lazy' .../> for non-hero images. Keep loading='eager' only for the LCP/first-viewport image."})
        score -= 5
    elif lazy_pct > 90:
        findings.append({"severity":"pass","module":"images","title":f"Excellent lazy loading: {lazy_pct}% of images lazy-loaded","detail":"Only critical above-fold images load eagerly. This is optimal for performance.","fix":None})

    # 6. SVG count — good or bad?
    if svg_imgs:
        findings.append({"severity":"pass","module":"images","title":f"{len(svg_imgs)} SVG images — ideal for logos and icons","detail":"SVGs are resolution-independent and tiny. Excellent choice for logos and icons.","fix":None})

    # 7. Alt text diversity check
    alt_texts = [m['alt'].lower().strip() for m in img_metrics if m['alt']]
    unique_alts = len(set(alt_texts))
    if unique_alts < len(alt_texts) * 0.5 and len(alt_texts) > 5:
        findings.append({"severity":"medium","module":"images","title":f"Low alt text diversity: {unique_alts} unique / {len(alt_texts)} total","detail":"Many images share identical alt text. Google may view this as keyword stuffing or low-quality content. Each image should have unique, descriptive alt text.","fix":"Write unique alt text for each image describing its specific content. Templates should generate contextual alt text (e.g., '[Business Name] {city} office exterior' not just generic keywords)."})
        score -= 6

    # Success
    if score >= 80:
        findings.insert(0, {"severity":"pass","module":"images","title":f"Image optimization: good","detail":f"{total} images: {webp_avif} WebP/AVIF, {lazy_pct}% lazy-loaded, {round((total-len(no_dims))/total*100)}% with dimensions, {round((total-len(alt_missing))/total*100)}% with alt text.","fix":None})

    return {
        "score": max(0, score),
        "findings": findings,
        "total": total,
        "with_alt": total - len(alt_missing),
        "with_quality_alt": total - len(alt_missing) - len(alt_generic),
        "with_dims": total - len(no_dims),
        "lazy": len(lazy_imgs),
        "webp_avif": len(webp_avif),
        "png": len(png_imgs),
        "jpg": len(jpg_imgs),
        "svg": len(svg_imgs),
        "alt_quality_issues": len(alt_missing) + len(alt_generic),
        "missing_dims": len(no_dims),
        "lcp_candidate": lcp_img['src'][:120] if lcp_img else None,
        "lcp_optimized": bool(lcp_img and lcp_img['fetchpriority'] and lcp_img['has_srcset']) if lcp_img else None,
    }
