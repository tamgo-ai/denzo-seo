"""
nextjs_renderer.py — Generic Next.js App Router page.jsx renderer for DENZO-SEO.
Parametrized entirely by ClientContext + optional nextjs_assets settings JSON.
Used by GitHubPublisher when ctx.github_format == 'nextjs'.

nextjs_assets (stored in settings key 'nextjs_assets') schema:
{
  "primary_color":   "#0b3950",
  "secondary_color": "#ea6018",
  "default_hero":    "/images/home/body.jpg",
  "brand_logo_map":  {"bmw": "/marcas/bmw.png", ...},
  "cert_map":        {"bmw": "/certifications/bmw.jpg", ...},
  "brand_hero_map":  {"bmw": "/bmw/hero.jpg"},
  "service_hero_map":{"paint-restoration": "/images/spray.jpg"},
  "stats": [
    {"value": "14", "label": "Manufacturer Certifications"},
    {"value": "OEM", "label": "Parts Only — Always"},
    {"value": "Lifetime", "label": "Written Warranty"},
    {"value": "Free", "label": "Estimates & Inspections"}
  ],
  "trust_items": [
    "All Insurance Accepted",
    "No Hidden Fees",
    "Rental Car Assistance",
    "4.8 ★ Google Rating"
  ],
  "cta_label":  "Get Free Estimate",
  "cta_link":   "/contact-us",
  "gallery_default": [
    ["/images/home/body.jpg", "Auto body repair"],
    ["/images/home/spray.jpg", "Paint restoration"]
  ]
}
"""
import json
import re
from denzo.agents.base_agent import ClientContext


# ── Helpers ───────────────────────────────────────────────────────────────────

def _component_name(slug: str) -> str:
    parts = re.split(r'[-_]', slug.strip("/"))
    return "".join(p.capitalize() for p in parts if p) or "DenzoPage"


def _extract_brand_slug(slug: str, brand_logo_map: dict) -> str:
    for brand in brand_logo_map:
        if slug == brand or slug.startswith(brand + "-") or ("-" + brand + "-") in slug:
            return brand
    return ""


def _clean_content(html: str) -> str:
    """Strip hero/H1/imgs/JSON-LD/custom class wrappers — same logic as NoHo's template."""
    html = re.sub(
        r'<section[^>]*class="[^"]*(?:cta-section|hero-section)[^"]*"[^>]*>.*?</section>',
        '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<div[^>]*class="[^"]*hero-btns[^"]*"[^>]*>.*?</div>',
                  '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<h1[^>]*>.*?</h1>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<img[^>]*/?>',  '', html, flags=re.IGNORECASE)
    html = re.sub(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>.*?</script>',
                  '', html, flags=re.DOTALL | re.IGNORECASE)
    for tag in ('section', 'div'):
        html = re.sub(
            rf'<{tag}[^>]*class="[^"]*(?:section-content|section-alt|two-col|col-text|col-image|container|faq-section)[^"]*"[^>]*>',
            '', html, flags=re.IGNORECASE)
        html = re.sub(rf'</{tag}>', '', html, flags=re.IGNORECASE)

    def _strip_attrs(m):
        tag = m.group(0)
        tag = re.sub(r'\s*style="[^"]*"', '', tag)
        tag = re.sub(r'\s*class="[^"]*"', '', tag)
        return tag
    html = re.sub(r'<p\b[^>]*>', _strip_attrs, html)
    html = re.sub(r'<(h[2-6])\b[^>]*>', _strip_attrs, html)
    html = re.sub(r'<(ul|ol|li)\b[^>]*>', _strip_attrs, html)
    html = re.sub(r'\n{3,}', '\n\n', html)
    return html.strip()


# ── Main render function ──────────────────────────────────────────────────────

