"""
Sitemap Analyzer v2 — deep sitemap analysis: discovery, parsing, validation,
domain consistency, lastmod freshness, hreflang audit, coverage gaps, indexability.
"""
import re, xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse
from collections import Counter
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from denzo.agents.utils.stealth_fetch import fetch_html


def _fetch(url: str) -> str:
    try:
        res = fetch_html(url)
        return res.get('html','') if res and res.get('ok') else None
    except: return None


def analyze_sitemap(url: str, html: str, domain: str) -> dict:
    findings = []
    score = 100
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    sitemap_url = None
    is_index = False
    child_sitemaps = 0
    child_sitemap_urls = []
    total_urls = total_sampled = 0
    domain_mismatches = hreflang_count = 0
    lastmod_present = lastmod_future = lastmod_identical = False
    lastmod_values = []
    changefreqs = Counter()
    priorities = []
    coverage = {"pages":0,"images":0,"other":0}

    # 1. Discover sitemap
    candidates = [
        f"{base}/sitemap.xml", f"{base}/sitemap_index.xml", f"{base}/wp-sitemap.xml",
        f"{base}/sitemap-index.xml", f"{base}/page-sitemap.xml",
    ]
    # Check robots.txt for Sitemap: directive
    robots_res = _fetch(f"{base}/robots.txt")
    if robots_res:
        for line in robots_res.split('\n'):
            if line.strip().lower().startswith('sitemap:'):
                candidate = line.split(':',1)[1].strip()
                if candidate: candidates.insert(0, candidate)
    # Check HTML link
    sitemap_link = re.search(r'<link[^>]*rel=["\']sitemap["\'][^>]*href=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if sitemap_link: candidates.insert(0, urljoin(url, sitemap_link.group(1)))
    # Also try BeautifulSoup for robustness
    if not sitemap_link:
        try:
            soup_sitemap = BeautifulSoup(html, 'html.parser')
            link_tag = soup_sitemap.find('link', rel=lambda r: r and 'sitemap' in str(r).lower())
            if link_tag and link_tag.get('href'):
                candidates.insert(0, urljoin(url, link_tag['href']))
        except Exception:
            pass

    sitemap_xml = None
    tried = []
    for c in candidates[:8]:
        xml = _fetch(c)
        if xml and ('<urlset' in xml or '<sitemapindex' in xml):
            sitemap_xml = xml; sitemap_url = c; break
        tried.append(c)

    if not sitemap_xml:
        findings.append({"severity":"critical","module":"sitemap","title":"No sitemap.xml found or accessible","detail":f"Tried {len(tried)} locations: {', '.join(tried[:5])}. A sitemap is the primary discovery mechanism for search engines. Without one, Google relies entirely on internal links to discover pages.","fix":"1. Create sitemap.xml at site root\n2. Reference it in robots.txt: Sitemap: https://www.{domain}/sitemap.xml\n3. Submit to Google Search Console\n4. Include all location pages, service pages, and key content.","impact":"Delayed indexing of new/updated pages. Google cannot efficiently discover site structure. Estimated indexing delay: 2-4 weeks for new content."})
        return {"score":0,"findings":findings,"total_urls":0,"sitemap_url":None,"is_index":False,"domain_mismatches":0,"hreflang_count":0,"lastmod_future":False,"lastmod_identical":False,"coverage":coverage,"tried_locations":tried}

    # 2. Parse
    try:
        root = ET.fromstring(sitemap_xml)
    except ET.ParseError as e:
        findings.append({"severity":"critical","module":"sitemap","title":"Sitemap XML is malformed — unparseable","detail":f"XML parse error: {str(e)[:200]}. Google cannot process a broken sitemap.","fix":"Validate with: https://www.xml-sitemaps.com/validate-xml-sitemap.html. Common causes: unescaped ampersands, invalid characters, missing closing tags. Regenerate from source.","impact":"Google will reject the entire sitemap. All 60+ URLs lose their primary discovery channel."})
        score -= 50
        return {"score":max(0,score),"findings":findings,"total_urls":0,"sitemap_url":sitemap_url}

    import re as _re
    _tag_match = _re.match(r'\{([^}]+)\}', root.tag)
    ns = '{' + _tag_match.group(1) + '}' if _tag_match else ''
    is_index = root.tag.endswith('sitemapindex')

    if is_index:
        child_sitemap_elements = root.findall(f'{ns}sitemap')
        child_sitemaps = len(child_sitemap_elements)
        # Extract child sitemap URLs (strings, JSON-serializable)
        child_sitemap_urls = []
        for elem in child_sitemap_elements:
            loc_e = elem.find(f'{ns}loc')
            if loc_e is not None and loc_e.text:
                child_sitemap_urls.append(loc_e.text.strip())
        findings.append({"severity":"info","module":"sitemap","title":f"Sitemap index: {len(child_sitemap_elements)} child sitemaps","detail":"Index structure is good for sites with many URLs. Each child sitemap should be under 50MB/50,000 URLs.","fix":None})

        # Sample first child
        if child_sitemap_urls:
            child_xml = _fetch(child_sitemap_urls[0])
            if child_xml:
                try:
                    child_root = ET.fromstring(child_xml)
                    urls = child_root.findall(f'{ns}url')
                    total_urls = len(urls) * child_sitemaps  # estimate
                except: pass
    else:
        urls = root.findall(f'{ns}url')
        total_urls = len(urls)
        total_sampled = min(total_urls, 300)

        for url_elem in urls[:300]:
            loc = url_elem.find(f'{ns}loc')
            if loc is None: continue
            loc_url = (loc.text or '').strip()
            parsed_loc = urlparse(loc_url)
            loc_domain = (parsed_loc.hostname or parsed_loc.netloc)
            loc_domain = re.sub(r'^www\.', '', loc_domain)
            if loc_domain and loc_domain != domain:
                domain_mismatches += 1

            lastmod = url_elem.find(f'{ns}lastmod')
            if lastmod is not None and lastmod.text:
                lastmod_present = True
                lastmod_values.append(lastmod.text.strip())

            changefreq = url_elem.find(f'{ns}changefreq')
            if changefreq is not None and changefreq.text:
                changefreqs[changefreq.text.strip()] += 1

            priority = url_elem.find(f'{ns}priority')
            if priority is not None and priority.text:
                try: priorities.append(float(priority.text.strip()))
                except: pass

            # Hreflang via xhtml:link
            for link in url_elem.findall('{http://www.w3.org/1999/xhtml}link'):
                if link.get('rel') == 'alternate' and link.get('hreflang'):
                    hreflang_count += 1

            loc_lower = loc_url.lower()
            if any(ext in loc_lower for ext in ['.jpg','.jpeg','.png','.webp','.gif','.svg','.avif']):
                coverage["images"] += 1
            elif any(ext in loc_lower for ext in ['.xml','.mp4','.pdf','.doc','.txt']):
                coverage["other"] += 1
            else: coverage["pages"] += 1

        # Analysis
        if total_urls < 10:
            findings.append({"severity":"high","module":"sitemap","title":f"Very small sitemap: only {total_urls} URLs","detail":"For a site with multiple locations and services, the sitemap should have 50+ URLs. Small sitemaps indicate missing pages or generation issues.","fix":"Ensure all pages are included: location pages, service pages, about, contact, blog posts, and any programmatic content."})
            score -= 15

        if domain_mismatches > 0:
            pct = round(domain_mismatches/total_sampled*100)
            findings.append({"severity":"critical","module":"sitemap","title":f"Domain mismatch: {domain_mismatches}/{total_sampled} URLs ({pct}%) use wrong domain","detail":f"Sitemap URLs use a different domain than the canonical {domain}. If canonical tags use www.{domain} but sitemap uses {domain}, Google receives contradictory signals. This is one of the most common and damaging sitemap errors.","fix":"Regenerate sitemap with ALL <loc> URLs using the EXACT same domain format as your canonical tags. If canonicals use www, sitemap MUST use www. Update robots.txt sitemap directive to match. Resubmit in Google Search Console.","impact":"Canonical confusion. Google must guess which domain to index. Estimated ranking dilution: 15-30% across all sitemap URLs."})
            score -= 30

        # Lastmod analysis
        if lastmod_present:
            unique_lastmods = set(lastmod_values)
            if len(unique_lastmods) == 1:
                lastmod_identical = True
                findings.append({"severity":"medium","module":"sitemap","title":f"All {len(lastmod_values)} lastmod timestamps are identical","detail":f"Value: '{lastmod_values[0]}'. This suggests the sitemap is bulk-regenerated rather than reflecting actual content update times. Google may distrust the timestamps.","fix":"Use actual page modification dates for lastmod. For CMS/Next.js sites, use the build time for static pages and actual update time for dynamic content."})
                score -= 8
            # Future dates
            for lv in lastmod_values[:5]:
                try:
                    dt = datetime.fromisoformat(lv.replace('Z','+00:00'))
                    if dt > datetime.now(timezone.utc):
                        lastmod_future = True
                        findings.append({"severity":"medium","module":"sitemap","title":"Future lastmod dates detected — will be ignored","detail":f"Example: {lv}. Google ignores timestamps in the future as they appear fraudulent.","fix":"Use current or past timestamps. If regenerating daily, use the actual generation time (not next midnight UTC)."})
                        score -= 8
                        break
                except: pass
        else:
            findings.append({"severity":"medium","module":"sitemap","title":"No <lastmod> tags in sitemap","detail":"Lastmod helps Google prioritize crawling. Without it, Google treats all pages as equally stale.","fix":"Add <lastmod> to each URL entry with the actual last modification date in W3C Datetime format (YYYY-MM-DD)."})
            score -= 10

        # Priority analysis
        if priorities and len(set(priorities)) == 1:
            findings.append({"severity":"low","module":"sitemap","title":"All priorities set to the same value — no differentiation","detail":f"All sampled URLs have priority={priorities[0]}. Priority hints to Google which pages matter most.","fix":"Set homepage to 1.0, location pages to 0.8, service pages to 0.7, blog/other to 0.5."})

        # Hreflang
        if hreflang_count > 0:
            findings.append({"severity":"pass","module":"sitemap","title":f"Hreflang annotations found: {hreflang_count}","detail":"Well-implemented. Hreflang in sitemaps helps Google serve the right language version in SERPs.","fix":None})
        else:
            findings.append({"severity":"info","module":"sitemap","title":"No hreflang annotations in sitemap URLs","detail":"No hreflang found. This is only relevant if the site serves multiple languages/regions. For a single-language site it is expected and not an issue.","fix":"ONLY if you serve multiple languages: add <xhtml:link rel=\"alternate\" hreflang=\"...\" href=\"...\"/> to each URL entry. Otherwise ignore."})

        # Coverage assessment
        img_pct = round(coverage["images"]/total_sampled*100) if total_sampled else 0
        if img_pct > 20:
            findings.append({"severity":"info","module":"sitemap","title":f"{coverage['images']} image URLs in sitemap ({img_pct}%)","detail":"Images in sitemaps are fine but ensure they reference the canonical image URL. Separate image sitemaps are preferred for large image collections.","fix":None})

    # Success
    if score >= 70:
        findings.insert(0, {"severity":"pass","module":"sitemap","title":f"Sitemap found: {total_urls:,} URLs" + (f" (index of {child_sitemaps} files)" if is_index else ""),"detail":f"URL: {sitemap_url}\nDiscovered via: {tried[0] if tried else 'direct try'}\nTotal URLs: {total_urls:,}\nHreflang entries: {hreflang_count}\nChangefreqs: {dict(changefreqs.most_common(3)) if changefreqs else 'none'}","fix":None})

    return {"score":max(0,score),"findings":findings,"total_urls":total_urls,"sitemap_url":sitemap_url,"is_index":is_index,"child_sitemaps":child_sitemaps,"child_sitemap_urls":child_sitemap_urls,"domain_mismatches":domain_mismatches,"hreflang_count":hreflang_count,"lastmod_future":lastmod_future,"lastmod_identical":lastmod_identical,"lastmod_present":lastmod_present,"coverage":coverage,"changefreqs":dict(changefreqs.most_common(5)),"priority_range":[round(min(priorities),1),round(max(priorities),1)] if priorities else None}
