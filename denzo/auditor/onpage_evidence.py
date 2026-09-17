"""Observable homepage checks. No ranking, revenue, word-count or speed estimates.

Each deduction carries its rule and evidence. A zero deduction is a review
suggestion, not proof of a defect. Browser layout and link targets are not tested
here; real mobile performance is supplied separately by PageSpeed.
"""
import json
import re
from urllib.parse import urljoin, urlsplit
from bs4 import BeautifulSoup


def visible_soup(html):
    soup = BeautifulSoup(html, 'html.parser')
    for node in soup.select('script,style,template,noscript,[hidden],[aria-hidden="true"]'):
        node.decompose()
    return soup


class Module:
    def __init__(self, name, url):
        self.name, self.url, self.findings, self.deductions = name, url, [], 0

    def add(self, rule, title, detail, fix=None, points=0, severity='info', **evidence):
        self.deductions += points
        self.findings.append(dict(rule_id=rule, module=self.name, title=title,
            detail=detail, fix=fix, severity=severity, deduction=points,
            evidence=dict(source='fetched_homepage_html', url=self.url, **evidence)))

    def result(self, **metrics):
        return dict(score=max(0, 100-self.deductions), status='completed',
                    findings=self.findings, deductions=self.deductions, **metrics)


def analyze_onpage(url, html, headers=None, is_local=False):
    soup, visible = BeautifulSoup(html, 'html.parser'), visible_soup(html)
    text = (visible.body or visible).get_text(' ', strip=True)
    headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    modules = {name: Module(name, url) for name in ['technical','geo','content','images','local_seo']}
    tech, geo, content, images, local = [modules[name] for name in modules]
    title = soup.title.get_text(' ', strip=True) if soup.title else ''
    metas = {str(m.get('name', '')).lower(): str(m.get('content', '')).strip() for m in soup.find_all('meta')}
    description = metas.get('description', '')
    h1 = visible.find_all('h1')
    if urlsplit(url).scheme != 'https':
        tech.add('https', 'The final page uses HTTP', 'The fetched final URL does not use an encrypted HTTPS connection.',
                 'Enable HTTPS and redirect the HTTP URL.', 20, 'high', final_url=url)
    page_directives = [str(m.get('content','')) for m in soup.find_all('meta')
                       if str(m.get('name','')).lower() in ('robots','googlebot')]
    header_scope = None
    for directive in headers.get('x-robots-tag', '').lower().split(','):
        if ':' in directive:
            header_scope, directive = (s.strip() for s in directive.split(':',1))
        if header_scope in (None,'googlebot'):
            page_directives.append(directive)
    directives = ','.join(page_directives).lower()
    if re.search(r'\b(noindex|none)\b', directives):
        tech.add('noindex', 'An indexing restriction was found', 'The page declares noindex or none. Confirm whether the restriction is intentional; this audit does not query the Google index.',
                 'Review the robots meta tags and X-Robots-Tag before changing a deliberate restriction.', 40, 'critical', directives=directives[:300])
    if not title:
        tech.add('title_missing', 'The page has no title', 'No non-empty title element was found in the fetched HTML.',
                 'Add a descriptive page title.', 20, 'high', title_count=len(soup.find_all('title')))
    if not description:
        tech.add('description_missing', 'No meta description was found', 'The fetched HTML has no non-empty meta description. Search engines may generate a snippet from other page text.',
                 'Add a concise description of the page.', 5, 'medium')
    if not h1:
        tech.add('heading_missing', 'No main heading was found', 'There is no H1 in the fetched page content.',
                 'Give the page a clear, descriptive main heading.', 10, 'medium', h1_count=0)
    viewport = metas.get('viewport', '').replace(' ', '').lower()
    if 'width=device-width' not in viewport:
        tech.add('viewport', 'Responsive viewport configuration needs review', 'The page does not declare width=device-width. Actual device layout should also be checked in a browser.',
                 'Configure the viewport for mobile devices.', 15, 'high', viewport=viewport)
    html_tag = soup.find('html')
    if not html_tag or not html_tag.get('lang'):
        tech.add('language', 'Page language is not declared', 'The HTML root has no lang attribute for assistive technology.',
                 'Declare the language used on the page.', 5, 'low')
    canonical = soup.find('link', rel=lambda v: v and 'canonical' in str(v).lower())
    if canonical and canonical.get('href'):
        target = urljoin(url, canonical['href'])
        if urlsplit(target).hostname != urlsplit(url).hostname:
            tech.add('canonical_host', 'The canonical points to another hostname', 'A different canonical can be intentional. Its index status and ownership have not been verified.',
                     'Confirm that this is the intended canonical page.', canonical=target[:500])

    # Mixed content concerns loaded assets, not ordinary links to an HTTP site.
    insecure = []
    for node in soup.find_all(['img', 'script', 'iframe', 'source', 'link']):
        if node.name == 'link' and not set(node.get('rel', [])).intersection({'stylesheet','preload','modulepreload'}):
            continue
        source = node.get('src') or node.get('href') or ''
        if urlsplit(url).scheme == 'https' and source.lower().startswith('http://'):
            insecure.append(source[:200])
    if insecure:
        tech.add('mixed_assets', 'HTTP assets are referenced by an HTTPS page', 'These asset URLs need review for mixed-content loading. Ordinary navigation links are excluded.',
                 'Use HTTPS for the affected assets.', 10, 'high', examples=insecure[:5], count=len(insecure))

    schema_types, schema_errors = set(), 0
    def walk(value):
        if isinstance(value, dict):
            kind = value.get('@type', [])
            schema_types.update([kind] if isinstance(kind, str) else [x for x in kind if isinstance(x, str)] if isinstance(kind, list) else [])
            for child in value.values(): walk(child)
        elif isinstance(value, list):
            for child in value: walk(child)
    schema_blocks = soup.find_all('script', type=lambda v: v and v.lower() == 'application/ld+json')
    for block in schema_blocks:
        try: walk(json.loads(block.string or block.get_text()))
        except (ValueError, TypeError): schema_errors += 1
    if schema_errors:
        geo.add('schema_json', 'Some structured-data blocks are invalid JSON', 'These JSON-LD blocks could not be parsed. This is a syntax check, not rich-result eligibility validation.',
                'Validate and repair the JSON-LD syntax.', 25, 'medium', invalid_blocks=schema_errors)
    elif not schema_blocks:
        geo.add('schema_absent', 'No JSON-LD structured data was found', 'Other formats may be present. Structured data is optional and its absence does not establish an indexing or AI visibility problem.',
                'Consider relevant structured data if it accurately describes the business.')
    geo.add('schema_scope', 'Structured-content check scope', 'This module checks on-page structure. It does not measure AI citations, backlinks or authority.', types=sorted(schema_types))

    words = len(text.split())
    content.add('content_scope', f'{words} words found in the fetched content', 'Length alone is not a ranking criterion. Content usefulness and coverage need human review; no word-count deduction is applied.', word_count=words)
    headings = visible.find_all(re.compile(r'^h[1-6]$'))
    empty = sum(not h.get_text(' ', strip=True) and not h.find('img', alt=True) for h in headings)
    if empty:
        content.add('empty_heading', 'Some headings have no readable text', 'Empty headings may make the document harder to navigate with assistive technology.',
                    'Add meaningful heading text or remove decorative heading elements.', 10, 'medium', count=empty)

    img_nodes = visible.find_all('img')
    missing_alt = [img for img in img_nodes if not img.has_attr('alt') and img.get('role') not in ('presentation','none')]
    if missing_alt:
        images.add('alt_missing', 'Some images have no alt attribute', 'An absent alt attribute differs from an intentional empty alt for a decorative image.',
                   'Describe informative images and use alt="" for decorative images.', min(40, round(40*len(missing_alt)/max(1,len(img_nodes)))), 'medium', count=len(missing_alt), total=len(img_nodes))
    no_dimensions = sum(not (img.get('width') and img.get('height')) for img in img_nodes)
    if no_dimensions:
        images.add('image_dimensions_review', 'Some image dimensions are not in HTML attributes', 'CSS or a framework may reserve the space. This alone does not prove layout shift; check the measured PageSpeed result.',
                   'Review whether image space is reserved before loading.', count=no_dimensions)

    if is_local:
        phones = [a for a in visible.find_all('a', href=True) if str(a['href']).lower().startswith('tel:')]
        if not phones and not re.search(r'(?:\+?1[ .-]?)?\(?[2-9]\d{2}\)?[ .-]\d{3}[ .-]\d{4}', text):
            local.add('local_contact_review', 'A telephone contact was not detected on this page', 'Contact details may be on another page or use a format this check does not recognize. No Google Business Profile comparison was performed.',
                      'Confirm that visitors can find the preferred contact method.', 15, 'medium')
        local.add('local_scope', 'Local-business signals need confirmation', 'This check does not verify address accuracy, reviews, business ownership or map rankings.', detected_phone_links=len(phones))
    else:
        local.add('local_not_applicable', 'Local-business checks are not weighted', 'This page was not classified as a local business.')

    result = {name: m.result() for name, m in modules.items()}
    result['technical'].update(word_count=words, image_count=len(img_nodes), schema_blocks=len(schema_blocks),
        schema_types=sorted(schema_types), text_html_ratio=round(len(text)/max(1,len(html))*100,1),
        internal_links=sum(urlsplit(urljoin(url,a['href'])).hostname == urlsplit(url).hostname for a in visible.find_all('a',href=True)))
    result['images'].update(total=len(img_nodes), with_alt=len(img_nodes)-len(missing_alt), missing_dims=no_dimensions)
    result['content']['word_count'] = words
    return result
