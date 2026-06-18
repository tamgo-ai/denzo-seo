"""
nextjs_renderer.py v2 — Genera page.jsx para Next.js App Router con i18n [locale].
Compatible con pdx-prog/acg-web (next-intl, layout compartido, Tailwind globals.css).

La página generada es un Server Component mínimo:
  - metadata export (SEO + OpenGraph)
  - Contenido HTML via dangerouslySetInnerHTML
  - Schema JSON-LD
  - Sin dependencia de next-intl (inglés directo)
"""
import json
import re
import hashlib
from denzo.agents.base_agent import ClientContext


def _component_name(slug: str) -> str:
    """Convert slug to PascalCase component name."""
    parts = re.split(r'[-_]', slug.strip("/"))
    return "".join(p.capitalize() for p in parts if p) or "DenzoPage"


def _extract_h1(content_html: str, fallback: str) -> str:
    """Extract H1 text from HTML content, return fallback if none found."""
    m = re.search(r'<h1[^>]*>(.*?)</h1>', content_html, re.DOTALL | re.IGNORECASE)
    if m:
        return re.sub(r'<[^>]+>', '', m.group(1)).strip()
    return fallback


def _clean_html_for_jsx(html: str) -> str:
    """
    Clean AI-generated HTML for embedding in JSX.
    - Remove wrapper divs/sections with class names from old template
    - Remove hero sections, CTA sections (layout provides those)
    - Remove H1 (we inject it as JSX)
    - Normalize whitespace
    """
    # Strip hero/CTA sections — layout provides them
    html = re.sub(
        r'<section[^>]*class="[^"]*(?:hero-section|cta-section)[^"]*"[^>]*>.*?</section>',
        '', html, flags=re.DOTALL | re.IGNORECASE)

    # Strip standalone H1 tags (will be rendered as JSX)
    html = re.sub(r'<h1[^>]*>.*?</h1>', '', html, flags=re.DOTALL | re.IGNORECASE)

    # Remove script tags (schema is injected separately)
    html = re.sub(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>.*?</script>',
                  '', html, flags=re.DOTALL | re.IGNORECASE)

    # Strip wrapper classes from common patterns
    for wrapper_class in ['section-content', 'section-alt', 'two-col', 'col-text',
                          'col-image', 'container', 'page-wrap', 'site-header',
                          'site-footer', 'breadcrumb']:
        html = re.sub(
            rf'<div[^>]*class="[^"]*{wrapper_class}[^"]*"[^>]*>',
            '', html, flags=re.IGNORECASE)
        html = re.sub(rf'</div>', '', html, count=1, flags=re.IGNORECASE) if wrapper_class in html else html

    # Clean up excessive newlines
    html = re.sub(r'\n{3,}', '\n\n', html)

    return html.strip()


