"""
Framework Detector — identifies the site's tech stack from HTML markers and HTTP
headers so downstream analyzers don't apply static-HTML heuristics to JS
frameworks (Next.js, Nuxt, SvelteKit, …) where they produce false positives.

The two key flags consumers use:
- is_js_framework: SSR/hydration inflates HTML (text-to-HTML ratio) and
  code-splits JS into many chunks (external-script count) — both are normal.
- uses_image_component: the framework's Image component manages layout via CSS
  (next/image fill mode), so missing width/height attributes are NOT a CLS defect.
"""
from bs4 import BeautifulSoup


# JS frameworks where SSR/hydration payload + code-splitting make static-HTML
# heuristics (text-to-HTML ratio, external-script count) misleading.
_JS_FRAMEWORKS = {'nextjs', 'nuxt', 'sveltekit', 'gatsby', 'astro', 'angular', 'vue', 'react'}

# Frameworks whose Image component manages dimensions via CSS (fill/layout mode),
# so an <img> without width/height is not automatically a CLS risk.
_IMAGE_COMPONENT_FRAMEWORKS = {'nextjs', 'nuxt', 'sveltekit', 'astro', 'gatsby', 'react'}

_FRAMEWORK_LABELS = {
    'nextjs': 'Next.js (React)',
    'nuxt': 'Nuxt (Vue)',
    'sveltekit': 'SvelteKit',
    'gatsby': 'Gatsby (React)',
    'astro': 'Astro',
    'angular': 'Angular',
    'vue': 'Vue',
    'react': 'React',
    'wordpress': 'WordPress',
    'wix': 'Wix',
    'shopify': 'Shopify',
    'webflow': 'Webflow',
    'squarespace': 'Squarespace',
    'framer': 'Framer',
    'bubble': 'Bubble',
    'ghost': 'Ghost',
    'static': 'Static HTML',
}


def detect_framework(html: str, headers: dict = None) -> dict:
    """Detect the site's framework/stack. Returns a dict with flags for analyzers."""
    h = html or ''
    low = h.lower()
    headers_lower = {k.lower(): str(v).lower() for k, v in (headers or {}).items()} if headers else {}

    framework = 'static'

    # Order matters: strongest, most-specific markers first.
    if ('__next_data__' in low or '/_next/' in low or 'data-nimg' in low
            or 'id="__next"' in low or 'data-nextjs' in low):
        framework = 'nextjs'
    elif '__nuxt__' in low or '/_nuxt/' in low:
        framework = 'nuxt'
    elif '__sveltekit' in low or '/_app/' in low and 'svelte' in low:
        framework = 'sveltekit'
    elif '__gatsby' in low or 'id="___gatsby"' in low:
        framework = 'gatsby'
    elif '<astro-' in low or '/_astro/' in low:
        framework = 'astro'
    elif 'ng-version=' in low or '<app-root' in low or '_ngcontent-' in low:
        framework = 'angular'
    elif 'data-v-' in low and ('__vue__' in low or 'vue' in low):
        framework = 'vue'
    elif 'data-reactroot' in low or 'data-react-checksum' in low:
        framework = 'react'
    elif 'wp-content' in low or 'wp-includes' in low:
        framework = 'wordpress'
    elif 'wixstatic' in low or 'x-wix' in headers_lower or 'wix.com' in low:
        framework = 'wix'
    elif 'cdn.shopify.com' in low or 'shopify.theme' in low:
        framework = 'shopify'
    elif 'data-wf-page' in low or 'data-wf-site' in low or 'webflow.io' in low:
        framework = 'webflow'
    elif 'static1.squarespace.com' in low or 'squarespace' in low:
        framework = 'squarespace'
    elif 'data-framer-' in low or 'framer' in low:
        framework = 'framer'
    elif 'bubble.io' in low:
        framework = 'bubble'
    elif 'ghost.io' in low:
        framework = 'ghost'

    return {
        'framework': framework,
        'label': _FRAMEWORK_LABELS.get(framework, framework),
        'is_js_framework': framework in _JS_FRAMEWORKS,
        'uses_image_component': framework in _IMAGE_COMPONENT_FRAMEWORKS,
        'server': headers_lower.get('server', ''),
    }


def is_framework_image(img) -> bool:
    """True if this <img> is rendered by a framework Image component that manages
    dimensions via CSS (e.g. Next.js next/image fill mode). Missing width/height
    attributes on these are NOT a CLS defect."""
    if img.get('data-nimg') is not None:        # Next.js next/image
        return True
    if img.get('data-nuxt-img') is not None:    # Nuxt Image
        return True
    if img.get('data-v-lazy') is not None:      # Vue lazy directives
        return True
    return False
