"""Bounded sitemap discovery with actual sample counts and no ranking estimates."""
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlsplit
from bs4 import BeautifulSoup
from denzo.auditor.safe_fetch import fetch_html
from denzo.auditor.robots_analyzer import parse_robots


def _fetch(url):
    result = fetch_html(url, allow_jina=False)
    if result.get('status') in (404, 410): return None
    if not result.get('ok'): raise ValueError('Sitemap response could not be verified')
    return result.get('html', '')


def _parse(text):
    if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
        raise ValueError('Sitemap DTD declarations are not supported')
    root = ET.fromstring(text)
    if root.tag.split('}')[-1] not in ('urlset','sitemapindex'):
        raise ValueError('Expected a sitemap XML root')
    return root


def _children(root, tag):
    return [x for x in root if x.tag.split('}')[-1] == tag]


def _locs(root, tag):
    return [x.text.strip() for item in _children(root, tag) for x in item
            if x.tag.split('}')[-1] == 'loc' and x.text and x.text.strip()]


def analyze_sitemap(url, html, domain, robots_result=None):
    parsed = urlsplit(url)
    base = f'{parsed.scheme}://{parsed.netloc}'
    if robots_result is None:
        robots = _fetch(base+'/robots.txt')
        declared = parse_robots(robots)[1] if robots else []
    else:
        declared = list(robots_result.get('sitemap_refs', []))
    soup = BeautifulSoup(html, 'html.parser')
    declared += [urljoin(url, link['href']) for link in soup.find_all('link',href=True)
                 if 'sitemap' in link.get('rel', [])]
    candidates = list(dict.fromkeys(declared + [base+'/sitemap.xml', base+'/sitemap_index.xml', base+'/wp-sitemap.xml']))[:8]
    tried, invalid = [], []
    root, sitemap_url = None, None
    for candidate in candidates:
        xml = _fetch(candidate)
        tried.append(candidate)
        if not xml: continue
        try:
            root = _parse(xml)
            sitemap_url = candidate
            break
        except (ET.ParseError, ValueError):
            if candidate in declared or '<urlset' in xml or '<sitemapindex' in xml:
                invalid.append(candidate)
    if root is None:
        found_invalid = bool(invalid)
        return dict(score=30 if found_invalid else 0, status='completed', sitemap_url=None, total_urls=0,
            is_index=False, is_sample=True, tried_locations=tried,
            findings=[dict(rule_id='sitemap_discovery', module='sitemap', severity='high' if found_invalid else 'medium',
                title='A declared sitemap could not be parsed' if found_invalid else 'No sitemap found at the checked locations',
                detail='A broken or missing XML sitemap makes it harder for Google to discover new and changed pages, especially on larger sites.',
                fix='Validate the declared XML sitemap.' if found_invalid else 'Publish an XML sitemap and reference it from robots.txt.',
                deduction=70 if found_invalid else 100, evidence=dict(source='sitemap_discovery', checked_urls=tried, invalid_urls=invalid))])
    is_index = root.tag.split('}')[-1] == 'sitemapindex'
    children = _locs(root, 'sitemap') if is_index else []
    urls, inspected = [], []
    if is_index:
        for child in children[:3]:
            child_url = urljoin(sitemap_url, child)
            xml = _fetch(child_url)
            if not xml: raise ValueError('A sampled child sitemap was unavailable')
            sub = _parse(xml)
            if sub.tag.split('}')[-1] != 'urlset':
                raise ValueError('Nested sitemap index requires manual review')
            urls.extend(_locs(sub,'url'))
            inspected.append(child_url)
    else:
        urls = _locs(root,'url')
    unique = sorted(set(urls))
    invalid_urls = [u for u in unique if urlsplit(u).scheme not in ('http','https') or not urlsplit(u).hostname]
    findings = [dict(rule_id='sitemap_sample', module='sitemap', severity='info',
        title=f'{len(unique)} distinct URLs read from the sitemap sample',
        detail='These are counted entries, not an estimate of the whole site. URL availability and indexing have not been checked.',
        fix=None, deduction=0, evidence=dict(source='sitemap_xml', url=sitemap_url, sampled_children=inspected,
            declared_children=len(children), sampled_url_count=len(unique)))]
    if not unique:
        findings.append(dict(rule_id='sitemap_empty', module='sitemap', severity='medium',
            title='The sitemap contains no URLs', detail='An empty sitemap gives Google nothing to discover.',
            fix="Populate the sitemap with the site's real pages.", deduction=60,
            evidence=dict(source='sitemap_xml', url=sitemap_url)))
    if invalid_urls:
        findings.append(dict(rule_id='sitemap_urls', module='sitemap', severity='medium',
            title='Some sitemap entries are not absolute HTTP URLs', detail='Sitemap loc entries must identify absolute URLs.',
            fix='Correct the affected loc entries.', deduction=30,
            evidence=dict(source='sitemap_xml', url=sitemap_url, count=len(invalid_urls), examples=invalid_urls[:5])))
    score = 40 if not unique else (70 if invalid_urls else 100)
    return dict(score=score, status='completed', findings=findings, sitemap_url=sitemap_url,
        total_urls=len(unique), is_index=is_index, child_sitemaps=len(children), child_sitemap_urls=children,
        is_sample=is_index, sampled_children=len(inspected), sampled_url_count=len(unique))