def render_nextjs_page(page: dict, ctx: ClientContext, assets: dict) -> str:
    """
    Returns a complete Next.js App Router page.jsx string.
    ctx       — ClientContext with business data.
    assets    — parsed nextjs_assets settings dict (may be empty dict for defaults).
    """
    # Asset config with defaults
    primary    = assets.get("primary_color",   "#0b3950")
    secondary  = assets.get("secondary_color", "#ea6018")
    dflt_hero  = assets.get("default_hero",    "/images/home/body.jpg")
    logo_map   = assets.get("brand_logo_map",  {})
    cert_map   = assets.get("cert_map",        {})
    hero_map   = assets.get("brand_hero_map",  {})
    svc_map    = assets.get("service_hero_map",{})
    stats      = assets.get("stats", [
        {"value": str(len(ctx.certifications)) or "—", "label": "Certifications"},
        {"value": "OEM", "label": "Parts Only — Always"},
        {"value": "Lifetime", "label": "Written Warranty"},
        {"value": "Free", "label": "Estimates"},
    ])
    trust      = assets.get("trust_items", [
        "All Insurance Accepted", "No Hidden Fees",
        "Rental Car Assistance", "Lifetime Warranty"
    ])
    cta_label  = assets.get("cta_label", "Get Free Estimate")
    cta_link   = assets.get("cta_link",  "/contact-us")
    _default_alt = (ctx.services[0] if ctx.services else ctx.industry_vertical or "service")
    gallery_df = assets.get("gallery_default", [[dflt_hero, _default_alt]])

    # Page data
    slug         = (page.get("slug") or "").strip("/")
    meta_title   = page.get("meta_title")  or page.get("title") or ctx.client_name
    meta_desc    = page.get("meta_description") or ""
    location     = page.get("location") or ctx.primary_city or ""
    keyword      = page.get("target_keyword") or ""
    content_html = page.get("content") or ""
    title        = page.get("title") or meta_title

    domain       = ctx.pages_domain or (f"https://www.{ctx.domain}" if ctx.domain else "")

    # H1 from AI content
    h1_match = re.search(r'<h1[^>]*>(.*?)</h1>', content_html, re.DOTALL | re.IGNORECASE)
    h1_raw   = re.sub(r'<[^>]+>', '', h1_match.group(1)).strip() if h1_match else title
    content_body = _clean_content(content_html)

    # Brand / asset resolution
    brand_slug = _extract_brand_slug(slug, logo_map)
    brand_logo = logo_map.get(brand_slug, "")
    cert_img   = cert_map.get(brand_slug, "")

    # Hero image
    hero_img = hero_map.get(brand_slug, "")
    if not hero_img:
        for svc_key, img in svc_map.items():
            if svc_key in slug:
                hero_img = img
                break
    if not hero_img:
        hero_img = dflt_hero

    fn_name   = _component_name(slug)
    canonical = f"{domain}/{slug}" if domain else f"/{slug}"

    # Schema.org LocalBusiness
    schema = {
        "@context":   "https://schema.org",
        "@type":      "LocalBusiness",
        "name":       ctx.client_name,
        "url":        canonical,
        "telephone":  ctx.phone,
        "address": {
            "@type":           "PostalAddress",
            "streetAddress":   ctx.address,
            "addressLocality": ctx.primary_city,
            "addressRegion":   ctx.state,
            "addressCountry":  "US"
        },
        "description": meta_desc,
        "areaServed":  location,
    }

    # ── Phone digits for tel: link ────────────────────────────────────────────
    phone_digits = re.sub(r'\D', '', ctx.phone)

    # ── Brand certification bar ───────────────────────────────────────────────
    if brand_logo:
        brand_display = brand_slug.replace("-", " ").title()
        cert_label_txt = f"{brand_display} Certified Repair"
        cert_img_jsx = ""
        if cert_img:
            cert_img_jsx = f"""
            <div className="hidden md:block">
              <Image src={json.dumps(cert_img)} alt={json.dumps(cert_label_txt + " certificate")}
                     width={{200}} height={{120}} className="object-contain rounded-lg" />
            </div>"""
        brand_bar = f"""
      {{/* Brand Certification Bar */}}
      <section className="bg-gray-50 border-b border-gray-200 py-6 px-6">
        <div className="max-w-5xl mx-auto flex flex-col md:flex-row items-center justify-between gap-6">
          <div className="flex items-center gap-4">
            <Image src={json.dumps(brand_logo)} alt={json.dumps(cert_label_txt)}
                   width={{140}} height={{70}} className="object-contain" />
            <div>
              <p className="font-bold text-[{primary}] text-lg">{cert_label_txt}</p>
              <p className="text-gray-500 text-sm">{ctx.address}</p>
            </div>
          </div>
          <div className="flex gap-8 text-center">{cert_img_jsx}
            <div><p className="text-xl font-bold text-[{primary}]">OEM</p>
                 <p className="text-xs text-gray-500 uppercase tracking-wide">Parts Only</p></div>
            <div><p className="text-xl font-bold text-[{primary}]">Free</p>
                 <p className="text-xs text-gray-500 uppercase tracking-wide">Estimates</p></div>
            <div><p className="text-xl font-bold text-[{primary}]">Lifetime</p>
                 <p className="text-xs text-gray-500 uppercase tracking-wide">Warranty</p></div>
          </div>
        </div>
      </section>"""
    else:
        brand_bar = ""

    # ── Gallery ───────────────────────────────────────────────────────────────
    gallery_imgs = [(g[0], g[1]) for g in gallery_df[:3]]
    gallery_divs = ""
    for idx, (img_src, img_alt) in enumerate(gallery_imgs):
        extra = ' hidden md:block' if idx == 2 else ''
        gallery_divs += f"""
      <div className="relative h-48 rounded-xl overflow-hidden{extra}">
        <Image src={json.dumps(img_src)} alt={json.dumps(img_alt)}
               fill className="object-cover hover:scale-105 transition-transform duration-300" />
      </div>"""
    cols = "grid-cols-2 md:grid-cols-3" if len(gallery_imgs) >= 3 else "grid-cols-2"
    gallery_block = f"""
      {{/* Image Gallery */}}
      <section className="py-10 px-6 bg-gray-50">
        <div className="max-w-5xl mx-auto grid {cols} gap-4">{gallery_divs}
        </div>
      </section>"""

    # ── Stats bar JSX ─────────────────────────────────────────────────────────
    stats_divs = "".join(
        f"""          <div>
            <p className="text-3xl font-bold text-[{secondary}]">{s['value']}</p>
            <p className="text-sm text-gray-300 uppercase tracking-wide mt-1">{s['label']}</p>
          </div>"""
        for s in stats
    )

    # ── Trust bar JSX ─────────────────────────────────────────────────────────
    trust_spans = "".join(
        f'          <span className="flex items-center gap-2">'
        f'<span className="text-green-500 font-bold">✓</span> {item}</span>\n'
        for item in trust
    )

    # ── Area label for bottom CTA ─────────────────────────────────────────────
    service_area = ", ".join(ctx.service_cities[:4]) if ctx.service_cities else location

    return f"""import Image from 'next/image';

export const metadata = {{
  title: {json.dumps(meta_title)},
  description: {json.dumps(meta_desc)},
  keywords: {json.dumps(keyword)},
  alternates: {{
    canonical: {json.dumps(canonical)},
  }},
  openGraph: {{
    title: {json.dumps(meta_title)},
    description: {json.dumps(meta_desc)},
    url: {json.dumps(domain or "/")},
    siteName: {json.dumps(ctx.client_name)},
    locale: 'en_US',
    type: 'website',
  }},
}};

export default function {fn_name}() {{
  const h1Text = {json.dumps(h1_raw)};
  const location = {json.dumps(location)};
  const metaDesc = {json.dumps(meta_desc)};
  const contentHtml = {json.dumps(content_body)};

  return (
    <div className="min-h-screen bg-white">

      {{/* ── Hero ────────────────────────────────────────── */}}
      <section className="relative bg-[{primary}] overflow-hidden">
        <div className="relative w-full h-[50vh] md:h-[60vh]">
          <Image src={json.dumps(hero_img)}
                 alt={json.dumps(f"{ctx.services[0] if ctx.services else 'Service'} in {{location}} - {{ctx.client_name}}")}
                 fill className="object-cover opacity-50" priority />
          <div className="absolute inset-0 flex flex-col justify-center items-center px-6 text-center">
            <p className="text-[{secondary}] font-bold text-sm uppercase tracking-widest mb-3">
              {ctx.client_name} &middot; Serving {{location}}
            </p>
            <h1 className="text-3xl md:text-5xl lg:text-6xl font-bold text-white uppercase mb-4 max-w-4xl leading-tight">
              {{h1Text}}
            </h1>
            <p className="text-lg md:text-xl text-gray-200 mb-8 max-w-2xl">
              {{metaDesc}}
            </p>
            <div className="flex flex-col sm:flex-row gap-4 justify-center">
              <a href={json.dumps(cta_link)}
                 className="bg-[{secondary}] text-white font-bold py-4 px-8 rounded-lg text-lg hover:opacity-90 transition-opacity uppercase">
                {cta_label}
              </a>
              <a href={json.dumps(f"tel:{phone_digits}")}
                 className="border-2 border-white text-white font-bold py-4 px-8 rounded-lg text-lg hover:bg-white hover:text-[{primary}] transition-colors uppercase">
                Call {ctx.phone}
              </a>
            </div>
          </div>
        </div>
      </section>
{brand_bar}
{gallery_block}

      {{/* ── Stats Bar ──────────────────────────────────── */}}
      <section className="bg-[{primary}] py-8 px-6">
        <div className="max-w-5xl mx-auto grid grid-cols-2 md:grid-cols-4 gap-6 text-center">
{stats_divs}
        </div>
      </section>

      {{/* ── Trust Bar ───────────────────────────────────── */}}
      <section className="border-b border-gray-100 bg-white py-5 px-6">
        <div className="max-w-5xl mx-auto flex flex-wrap justify-center gap-6 text-sm text-gray-500">
{trust_spans}        </div>
      </section>

      {{/* ── SEO Content ─────────────────────────────────── */}}
      <div
        className="max-w-4xl mx-auto px-6 py-14
          [&_p]:mb-5 [&_p]:leading-relaxed [&_p]:text-gray-700 [&_p]:text-[1.05rem]
          [&_h2]:text-2xl [&_h2]:font-bold [&_h2]:text-[{primary}] [&_h2]:mt-12 [&_h2]:mb-4 [&_h2]:pb-2 [&_h2]:border-b [&_h2]:border-gray-100
          [&_h3]:text-xl [&_h3]:font-semibold [&_h3]:text-[{primary}] [&_h3]:mt-8 [&_h3]:mb-3
          [&_ul]:mb-6 [&_ul]:pl-6 [&_ul]:space-y-2
          [&_li]:text-gray-700 [&_li]:leading-relaxed [&_li]:list-disc
          [&_ol]:mb-6 [&_ol]:pl-6 [&_ol]:space-y-2 [&_ol_li]:list-decimal
          [&_strong]:font-semibold [&_strong]:text-gray-900
          [&_blockquote]:border-l-4 [&_blockquote]:border-[{secondary}] [&_blockquote]:pl-5 [&_blockquote]:italic [&_blockquote]:text-gray-600 [&_blockquote]:my-6 [&_blockquote]:py-3 [&_blockquote]:rounded-r-lg
          [&_a]:text-[{secondary}] [&_a]:underline [&_a]:font-medium
          [&_details]:mb-4 [&_details]:border [&_details]:border-gray-200 [&_details]:rounded-lg [&_details]:overflow-hidden
          [&_summary]:cursor-pointer [&_summary]:font-semibold [&_summary]:text-[{primary}] [&_summary]:px-5 [&_summary]:py-4 [&_summary]:bg-gray-50 [&_summary]:hover:bg-gray-100 [&_summary]:select-none [&_summary]:list-none
          [&_details_p]:px-5 [&_details_p]:py-4 [&_details_p]:mb-0 [&_details_p]:text-gray-700
          [&_table]:w-full [&_table]:border-collapse [&_table]:mb-6
          [&_th]:bg-[{primary}] [&_th]:text-white [&_th]:px-4 [&_th]:py-2 [&_th]:text-left [&_th]:text-sm
          [&_td]:border [&_td]:border-gray-200 [&_td]:px-4 [&_td]:py-2 [&_td]:text-gray-700 [&_td]:text-sm"
        dangerouslySetInnerHTML={{{{ __html: contentHtml }}}}
      />

      {{/* ── Bottom CTA ──────────────────────────────────── */}}
      <section className="bg-[{primary}] py-16 px-6">
        <div className="max-w-3xl mx-auto text-center">
          <h2 className="text-3xl md:text-4xl font-bold text-white mb-4 uppercase">
            {cta_label} &mdash; Serving {{location}}
          </h2>
          <p className="text-gray-300 mb-2 text-lg">
            Serving {{location}} and {service_area}.
          </p>
          <p className="text-gray-400 text-sm mb-8">
            {ctx.address} &nbsp;&middot;&nbsp; {ctx.phone}
          </p>
          <div className="flex flex-col sm:flex-row gap-4 justify-center">
            <a href={json.dumps(cta_link)}
               className="bg-[{secondary}] text-white font-bold py-4 px-10 rounded-lg text-xl hover:opacity-90 transition-opacity uppercase">
              {cta_label}
            </a>
            <a href={json.dumps(f"tel:{phone_digits}")}
               className="border-2 border-white text-white font-bold py-4 px-10 rounded-lg text-xl hover:bg-white hover:text-[{primary}] transition-colors uppercase">
              {ctx.phone}
            </a>
          </div>
        </div>
      </section>

      {{/* ── Schema.org JSON-LD ──────────────────────────── */}}
      <script type="application/ld+json"
              dangerouslySetInnerHTML={{{{ __html: {json.dumps(json.dumps(schema))} }}}} />

    </div>
  );
}}
"""