def render_nextjs_page(page: dict, ctx: ClientContext, assets: dict = None) -> str:
    """
    Generate a Next.js App Router page.jsx as a Server Component.

    Output structure:
      export const metadata = { ... }
      export default function ComponentName() {
        return (
          <article className="prose ...">
            <h1>...</h1>
            <div dangerouslySetInnerHTML={{ __html: "..." }} />
            <script type="application/ld+json" ... />
          </article>
        );
      }
    """
    assets = assets or {}

    # Page data
    slug = (page.get("slug") or "").strip("/")
    meta_title = page.get("meta_title") or page.get("title") or ctx.client_name
    meta_desc = page.get("meta_description") or ""
    keyword = page.get("target_keyword") or ""
    content_html = page.get("content") or ""
    title = page.get("title") or meta_title
    page_type = page.get("type") or "page"
    location = page.get("location") or ctx.primary_city or ""

    # Domain resolution
    domain = ctx.pages_domain or (f"https://www.{ctx.domain}" if ctx.domain else "")
    canonical = f"{domain}/en/{page_type}s/{slug}" if domain else f"/en/{page_type}s/{slug}"

    # H1 from content
    h1_text = _extract_h1(content_html, title)

    # Clean content for JSX embedding
    clean_html = _clean_html_for_jsx(content_html)

    # Component name
    fn_name = _component_name(slug)

    # Primary color from assets (github_publisher resolves this from
    # nextjs_assets → site_style_guide → default #0b3950)
    primary = assets.get("primary_color", "#0b3950")

    # Schema.org (from page if exists, otherwise generate)
    schema_raw = page.get("schema_markup", "")
    if schema_raw:
        try:
            schema_obj = json.loads(schema_raw)
        except Exception:
            schema_obj = {
                "@context": "https://schema.org",
                "@type": "LocalBusiness",
                "name": ctx.client_name,
                "url": canonical,
                "telephone": ctx.phone,
                "description": meta_desc,
                "areaServed": location,
            }
    else:
        schema_obj = {
            "@context": "https://schema.org",
            "@type": "LocalBusiness",
            "name": ctx.client_name,
            "url": canonical,
            "telephone": ctx.phone,
            "description": meta_desc,
            "areaServed": location,
        }

    schema_json = json.dumps(schema_obj)

    # Escape HTML content for JSX embedding
    # The content goes inside a JSX string that gets passed to dangerouslySetInnerHTML
    # We need to escape backslashes, backticks, and ${} since it's inside a JSX template literal
    escaped_html = clean_html.replace('\\', '\\\\').replace('`', '\\`').replace('${', '\\${')

    return f"""export const metadata = {{
  title: {json.dumps(meta_title)},
  description: {json.dumps(meta_desc)},
  keywords: {json.dumps(keyword)},
  alternates: {{
    canonical: {json.dumps(canonical)},
  }},
  openGraph: {{
    title: {json.dumps(meta_title)},
    description: {json.dumps(meta_desc)},
    url: {json.dumps(canonical)},
    siteName: {json.dumps(ctx.client_name)},
    locale: 'en_US',
    type: 'website',
  }},
  twitter: {{
    card: 'summary_large_image',
    title: {json.dumps(meta_title)},
    description: {json.dumps(meta_desc)},
  }},
}};

export default function {fn_name}() {{
  return (
    <article className="max-w-4xl mx-auto px-6 py-14 bg-white text-gray-900
      [&_p]:mb-5 [&_p]:leading-relaxed [&_p]:text-gray-700 [&_p]:text-[1.05rem]
      [&_h2]:text-2xl [&_h2]:font-bold [&_h2]:text-[{primary}] [&_h2]:mt-12 [&_h2]:mb-4 [&_h2]:pb-2 [&_h2]:border-b [&_h2]:border-gray-100
      [&_h3]:text-xl [&_h3]:font-semibold [&_h3]:text-[{primary}] [&_h3]:mt-8 [&_h3]:mb-3
      [&_ul]:mb-6 [&_ul]:pl-6 [&_ul]:space-y-2
      [&_li]:text-gray-700 [&_li]:leading-relaxed [&_li]:list-disc
      [&_ol]:mb-6 [&_ol]:pl-6 [&_ol]:space-y-2 [&_ol_li]:list-decimal
      [&_strong]:font-semibold [&_strong]:text-gray-900
      [&_blockquote]:border-l-4 [&_blockquote]:border-[{primary}] [&_blockquote]:pl-5 [&_blockquote]:italic [&_blockquote]:text-gray-600 [&_blockquote]:my-6
      [&_a]:text-[{primary}] [&_a]:underline [&_a]:font-medium
      [&_details]:mb-4 [&_details]:border [&_details]:border-gray-200 [&_details]:rounded-lg [&_details]:overflow-hidden
      [&_summary]:cursor-pointer [&_summary]:font-semibold [&_summary]:text-[{primary}] [&_summary]:px-5 [&_summary]:py-4 [&_summary]:bg-gray-50
      [&_details_p]:px-5 [&_details_p]:py-4 [&_details_p]:mb-0
      [&_table]:w-full [&_table]:border-collapse [&_table]:mb-6
      [&_th]:bg-[{primary}] [&_th]:text-white [&_th]:px-4 [&_th]:py-2 [&_th]:text-left
      [&_td]:border [&_td]:border-gray-200 [&_td]:px-4 [&_td]:py-2">

      <h1 className="text-3xl md:text-4xl font-bold text-[{primary}] mb-8 pb-4 border-b-2 border-gray-200">
        {json.dumps(h1_text)}
      </h1>

      <div dangerouslySetInnerHTML={{{{ __html: `{escaped_html}` }}}} />

      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{{{ __html: {json.dumps(schema_json)} }}}}
      />
    </article>
  );
}}
"""
