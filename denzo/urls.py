"""Canonical public URLs and rendered quality documents for every publisher."""
import json
import re
from html import escape
from urllib.parse import urlsplit, urlunsplit, quote


def normalize_base(value):
    value = (value or '').strip().rstrip('/')
    if not value:
        raise ValueError('A public site URL is required')
    if '://' not in value:
        value = 'https://' + value
    p = urlsplit(value)
    if p.scheme not in ('https','http') or not p.hostname or p.username or p.password:
        raise ValueError('Invalid public site URL')
    return urlunsplit((p.scheme, p.netloc.lower(), p.path.rstrip('/'), '', ''))


def site_base_url(ctx):
    return normalize_base(ctx.pages_domain or (ctx.wp_url if ctx.publisher_type == 'wordpress' else '') or ctx.domain or ctx.website_url)


def page_directory(page_type):
    value = re.sub(r'[^a-z0-9_-]', '', (page_type or 'page').lower()) or 'page'
    return value if value.endswith('s') else value + 's'


def public_page_url(page, ctx):
    page = dict(page)
    if page.get('source_url') and page.get('origin') == 'existing':
        return page['source_url']
    if ctx.publisher_type == 'wordpress' and page.get('publish_url'):
        return page['publish_url']
    slug = (page.get('slug') or '').strip('/')
    if not slug or any(p in ('.','..') for p in slug.split('/')) or any(c in slug for c in ('?', '#', '\\')):
        raise ValueError('Invalid page slug')
    slug = quote(slug, safe='/-_~')
    base = site_base_url(ctx)
    if ctx.publisher_type == 'wordpress':
        return f'{base}/{slug}/'
    folder = page_directory(page.get('type'))
    return f'{base}/en/{folder}/{slug}' if ctx.github_format == 'nextjs' else f'{base}/{folder}/{slug}.html'


def schema_json(raw):
    if not raw:
        return ''
    if isinstance(raw,str):
        raw = re.sub(r'^\s*<script[^>]*>|</script>\s*$', '', raw, flags=re.I).strip()
        raw = json.loads(raw)
    if not isinstance(raw,(dict,list)):
        raise ValueError('Schema must be a JSON object or array')
    return json.dumps(raw,ensure_ascii=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')


def quality_document(content, page, ctx):
    page = dict(page)
    title = escape(page.get('meta_title') or page.get('title') or ctx.client_name)
    desc = escape(page.get('meta_description') or '',quote=True)
    canonical = escape(public_page_url(page,ctx),quote=True)
    schema = schema_json(page.get('schema_markup'))
    script = f'<script type="application/ld+json">{schema}</script>' if schema else ''
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{title}</title><meta name="description" content="{desc}">'
            f'<link rel="canonical" href="{canonical}">{script}</head>'
            f'<body><h1>{escape(page.get("title") or ctx.client_name)}</h1>{content}</body></html>')
