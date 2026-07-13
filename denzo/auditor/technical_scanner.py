"""
Technical SEO Deep Scan v2 — hyper-detailed on-page analysis.
Checks 40+ signals: titles, meta, headings, schema, OG, security headers,
cache, redirects, images, internal links, cookies, performance indicators.
"""
import re, json
from collections import Counter
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup


def scan_technical(url: str, html: str, domain: str, http_headers: dict = None, status_code: int = None, redirect_chain: list = None) -> dict:
    findings = []
    score = 100
    soup = BeautifulSoup(html, 'html.parser')
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    html_size = len(html)
    text = soup.get_text(separator=' ')
    text_bytes = len(text.encode('utf-8'))
    text_ratio = round(text_bytes / html_size * 100, 1) if html_size > 0 else 0

    headers_lower = {k.lower(): v for k, v in (http_headers or {}).items()}

    # ═════════════════════════════════════════════
    # 0. INDEXABILITY — the checks that override everything else
    #    A page that is noindexed / non-200 / blocked cannot rank at all,
    #    so these run first and carry the heaviest penalties.
    # ═════════════════════════════════════════════

    # 0a. HTTP status code
    if status_code and status_code >= 400:
        findings.append({"severity":"critical","module":"technical","title":f"Page returns HTTP {status_code} — not indexable","detail":f"The URL responded with {status_code}. Search engines drop 4xx/5xx pages from the index. Any SEO work on this URL is wasted until the status is fixed.","fix":"Return HTTP 200 for canonical content URLs. Fix server errors (5xx) or broken routes (4xx). If the page moved, 301 to the new location instead of serving an error.","impact":"Complete deindexation. 100% organic traffic loss for this URL."})
        score -= 40
    elif status_code and 300 <= status_code < 400:
        findings.append({"severity":"high","module":"technical","title":f"Page returns a {status_code} redirect at the canonical URL","detail":f"The requested URL responded with {status_code} instead of serving content directly. The audited HTML is from the redirect target.","fix":"Serve 200 content at the canonical URL. Reserve redirects for URLs that genuinely moved.","impact":"Redirect latency + potential signal dilution."})
        score -= 8

    # 0b. HTTPS scheme
    if parsed.scheme != 'https':
        findings.append({"severity":"critical","module":"technical","title":"Page served over HTTP, not HTTPS","detail":"The URL is not using HTTPS. HTTPS is a confirmed Google ranking signal since 2014 and browsers flag HTTP pages as 'Not Secure', destroying trust and conversions.","fix":"Install a TLS certificate (free via Let's Encrypt / auto on Vercel, Netlify, Cloudflare) and 301-redirect all HTTP URLs to HTTPS.","impact":"Ranking penalty + 'Not Secure' warning shown to every visitor."})
        score -= 20

    # 0c. Meta robots noindex / nofollow (the single most common catastrophic SEO bug)
    meta_robots = soup.find('meta', attrs={'name': lambda v: v and v.lower() == 'robots'})
    meta_robots_content = (meta_robots.get('content','').lower() if meta_robots and meta_robots.get('content') else '')
    x_robots = headers_lower.get('x-robots-tag', '').lower()
    if 'noindex' in meta_robots_content or 'noindex' in x_robots:
        where = 'meta robots tag' if 'noindex' in meta_robots_content else 'X-Robots-Tag HTTP header'
        findings.append({"severity":"critical","module":"technical","title":f"Page is set to NOINDEX (via {where}) — excluded from Google","detail":f"Found '{meta_robots_content or x_robots}'. This directive tells search engines NOT to index the page. If this is a page you want to rank, it is invisible in search regardless of every other optimization.","fix":"Remove 'noindex' from the robots meta tag and/or the X-Robots-Tag header for pages that should rank. In Next.js check the metadata `robots` field / middleware. Verify with Google Search Console URL Inspection.","impact":"The page cannot appear in Google at all. This overrides every other ranking factor."})
        score -= 40
    elif 'nofollow' in meta_robots_content:
        findings.append({"severity":"medium","module":"technical","title":"Page-level 'nofollow' robots directive","detail":"A page-wide nofollow prevents link equity from flowing through any link on this page, weakening internal linking.","fix":"Remove 'nofollow' from the robots meta tag unless intentionally sculpting crawl on a private page."})
        score -= 6

    # 0d. Mixed content on HTTPS pages
    if parsed.scheme == 'https':
        insecure = re.findall(r'(?:src|href)=["\']http://[^"\']+', html, re.IGNORECASE)
        insecure = [u for u in insecure if 'http://www.w3.org' not in u and 'http://schema.org' not in u]
        if len(insecure) >= 3:
            findings.append({"severity":"medium","module":"technical","title":f"Mixed content: {len(insecure)} insecure http:// resources on an HTTPS page","detail":"Loading http:// assets on an https:// page triggers browser mixed-content blocking and 'Not fully secure' warnings.","fix":"Update all asset URLs (images, scripts, styles) to https:// or protocol-relative. Add a Content-Security-Policy: upgrade-insecure-requests header."})
            score -= 5

    # 0e. <html lang> attribute
    html_tag = soup.find('html')
    if not (html_tag and html_tag.get('lang')):
        findings.append({"severity":"low","module":"technical","title":"Missing lang attribute on <html>","detail":"The <html> element has no lang attribute. This helps search engines and screen readers determine the page language and is used for correct hreflang handling.","fix":'Add the language to the root element, e.g. <html lang="en"> or <html lang="es">.'})


    # ═════════════════════════════════════════════
    # 1. TITLE TAG
    # ═════════════════════════════════════════════
    title_tag = soup.find('title')
    title = title_tag.get_text(strip=True) if title_tag else ''
    title_len = len(title)

    if not title:
        findings.append({"severity":"critical","module":"technical","title":"Missing <title> tag — most critical on-page element","detail":"No title tag found. This is the #1 on-page ranking factor. Without it, Google will auto-generate a title, often poorly.","fix":"Add to <head>: <title>[Primary Service] | [City] | [Business Name]</title>. Include primary keyword near the beginning, business name, and location.","impact":"Direct ranking loss for ALL target keywords. Estimated traffic impact: -20 to -40%."})
        score -= 30
    elif title_len < 30:
        findings.append({"severity":"high","module":"technical","title":f"Title too short: {title_len} chars — wasting SERP real estate","detail":f'Current: "{title}". Google displays 50-60 characters in desktop SERPs and 55-65 on mobile. At {title_len} chars, you are using less than half the available space.','fix':f'Expand to 50-60 chars. Recommended: "[Primary Service] | [City], CA | [Business Name]". Front-load primary keyword.','impact':'Reduced CTR in SERPs. Less keyword coverage. Estimated traffic loss: 10-15%.'})
        score -= 12
    elif title_len > 70:
        findings.append({"severity":"medium","module":"technical","title":f"Title too long: {title_len} chars — will be truncated","detail":f'Current: "{title[:100]}...". Google truncates titles at ~600px (55-65 chars on mobile, 65-75 on desktop). Excess characters are replaced with "...".','fix':'Trim to 50-60 characters. Remove filler words. Put the most important keywords first.','impact':'Truncated titles lose keywords and CTR. Estimated traffic loss: 3-8%.'})
        score -= 5
    else:
        findings.append({"severity":"pass","module":"technical","title":f"Title tag: {title_len} chars — optimal length","detail":f'"{title}"','fix':None})

    # Check title descriptiveness (industry-agnostic — no hardcoded cities/keywords)
    title_lower = title.lower()
    # Brand detection: try domain name first, then extract from title prefix (before separators)
    domain_brand = domain.replace('www.','').split('.')[0]
    title_brand = title.split('|')[0].split('—')[0].split('·')[0].split(' - ')[0].strip().lower()
    has_brand = domain_brand in title_lower or (len(title_brand) > 3 and title_brand in title_lower)
    word_n = len(title.split())
    if word_n < 3:
        findings.append({"severity":"medium","module":"technical","title":f"Title not descriptive enough: only {word_n} word(s)","detail":f'Current: "{title}". A strong title communicates the page topic plus the brand. Very short titles waste SERP space and topical relevance.',"fix":"Expand to a descriptive phrase: primary topic/keyword + brand. Add location only if the business is location-based."})
        score -= 5
    elif not has_brand:
        findings.append({"severity":"low","module":"technical","title":"Brand name not detected in title","detail":"Including the brand name in the title reinforces entity recognition and improves branded CTR. (Skip this if you intentionally omit branding.)","fix":"Consider appending the brand: \"[Primary topic] | [Brand]\"."})

    # ═════════════════════════════════════════════
    # 2. META DESCRIPTION
    # ═════════════════════════════════════════════
    meta_desc = soup.find('meta', attrs={'name': 'description'})
    desc = meta_desc['content'].strip() if meta_desc and meta_desc.get('content') else ''
    desc_len = len(desc)

    if not desc:
        findings.append({"severity":"high","module":"technical","title":"Missing meta description","detail":"No meta description tag. Google will auto-generate a snippet from page content, which may be poorly formatted, cut off mid-sentence, or lack a call to action.","fix":"Add: <meta name=\"description\" content=\"[150-160 char description with keyword, value proposition, and CTA]\">. Front-load key info — mobile truncates at ~120 chars.","impact":"Reduced CTR from SERPs. Estimated click loss: 5-15%."})
        score -= 12
    elif desc_len < 120:
        findings.append({"severity":"medium","module":"technical","title":f"Meta description too short: {desc_len} chars","detail":f'Current: "{desc}". Wasting ~35% of available SERP space.','fix':'Expand to 150-160 characters. Include: primary keyword, 2-3 value propositions, location, and a CTA like "Call [phone]" or "Free estimate".'})
        score -= 6
    elif desc_len > 165:
        findings.append({"severity":"low","module":"technical","title":f"Meta description too long: {desc_len} chars — truncated in SERPs","detail":'Google truncates at ~155-160 chars on desktop and ~120 on mobile. Content after the cutoff is invisible.','fix':'Trim to 150-160 chars. Ensure the CTA and key value proposition are in the first 120 characters for mobile visibility.'})
    _cta_words = ('call','free','get','buy','shop','book','try','start','learn','discover','contact','order','sign up','signup','request','download','explore','compare','save','find',
                  # Spanish CTAs
                  'agendá','agenda','llama','whatsapp','gratis','cotización','presupuesto','reserva','cita','consulta','pedir','solicitar','comprar','probar','contacto','descargar')
    if desc and not any(w in desc.lower() for w in _cta_words):
        findings.append({"severity":"low","module":"technical","title":"Meta description lacks a call-to-action","detail":"A CTA in the meta description increases CTR by 2-5%. The current description has no action-oriented language.","fix":'Add a clear CTA appropriate to your business (e.g. "Get started", "Book a demo", "Shop now", "Request a quote").'})

    # ═════════════════════════════════════════════
    # 3. CANONICAL
    # ═════════════════════════════════════════════
    canonical = soup.find('link', rel='canonical')
    canonical_url = canonical['href'].strip() if canonical and canonical.get('href') else None
    current_url = url.rstrip('/')
    if not canonical_url:
        findings.append({"severity":"high","module":"technical","title":"No canonical URL tag — duplicate content risk","detail":"Without a canonical, Google may index multiple URL variations (http/https, www/non-www, with/without trailing slash, with/without parameters) as separate pages. This splits ranking signals.","fix":'Add to <head>: <link rel="canonical" href="https://www.' + domain + parsed.path.rstrip('/') + '">. Ensure ALL internal links, sitemap URLs, and the canonical tag use the SAME domain format (www or non-www).','impact':'Potential duplicate content. Diluted PageRank across multiple URL versions. Estimated ranking dilution: 10-25%.'})
        score -= 15
    elif canonical_url.rstrip('/') != current_url:
        c_domain = urlparse(canonical_url).netloc.replace('www.','')
        if c_domain != domain:
            findings.append({"severity":"critical","module":"technical","title":f"Domain mismatch: canonical uses {urlparse(canonical_url).netloc} but current URL is {parsed.netloc}","detail":f"Canonical: {canonical_url}\nCurrent: {url}\nThis is a conflicting signal. Google must choose which to trust. If the sitemap also uses a different domain, the conflict is severe.","fix":"Align canonical domain with sitemap domain. Choose www or non-www and use it consistently everywhere: canonical tags, sitemap, internal links, robots.txt.","impact":"Severe canonical dilution. Google is receiving contradictory signals from multiple sources."})
            score -= 25

    # ═════════════════════════════════════════════
    # 4. H1 / HEADINGS
    # ═════════════════════════════════════════════
    h1_tags = soup.find_all('h1')
    h1_count = len(h1_tags)
    h2_tags = soup.find_all('h2')
    h3_tags = soup.find_all('h3')

    if h1_count == 0:
        findings.append({"severity":"high","module":"technical","title":"Missing H1 tag","detail":"Every page should have exactly one H1 containing the primary keyword. H1 is a top-3 on-page ranking signal.","fix":"Add a single <h1> containing primary keyword + location. Include your primary keyword + location (if local): <h1>[Primary Service] — [City], CA</h1>. Place it above the fold.","impact":"Weakened topical relevance signal. Estimated ranking impact: 5-15% for primary keywords."})
        score -= 15
    elif h1_count > 1:
        h1_texts = [h.get_text(strip=True) for h in h1_tags]
        findings.append({"severity":"medium","module":"technical","title":f"Multiple H1 tags ({h1_count}): {h1_texts[:3]}","detail":"Best practice is ONE H1 per page. Multiple H1s dilute the primary topic signal and confuse screen readers.","fix":"Consolidate to a single H1. If the design requires multiple large headings, use H2 with styling instead."})
        score -= 8

    # H2 duplicates
    h2_texts = [h.get_text(strip=True).lower() for h in h2_tags]
    h2_counts = Counter(h2_texts)
    h2_dupes = sorted(t for t, c in h2_counts.items() if c > 1)
    if h2_dupes:
        findings.append({"severity":"high","module":"technical","title":f"Duplicate H2 headings: {len(h2_dupes)} repeated","detail":f'Repeated H2s: {h2_dupes}. Duplicate headings confuse Google\'s content structure analysis and waste heading real estate.','fix':'Ensure each H2 is unique and describes a distinct section. Merge or delete duplicate sections.','impact':'Diluted content structure signals. Google may not understand page organization.'})
        score -= 10

    # Check for skipped heading levels
    if h1_count > 0 and len(h2_tags) == 0:
        findings.append({"severity":"medium","module":"technical","title":"Heading hierarchy skip: H1 present but no H2s","detail":"Headings should form a logical hierarchy (H1 → H2 → H3). Skipping levels makes content harder to parse for both users and search engines.","fix":"Wrap major content sections in H2 tags. Each H2 should cover a distinct topic (Services, Locations, About, FAQ, etc.)."})
        score -= 5

    # ═════════════════════════════════════════════
    # 5. SCHEMA / JSON-LD DEEP VALIDATION
    # ═════════════════════════════════════════════
    schema_scripts = soup.find_all('script', type='application/ld+json')
    schema_types = []
    schema_details = []
    schema_issues = []

    required_types = {
        'LocalBusiness': ['name','address','telephone','url'],
        'Organization': ['name','url'],
        'BreadcrumbList': ['itemListElement'],
        'FAQPage': ['mainEntity'],
        'Service': ['name','provider'],
        'WebSite': ['url'],
        'Product': ['name'],
    }

    for i, s in enumerate(schema_scripts):
        try:
            data = json.loads(s.string) if s.string else {}
            items = []
            if isinstance(data, dict):
                if '@graph' in data:
                    items = data['@graph']
                    type_names = []
                    for item in items:
                        if isinstance(item, dict):
                            t = item.get('@type','?')
                            if isinstance(t, list): t = ', '.join(t)
                            type_names.append(str(t))
                    findings.append({"severity":"info","module":"technical","title":f"@graph schema block #{i+1} with {len(items)} nodes","detail":f"Types: {', '.join(type_names)}","fix":None})
                elif '@type' in data:
                    items = [data]
            elif isinstance(data, list):
                items = data

            for item in items:
                if not isinstance(item, dict): continue
                stype = item.get('@type','')
                if isinstance(stype, list): stype = stype[0] if stype else ''
                if stype:
                    schema_types.append(stype)
                    # Validate required fields
                    if stype in required_types:
                        missing = [f for f in required_types[stype] if f not in item or not item[f]]
                        if missing:
                            schema_issues.append(f"{stype}: missing {', '.join(missing)}")

                    # Check for geo coordinates on LocalBusiness
                    if 'LocalBusiness' in stype or 'AutoBodyShop' in stype or 'AutoRepair' in stype:
                        has_geo = 'geo' in item and item['geo'] and item['geo'].get('latitude') and item['geo'].get('longitude')
                        if not has_geo:
                            schema_issues.append(f"{stype}: missing geo coordinates (blocks Google Maps & Local Pack)")

                        # Check for aggregateRating
                        if 'aggregateRating' not in item:
                            schema_issues.append(f"{stype}: missing aggregateRating (no star ratings in SERPs)")

                        addr = item.get('address',{})
                        if isinstance(addr, dict):
                            if not addr.get('streetAddress'):
                                schema_issues.append(f"{stype}: address missing streetAddress")

            loc_count = sum(1 for it in items if isinstance(it, dict) and ('LocalBusiness' in str(it.get('@type','')) or 'AutoBodyShop' in str(it.get('@type','')) or 'AutoRepair' in str(it.get('@type',''))))
            schema_details.append(f"{loc_count} LocalBusiness/AutoBodyShop nodes found")

        except (json.JSONDecodeError, AttributeError) as e:
            schema_issues.append(f"Block #{i+1}: invalid JSON — {str(e)[:100]}")

    if len(schema_scripts) == 0:
        findings.append({"severity":"critical","module":"technical","title":"ZERO structured data — invisible to rich results & AI","detail":"No JSON-LD schema found. The site cannot appear in: Google Local Pack, rich snippets, Knowledge Panel, AI Overviews citations, or voice search results. For a multi-location business, this is devastating.","fix":"Implement: (1) Organization schema on homepage, (2) LocalBusiness schema for EACH location with full NAP + geo coordinates, (3) Service schema for each service, (4) BreadcrumbList, (5) WebSite schema for Sitelinks Searchbox.","impact":"Estimated traffic loss from rich results: 30-50%. Each location missing LocalBusiness schema is invisible in Google Maps search."})
        score -= 30
    else:
        if schema_issues:
            for issue in schema_issues[:5]:
                findings.append({"severity":"high","module":"technical","title":f"Schema validation issue: {issue}","detail":"Schema is present but incomplete — search engines may not use it for rich results.","fix":"Add the missing required properties. Use Google's Rich Results Test to validate: https://search.google.com/test/rich-results"})
                score -= 8
        else:
            findings.append({"severity":"pass","module":"technical","title":f"Schema valid: {len(schema_scripts)} blocks, {len(set(schema_types))} unique types","detail":f"Types: {', '.join(sorted(set(schema_types)))}. Well-structured for rich results.","fix":None})

        # Check FAQPage deprecation warning (does NOT apply to healthcare/government sites)
        if 'FAQPage' in schema_types:
            is_healthcare = any(t in schema_types for t in ['MedicalBusiness','DiagnosticLab','MedicalClinic','Hospital','Physician','Dentist','HealthAndBeautyBusiness','MedicalOrganization'])
            is_government = any(t in schema_types for t in ['GovernmentOrganization','GovernmentOffice','PoliceStation','CityHall'])
            if is_healthcare or is_government:
                findings.append({"severity":"pass","module":"technical","title":"FAQPage schema present — eligible for rich results (healthcare/government site)","detail":"Since August 2023, Google reserves FAQ rich results for government and healthcare sites. This site qualifies because it has healthcare/government schema types. FAQ schema is beneficial here.","fix":None})
            else:
                findings.append({"severity":"info","module":"technical","title":"FAQPage schema present — note Google restriction","detail":"Since August 2023, Google only shows FAQ rich results for government and healthcare sites. This site appears to be commercial, so FAQPage schema will NOT generate rich results.","fix":"Consider removing FAQPage schema. Instead, render FAQ as visible HTML for AI/GEO citation value without the schema."})

    # ═════════════════════════════════════════════
    # 6. OPEN GRAPH / SOCIAL CARDS
    # ═════════════════════════════════════════════
    og_tags = {}
    for prop in ['title','description','image','url','type','site_name','locale']:
        tag = soup.find('meta', property=f'og:{prop}') or soup.find('meta', attrs={'name': f'og:{prop}'})
        og_tags[prop] = tag['content'].strip() if tag and tag.get('content') else None

    missing_og = [k for k,v in og_tags.items() if not v]
    if 'title' in missing_og or 'image' in missing_og:
        findings.append({"severity":"high","module":"technical","title":f"Critical Open Graph tags missing: {', '.join(missing_og)}","detail":"Without og:title and og:image, links shared on Facebook, LinkedIn, WhatsApp, iMessage, and Slack display as plain text with no preview. Social shares drive brand visibility and indirect SEO signals.","fix":"Add in <head>:\n<meta property=\"og:title\" content=\"[Business Name] | [Tagline]\">\n<meta property=\"og:description\" content=\"[150-160 char description]\">\n<meta property=\"og:image\" content=\"https://www.[domain]/og-image.jpg\">\n<meta property=\"og:url\" content=\"[canonical URL]\">\n<meta property=\"og:type\" content=\"website\">\n\nCreate a 1200×630px JPG social share image.","impact":"Links shared on social platforms appear as plain text — no image, no description. Estimated indirect traffic loss: 3-8%."})
        score -= 15
    elif og_tags.get('image'):
        findings.append({"severity":"pass","module":"technical","title":"Open Graph tags: complete","detail":f"og:image = {og_tags['image'][:80]}","fix":None})

    # Twitter card
    twitter_card = soup.find('meta', attrs={'name':'twitter:card'})
    if not twitter_card:
        findings.append({"severity":"low","module":"technical","title":"Missing Twitter Card tags","detail":"Without twitter:card, shares on X/Twitter won't render a summary card with image.","fix":"Add: <meta name=\"twitter:card\" content=\"summary_large_image\">\n<meta name=\"twitter:title\" content=\"...\">\n<meta name=\"twitter:description\" content=\"...\">\n<meta name=\"twitter:image\" content=\"...\">"})

    # ═════════════════════════════════════════════
    # 7. HTML SEMANTICS & RATIO
    # ═════════════════════════════════════════════
    if text_ratio < 5:
        findings.append({"severity":"high","module":"technical","title":f"Severely low text-to-HTML ratio: {text_ratio}%","detail":f"HTML: {html_size/1024:.0f}KB | Visible text: {text_bytes/1024:.1f}KB | Ratio: {text_ratio}%. Google expects 10-25% for a content-rich page. Below 5% triggers thin content filters regardless of actual word count.","fix":"Reduce inline JavaScript (move to external files with defer/async). Remove unnecessary wrapper divs. Increase visible text content by 50-100%.","impact":"Risk of being classified as thin content. Estimated ranking suppression: 5-15% across all terms."})
        score -= 15
    elif text_ratio < 10:
        findings.append({"severity":"medium","module":"technical","title":f"Below-average text-to-HTML ratio: {text_ratio}%","detail":f"Target 10-25%. At {text_ratio}%, the page is markup-heavy. This is common in React/Next.js SPAs due to hydration payloads.","fix":"Enable code splitting in Next.js. Use Partial Prerendering (PPR). Reduce RSC payload size. Increase text content."})
        score -= 6

    # Semantic tags
    semantic = {t:len(soup.find_all(t)) for t in ['main','article','section','nav','header','footer','aside']}
    if semantic['main'] == 0:
        findings.append({"severity":"low","module":"technical","title":"No <main> element — missing semantic landmark","detail":"<main> identifies the primary content area for accessibility and search engine content extraction.","fix":"Wrap primary content in <main> tag. This also helps AI/LLM content extraction."})
    if semantic['article'] == 0:
        findings.append({"severity":"low","module":"technical","title":"No <article> tags — missing content semantics","detail":"<article> tags help search engines and AI models identify self-contained content pieces for citation.","fix":"Use <article> for blog posts, service descriptions, and location content blocks."})

    # ═════════════════════════════════════════════
    # 8. IMAGES — DETAILED AUDIT
    # ═════════════════════════════════════════════
    images = soup.find_all('img')
    img_data = []
    total_img_bytes = 0
    for img in images:
        src = img.get('src','') or img.get('data-src','')
        alt = img.get('alt','')
        w = img.get('width')
        h = img.get('height')
        loading = img.get('loading','')
        _path = urlparse(src).path.lower()
        _ext = _path[_path.rfind('.'):] if '.' in _path else ''
        fmt_map = {'.webp': 'webp', '.avif': 'avif', '.png': 'png', '.jpg': 'jpg', '.jpeg': 'jpg', '.svg': 'svg', '.gif': 'gif'}
        fmt = fmt_map.get(_ext, 'other')
        # Try to get file size from next/image or srcset
        img_data.append({'src':src[:120],'alt':alt,'w':w,'h':h,'lazy':loading=='lazy','fmt':fmt,'has_dims':bool(w and h)})

    imgs_no_alt = [i for i in img_data if not i['alt']]
    imgs_no_dims = [i for i in img_data if not i['has_dims']]
    imgs_lazy = [i for i in img_data if i['lazy']]
    imgs_webp = [i for i in img_data if i['fmt'] in ('webp','avif')]
    imgs_png = [i for i in img_data if i['fmt'] == 'png']
    imgs_jpg = [i for i in img_data if i['fmt'] == 'jpg']

    if imgs_no_alt:
        pct = round(len(imgs_no_alt)/len(images)*100) if images else 0
        findings.append({"severity":"medium","module":"technical","title":f"{len(imgs_no_alt)}/{len(images)} images ({pct}%) missing alt text","detail":f"Examples: {[i['src'][:60] for i in imgs_no_alt[:3]]}. Alt text is essential for accessibility (WCAG), image SEO, and Google Images traffic.","fix":"Add descriptive alt text to every <img>. For Next.js: <Image alt=\"Descriptive text\" .../>. Alt text should describe the image content, not keyword stuff."})
        score -= 8

    if len(imgs_png) >= 5 and len(imgs_png) > len(images) * 0.3:
        findings.append({"severity":"medium","module":"technical","title":f"{len(imgs_png)} images still in PNG format — convert to WebP","detail":f"PNG images: {[i['src'][:50] for i in imgs_png[:5]]}. WebP is 30-50% smaller than PNG with equivalent quality. Large PNGs are the #1 cause of excessive page weight.","fix":"Convert PNG images to WebP/AVIF. In Next.js, the <Image> component auto-converts. For static images, use tools like cwebp or Sharp. Serve responsive sizes via srcSet.","impact":"Estimated page weight savings: 40-60% on image payload. Direct LCP improvement."})
        score -= 8

    if len(images) >= 3 and len(imgs_lazy) / len(images) < 0.6:
        findings.append({"severity":"low","module":"technical","title":f"Only {len(imgs_lazy)}/{len(images)} images lazy-loaded","detail":"Lazy loading defers off-screen images, reducing initial page weight and improving LCP.","fix":"Add loading=\"lazy\" to below-fold <img> tags. In Next.js: <Image loading=\"lazy\" .../>. Above-fold and logo images should NOT be lazy-loaded (harms LCP)."})

    if len(imgs_no_dims) > 5:
        findings.append({"severity":"medium","module":"technical","title":f"{len(imgs_no_dims)} images missing explicit width/height — CLS risk","detail":f"Without dimensions: {[i['src'][:50] for i in imgs_no_dims[:5]]}. Images without width/height cause Cumulative Layout Shift as they load and push content around.","fix":"Add width/height attributes. In Next.js, use <Image width={...} height={...}> or fill mode with parent container sizing.","impact":"CLS (Cumulative Layout Shift) penalty. Google penalizes CLS > 0.1 in Core Web Vitals."})
        score -= 7

    # ═════════════════════════════════════════════
    # 9. INTERNAL LINKS
    # ═════════════════════════════════════════════
    links = soup.find_all('a', href=True)
    internal = 0
    external = 0
    link_domains = {}
    for link in links:
        href = link.get('href','')
        if href.startswith('#') or href.startswith('javascript') or href.startswith('tel:') or href.startswith('mailto:'):
            continue
        parsed_href = urlparse(href)
        link_domain = parsed_href.netloc.replace('www.','')
        if not link_domain or link_domain == domain:
            internal += 1
        else:
            external += 1
            link_domains[link_domain] = link_domains.get(link_domain,0) + 1

    if internal < 10:
        findings.append({"severity":"medium","module":"technical","title":f"Very few internal links: {internal}","detail":"Internal links distribute PageRank and help Google understand site architecture. Pages with <10 internal links may be orphaned or undervalued.","fix":"Add contextual internal links to key pages: location pages, service pages, about page, contact page. Target 30-50 internal links for a homepage."})
        score -= 6

    # ═════════════════════════════════════════════
    # 10. SECURITY & HTTP HEADERS
    # ═════════════════════════════════════════════
    if http_headers:
        headers_lower = {k.lower():v for k,v in http_headers.items()}
        security_checks = {
            'content-security-policy': ('Content Security Policy','Protects against XSS, data injection, and supply chain attacks. Critical for sites with user forms.'),
            'strict-transport-security': ('HSTS','Forces HTTPS. Already properly configured (good).') if 'strict-transport-security' in headers_lower else ('HSTS','Forces HTTPS connections. Recommended max-age=63072000 (2 years).'),
            'x-content-type-options': ('X-Content-Type-Options','Prevents MIME type sniffing. Set to "nosniff".'),
            'x-frame-options': ('X-Frame-Options','Prevents clickjacking. Set to "DENY" or "SAMEORIGIN".'),
            'referrer-policy': ('Referrer Policy','Controls referrer information leakage. Recommended: "strict-origin-when-cross-origin".'),
            'permissions-policy': ('Permissions Policy','Restricts browser features (camera, mic, etc). Recommended for security hardening.'),
        }

        cache = headers_lower.get('cache-control','')
        if 'no-store' in cache:
            findings.append({"severity":"high","module":"technical","title":"Cache-Control: no-store — zero browser caching","detail":f"Current: {cache}. Every visit re-downloads the full page. This disables browser cache, CDN edge cache, and Back/Forward cache (bfcache).","fix":"For content pages, use: Cache-Control: public, s-maxage=60, stale-while-revalidate=3600. For static assets, use longer max-age. This alone can reduce LCP by 1-2 seconds on repeat visits.","impact":"Every page load is a cold load. TTFB inflated by 500-700ms. bfcache disabled. Estimated LCP penalty: 1-3s."})
            score -= 12

        for header_key, (display_name, description) in security_checks.items():
            if header_key not in headers_lower:
                # Security headers are best practices, NOT confirmed SEO ranking factors.
                # We report them as informational only — no score impact.
                findings.append({"severity":"info","module":"technical","title":f"Missing {display_name} header (security best practice, not an SEO ranking factor)","detail":description,"fix":f"Add {display_name} header to server configuration (Vercel: vercel.json headers, Next.js: next.config.js headers() function)."})

        # Check for ETag / Last-Modified
        if 'etag' not in headers_lower and 'last-modified' not in headers_lower:
            findings.append({"severity":"medium","module":"technical","title":"No ETag or Last-Modified — conditional requests disabled","detail":"Without cache validation headers, browsers cannot validate cached copies with a 304 Not Modified response. Every request is a full download.","fix":"Enable ETag headers (Vercel generates these automatically for static files). For dynamic pages, generate ETags from content hash."})
            score -= 5

    # ═════════════════════════════════════════════
    # 11. PERFORMANCE INDICATORS
    # ═════════════════════════════════════════════
    inline_scripts = soup.find_all('script')
    external_scripts = [s for s in inline_scripts if s.get('src')]
    inline_js = [s for s in inline_scripts if not s.get('src') and s.string]
    inline_css = soup.find_all('style')

    if len(external_scripts) > 8:
        findings.append({"severity":"medium","module":"technical","title":f"{len(external_scripts)} external script files — excessive HTTP requests","detail":f"Each external JS file requires a separate HTTP request. While HTTP/2 multiplexes, too many bundles increase parse time and main-thread blocking.","fix":"Bundle JavaScript into fewer files. Use code splitting per-route in Next.js. Remove unused dependencies. Enable Turbopack."})
        score -= 6

    if len(inline_js) > 5:
        inline_js_size = sum(len(s.string or '') for s in inline_js)
        if inline_js_size > 100000:
            findings.append({"severity":"high","module":"technical","title":f"Massive inline JavaScript: {inline_js_size/1024:.0f}KB in {len(inline_js)} blocks","detail":"This is Next.js RSC (React Server Components) hydration payload. It blocks rendering and increases TBT (Total Blocking Time) significantly.","fix":"Enable Partial Prerendering (PPR) in Next.js 14+. This serves static HTML shells with dynamic 'holes' that hydrate progressively. Lazy-load below-fold components.","impact":"Estimated TBT impact: +500-1500ms. Direct LCP and INP penalty in Core Web Vitals."})
            score -= 10

    # ═════════════════════════════════════════════
    # 12. WORD COUNT & CONTENT DEPTH
    # ═════════════════════════════════════════════
    words = len(text.split())
    if words < 500:
        findings.append({"severity":"high","module":"technical","title":f"Severely thin content: {words} words","detail":"Pages with <500 words are considered 'thin content' by Google and struggle to rank. The average top-10 result has 1,500-2,500 words.","fix":"Expand to 1,500+ words. Structure with H2 sections covering: detailed service/product descriptions, FAQ, about/credentials, testimonials, process, and location info if local.","impact":"Cannot compete for mid-to-high difficulty keywords. Estimated ranking ceiling: position 20+."})
        score -= 15
    elif words < 1500:
        findings.append({"severity":"medium","module":"technical","title":f"Below-competitive word count: {words}","detail":f"Top-ranking pages average 1,500-2,500 words. At {words}, you are below the competitive threshold.","fix":"Add 500-1,000 more words. Best ROI: FAQ section, detailed service/product descriptions, location-specific content, and credentials/certifications."})
        score -= 6

    # ═════════════════════════════════════════════
    # 13. MOBILE / VIEWPORT
    # ═════════════════════════════════════════════
    viewport = soup.find('meta', attrs={'name':'viewport'})
    if not viewport:
        findings.append({"severity":"critical","module":"technical","title":"Missing viewport meta tag — not mobile-friendly","detail":"Without a viewport meta tag, mobile browsers render the page at desktop width, forcing users to pinch-zoom. Google uses mobile-first indexing — this directly hurts rankings.","fix":'Add to <head>: <meta name="viewport" content="width=device-width, initial-scale=1.0">'})
        score -= 20
    else:
        content = viewport.get('content','')
        if 'user-scalable=no' in content:
            findings.append({"severity":"low","module":"technical","title":"Viewport disables user scaling — accessibility concern","detail":"user-scalable=no prevents users from zooming, which is an accessibility violation (WCAG 1.4.4).","fix":"Remove user-scalable=no or set to user-scalable=yes."})

    charset = soup.find('meta', attrs={'charset':True}) or soup.find('meta', attrs={'http-equiv':lambda v: v and v.lower()=='content-type'})
    if not charset:
        findings.append({"severity":"medium","module":"technical","title":"Missing charset declaration","detail":"Explicit charset prevents encoding-related rendering issues.","fix":'Add as first element in <head>: <meta charset="UTF-8">'})
        score -= 3

    # ═════════════════════════════════════════════
    # 14. STRUCTURED LISTS
    # ═════════════════════════════════════════════
    ul_count = len(soup.find_all('ul'))
    ol_count = len(soup.find_all('ol'))
    li_count = len(soup.find_all('li'))
    if li_count == 0:
        findings.append({"severity":"high","module":"technical","title":"Zero HTML list elements (ul/ol) — poor scannability","detail":"Structured lists improve readability and are one of the most commonly cited formats in Google AI Overviews. 0 list items = near-zero chance of appearing in featured snippets or AI Overviews for list-type queries.","fix":"Add structured lists: (1) services/products with short descriptions, (2) locations if multi-site, (3) certifications/credentials, (4) process steps in <ol>, (5) key differentiators.","impact":"Missed opportunity for featured snippets. Estimated traffic loss from quick-answer queries: 10-20%."})
        score -= 12

    # ═════════════════════════════════════════════
    # 15. CLIENT-SIDE RENDERING (SPA) DETECTION
    #     Warn when the audited HTML is a pre-hydration shell: content injected
    #     by JS won't be in this HTML, so content/schema/heading checks above may
    #     produce false negatives. This makes the report honest about its input.
    # ═════════════════════════════════════════════
    _spa_markers = ('__NEXT_DATA__', 'id="__next"', 'data-reactroot', 'ng-version=',
                    'data-server-rendered', 'window.__NUXT__', 'id="app"')
    _is_spa = any(m in html for m in _spa_markers)
    if _is_spa and words < 300 and text_ratio < 8:
        findings.append({"severity":"high","module":"technical","title":"Content appears to be client-side rendered (JS) — audit sees a near-empty shell","detail":f"Framework markers detected with very little text in the initial HTML ({words} words, {text_ratio}% text ratio). Search engines render JS, but many AI crawlers and social scrapers do NOT. Critically, on-page checks above (content, schema, headings) may report false negatives because the real content is injected after load.","fix":"Serve meaningful HTML on first response: use SSR/SSG/ISR (Next.js) or prerendering so title, headings, primary copy and JSON-LD exist in the raw HTML. Re-run this audit with JS rendering enabled (DENZO_RENDER_JS) for an accurate content picture.","impact":"AI/GEO invisibility for non-rendering crawlers and unreliable audit signals until fixed."})
        score -= 8

    return {
        "score": max(0, score),
        "findings": findings,
        "title": title, "title_len": title_len,
        "meta_description": desc, "meta_description_len": desc_len,
        "canonical_url": canonical_url,
        "h1_count": h1_count, "h2_count": len(h2_tags), "h3_count": len(h3_tags),
        "h2_duplicates": len(h2_dupes),
        "schema_blocks": len(schema_scripts), "schema_types": list(set(schema_types)),
        "schema_issues": len(schema_issues),
        "og_missing": missing_og,
        "image_count": len(images), "images_no_alt": len(imgs_no_alt),
        "images_no_dims": len(imgs_no_dims), "images_lazy": len(imgs_lazy),
        "images_webp": len(imgs_webp), "images_png": len(imgs_png),
        "ul_count": ul_count, "ol_count": ol_count, "li_count": li_count,
        "word_count": words, "text_html_ratio": text_ratio,
        "html_size_kb": round(html_size/1024),
        "internal_links": internal, "external_links": external,
        "inline_scripts": len(inline_js), "external_scripts": len(external_scripts),
        "has_viewport": bool(viewport),
        "client_rendered": _is_spa,
        "http_status": status_code,
    }
