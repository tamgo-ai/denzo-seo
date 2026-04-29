"""
GitHub Publisher — Layer 5
Publishes pages to a GitHub repository via the Contents API.
Supports two output formats:
  html   — standalone HTML file with inline CSS (default)
  nextjs — Next.js App Router page.jsx (for sites using Next.js + Tailwind)
"""
import json
import base64
import time
import requests
from denzo.agents.base_agent import TenantAwareBaseAgent, ClientContext, db_execute, db_write


def _build_html_page(title, meta_description, content, style_guide=None, ctx=None):
    """
    Build a fully-styled, brand-aware HTML page.
    Uses style_guide (from site_style_guide setting) for brand colors/fonts.
    Falls back to sensible defaults if no style guide.
    """
    sg = style_guide or {}
    primary_colors = sg.get("primary_colors") or []
    accent_colors  = sg.get("accent_colors") or []

    # Brand colors — primary first, then accent, then generic fallback
    c1  = primary_colors[0] if len(primary_colors) > 0 else "#101330"  # navy/dark
    c2  = primary_colors[1] if len(primary_colors) > 1 else "#20b69e"  # teal/cta
    c3  = primary_colors[2] if len(primary_colors) > 2 else "#6f42c1"  # accent
    ca  = accent_colors[0]  if len(accent_colors)  > 0 else c2

    domain      = (ctx.domain if ctx else "").rstrip("/")
    client_name = ctx.client_name if ctx else "Denzo Studios"
    phone       = ctx.phone if ctx else ""
    phone_raw   = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    tagline     = ctx.tagline if ctx else ""
    certifications = getattr(ctx, "certifications", []) or []

    # Build logo text (e.g. "Denzo <span>Studios</span>")
    parts = client_name.split()
    if len(parts) >= 2:
        logo_text = parts[0] + " <span>" + " ".join(parts[1:]) + "</span>"
    else:
        logo_text = client_name

    # Phone line in footer
    phone_line = f'<p style="margin-top:.75rem;"><a href="tel:{phone_raw}" style="color:var(--cta);font-weight:600;">{phone}</a></p>' if phone else ""

    # Nav from services
    services = (ctx.services[:4] if ctx and ctx.services else [])
    nav_links = "".join(
        f'<a href="{domain}/services/{s.lower().replace(" ", "-").replace("&", "and")}.html">{s}</a>'
        for s in services
    )

    # Footer services column
    footer_services = "".join(
        f'<a href="{domain}/services/{s.lower().replace(" ", "-").replace("&", "and")}.html">{s}</a>'
        for s in (ctx.services[:6] if ctx and ctx.services else [])
    )

    # Certifications line for footer bottom
    certs_line = " · ".join(certifications[:3]) if certifications else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} | {client_name}</title>
  <meta name="description" content="{meta_description}">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap">
  <style>
    :root {{
      --primary:   {c1};
      --cta:       {c2};
      --accent:    {c3};
      --text:      #1a1a2e;
      --muted:     #64748b;
      --bg:        #ffffff;
      --bg2:       #f8fafc;
      --border:    #e2e8f0;
      --radius:    10px;
    }}
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: 'Inter', system-ui, sans-serif; color: var(--text); background: var(--bg); line-height: 1.7; }}
    a {{ color: var(--cta); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    img {{ max-width: 100%; height: auto; }}

    /* ── HEADER ── */
    .site-header {{
      background: var(--primary);
      padding: 0 2rem;
      position: sticky; top: 0; z-index: 100;
      box-shadow: 0 2px 12px rgba(0,0,0,.3);
    }}
    .header-inner {{
      max-width: 1200px; margin: 0 auto;
      display: flex; align-items: center; justify-content: space-between;
      height: 64px;
    }}
    .site-logo {{
      font-size: 1.3rem; font-weight: 800; color: #fff; letter-spacing: -.03em;
    }}
    .site-logo span {{ color: var(--cta); }}
    .site-nav {{ display: flex; gap: 1.5rem; align-items: center; }}
    .site-nav a {{ color: rgba(255,255,255,.8); font-size: .85rem; font-weight: 500; transition: color .15s; }}
    .site-nav a:hover {{ color: #fff; text-decoration: none; }}
    .nav-cta {{
      background: var(--cta); color: #fff !important; padding: .45rem 1.1rem;
      border-radius: 6px; font-weight: 600 !important; font-size: .82rem !important;
    }}
    .nav-cta:hover {{ opacity: .9; }}

    /* ── LAYOUT ── */
    .page-wrap {{ max-width: 1200px; margin: 0 auto; padding: 0 1.5rem; }}

    /* ── HERO ── */
    .hero-section {{
      background: linear-gradient(135deg, var(--primary) 0%, #1a2550 60%, #0d1a3a 100%);
      color: #fff; padding: 5rem 1.5rem 4rem;
      text-align: center;
    }}
    .hero-section h1 {{
      font-size: clamp(1.8rem, 4vw, 3rem); font-weight: 800;
      line-height: 1.15; letter-spacing: -.03em; margin-bottom: 1.25rem;
    }}
    .hero-badge {{
      display: inline-block; background: rgba(255,255,255,.12);
      border: 1px solid rgba(255,255,255,.2); color: rgba(255,255,255,.9);
      padding: .35rem 1rem; border-radius: 20px; font-size: .8rem;
      font-weight: 600; letter-spacing: .04em; text-transform: uppercase;
      margin-bottom: 1.25rem;
    }}
    .hero-lead {{
      font-size: 1.15rem; color: rgba(255,255,255,.8);
      max-width: 680px; margin: 0 auto 2rem; line-height: 1.7;
    }}
    .btn-primary {{
      display: inline-block; background: var(--cta); color: #fff;
      padding: .85rem 2.2rem; border-radius: var(--radius);
      font-weight: 700; font-size: 1rem; transition: transform .15s, opacity .15s;
      box-shadow: 0 4px 15px rgba(0,0,0,.2);
    }}
    .btn-primary:hover {{ transform: translateY(-2px); opacity: .92; text-decoration: none; }}
    .trust-bar {{
      display: flex; flex-wrap: wrap; justify-content: center; gap: .75rem 2rem;
      margin-top: 2rem;
    }}
    .trust-bar span {{ font-size: .85rem; color: rgba(255,255,255,.7); }}
    .trust-bar span::before {{ content: "✓ "; color: var(--cta); font-weight: 700; }}

    /* ── STATS BAR ── */
    .stats-bar {{
      background: var(--primary); color: #fff;
      display: grid; grid-template-columns: repeat(auto-fit, minmax(140px,1fr));
      text-align: center; padding: 2.5rem 1.5rem; gap: 1rem;
    }}
    .stat strong {{ display: block; font-size: 2rem; font-weight: 800; color: var(--cta); line-height: 1; }}
    .stat span   {{ font-size: .8rem; color: rgba(255,255,255,.7); margin-top: .3rem; display: block; }}

    /* ── CONTENT SECTIONS ── */
    .intro-section, .content-section, section {{
      padding: 3.5rem 1.5rem;
    }}
    .intro-section:nth-child(even), section:nth-child(even) {{
      background: var(--bg2);
    }}
    .section-inner {{ max-width: 820px; margin: 0 auto; }}

    h2 {{
      font-size: 1.6rem; font-weight: 700; color: var(--primary);
      margin-bottom: 1rem; letter-spacing: -.02em;
      padding-bottom: .6rem; border-bottom: 3px solid var(--cta);
      display: inline-block;
    }}
    h3 {{ font-size: 1.15rem; font-weight: 600; color: var(--primary); margin: 1.5rem 0 .6rem; }}
    p  {{ margin-bottom: 1.25rem; color: #374151; line-height: 1.8; }}
    ul, ol {{ margin: 0 0 1.25rem 1.5rem; }}
    li {{ margin-bottom: .5rem; color: #374151; line-height: 1.7; }}

    /* ── CARDS / BENEFITS ── */
    .benefits-grid, .services-grid, .features-grid {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(240px,1fr));
      gap: 1.5rem; margin: 2rem 0;
    }}
    .benefit-card, .service-card, .feature-card {{
      background: var(--bg); border: 1px solid var(--border);
      border-radius: var(--radius); padding: 1.75rem;
      box-shadow: 0 1px 4px rgba(0,0,0,.05);
      transition: box-shadow .2s, transform .2s;
    }}
    .benefit-card:hover, .service-card:hover, .feature-card:hover {{
      box-shadow: 0 6px 24px rgba(0,0,0,.1); transform: translateY(-2px);
    }}
    .benefit-card h3, .service-card h3, .feature-card h3 {{
      font-size: 1rem; color: var(--primary); margin-bottom: .5rem;
    }}
    .card-icon {{ font-size: 1.75rem; margin-bottom: .75rem; }}

    /* ── CTA SECTION ── */
    .cta-section {{
      background: linear-gradient(135deg, var(--primary), #1a2550);
      color: #fff; text-align: center; padding: 4rem 1.5rem;
    }}
    .cta-section h2 {{
      color: #fff; border-color: var(--cta); font-size: 1.8rem; margin-bottom: 1rem;
    }}
    .cta-section p {{ color: rgba(255,255,255,.8); max-width: 560px; margin: 0 auto 2rem; }}

    /* ── FAQ ── */
    details {{ border: 1px solid var(--border); border-radius: var(--radius); margin-bottom: .75rem; overflow: hidden; }}
    summary {{
      padding: 1rem 1.25rem; font-weight: 600; cursor: pointer;
      list-style: none; display: flex; justify-content: space-between; align-items: center;
      background: var(--bg2); color: var(--primary);
    }}
    summary::-webkit-details-marker {{ display: none; }}
    summary::after {{ content: "+"; font-size: 1.25rem; color: var(--cta); }}
    details[open] summary::after {{ content: "−"; }}
    details > *:not(summary) {{ padding: 1rem 1.25rem; }}

    /* ── BLOCKQUOTE / TESTIMONIAL ── */
    blockquote {{
      border-left: 4px solid var(--cta); background: var(--bg2);
      padding: 1.25rem 1.5rem; margin: 1.5rem 0; border-radius: 0 var(--radius) var(--radius) 0;
      font-style: italic; color: #4b5563;
    }}
    blockquote cite {{ display: block; margin-top: .75rem; font-style: normal; font-weight: 600; font-size: .85rem; color: var(--primary); }}

    /* ── TABLE ── */
    table {{ width: 100%; border-collapse: collapse; margin: 1.5rem 0; font-size: .9rem; }}
    th {{ background: var(--primary); color: #fff; padding: .75rem 1rem; text-align: left; }}
    td {{ padding: .7rem 1rem; border-bottom: 1px solid var(--border); }}
    tr:nth-child(even) td {{ background: var(--bg2); }}

    /* ── BREADCRUMB ── */
    .breadcrumb {{
      padding: .75rem 1.5rem; background: var(--bg2); border-bottom: 1px solid var(--border);
      font-size: .8rem; color: var(--muted);
    }}
    .breadcrumb a {{ color: var(--muted); }}
    .breadcrumb span {{ margin: 0 .4rem; }}

    /* ── FOOTER ── */
    .site-footer {{
      background: var(--primary); color: rgba(255,255,255,.6);
      padding: 3rem 1.5rem; margin-top: 4rem;
    }}
    .footer-inner {{
      max-width: 1200px; margin: 0 auto;
      display: grid; grid-template-columns: 2fr 1fr 1fr; gap: 2rem;
    }}
    .footer-brand {{ color: #fff; font-size: 1.1rem; font-weight: 700; margin-bottom: .75rem; }}
    .footer-brand span {{ color: var(--cta); }}
    .footer-desc {{ font-size: .85rem; line-height: 1.7; }}
    .footer-col h4 {{ color: #fff; font-size: .85rem; font-weight: 600; margin-bottom: 1rem; text-transform: uppercase; letter-spacing: .06em; }}
    .footer-col a {{ display: block; color: rgba(255,255,255,.55); font-size: .82rem; margin-bottom: .5rem; }}
    .footer-col a:hover {{ color: #fff; text-decoration: none; }}
    .footer-bottom {{
      max-width: 1200px; margin: 2rem auto 0;
      padding-top: 1.5rem; border-top: 1px solid rgba(255,255,255,.1);
      display: flex; justify-content: space-between; align-items: center;
      font-size: .78rem;
    }}
    .footer-phone {{ color: var(--cta); font-weight: 600; }}

    /* ── RESPONSIVE ── */
    @media (max-width: 768px) {{
      .site-nav {{ display: none; }}
      .hero-section {{ padding: 3rem 1rem 2.5rem; }}
      .footer-inner {{ grid-template-columns: 1fr; }}
      .footer-bottom {{ flex-direction: column; gap: .5rem; text-align: center; }}
      .stats-bar {{ grid-template-columns: repeat(2, 1fr); }}
    }}
  </style>
</head>
<body>

<!-- HEADER -->
<header class="site-header">
  <div class="header-inner">
    <a href="{domain}" class="site-logo">{logo_text}</a>
    <nav class="site-nav">
      {nav_links}
      <a href="{domain}/contact" class="nav-cta">Free Audit →</a>
    </nav>
  </div>
</header>

<!-- BREADCRUMB -->
<div class="breadcrumb page-wrap">
  <a href="{domain}">Home</a><span>›</span>{title}
</div>

<!-- PAGE CONTENT -->
<main>
{content}
</main>

<!-- FOOTER -->
<footer class="site-footer">
  <div class="footer-inner">
    <div>
      <div class="footer-brand">{logo_text}</div>
      <p class="footer-desc">{tagline}</p>
      {phone_line}
    </div>
    <div class="footer-col">
      <h4>Services</h4>
      {footer_services}
    </div>
    <div class="footer-col">
      <h4>Contact</h4>
      <a href="{domain}">Website</a>
      <a href="tel:{phone_raw}">{phone}</a>
      <a href="{domain}/contact">Free Strategy Call</a>
    </div>
  </div>
  <div class="footer-bottom">
    <span>© 2026 {client_name}. All rights reserved.</span>
    <span>{certs_line}</span>
  </div>
</footer>

</body>
</html>"""


def _build_html_wrapper_compat(title, meta_description, content, style_guide=None, ctx=None):
    """Compatibility wrapper keeping the old signature."""
    return _build_html_page(title, meta_description, content, style_guide, ctx)


class GitHubPublisher(TenantAwareBaseAgent):

    def __init__(self, ctx: ClientContext):
        super().__init__("GitHub Publisher", ctx, layer=5, color="slate")

    def _publish_file(self, session: requests.Session, repo: str, branch: str,
                      path: str, content_b64: str, message: str) -> bool:
        api_url = f"https://api.github.com/repos/{repo}/contents/{path}"
        try:
            r = session.get(api_url, params={"ref": branch}, timeout=20)
            sha = r.json().get("sha") if r.status_code == 200 else None
        except Exception as e:
            self.log(f"GitHub GET error for {path}: {e}", "warning")
            sha = None

        payload = {"message": message, "content": content_b64, "branch": branch}
        if sha:
            payload["sha"] = sha

        try:
            r = session.put(api_url, json=payload, timeout=30)
            return r.status_code in (200, 201)
        except Exception as e:
            self.log(f"GitHub PUT error for {path}: {e}", "error")
            return False

    def _load_nextjs_assets(self) -> dict:
        """Load nextjs_assets settings for this tenant (empty dict if not set)."""
        row = db_execute(
            "SELECT value FROM settings WHERE tenant_id=? AND key='nextjs_assets'",
            (self.ctx.tenant_id,)
        )
        if row:
            try:
                return json.loads(row[0]["value"])
            except Exception:
                pass
        return {}

    def run(self):
        self.log("GitHub Publisher starting...")
        self.set_status("working", "Checking configuration")
        ctx = self.ctx

        if not ctx.github_repo or not ctx.github_token:
            self.log("GitHub repo or token not configured. Set them in Settings.", "error")
            self.set_status("error", "Missing GitHub config")
            return

        repo   = ctx.github_repo
        branch = ctx.github_branch or "main"
        token  = ctx.github_token
        fmt         = ctx.github_format or "html"
        path_prefix = getattr(ctx, "github_path_prefix", "") or ""  # e.g. "public/" for Next.js
        # Resolve base URL: pages_domain wins, fallback to domain, strip trailing slash
        _base = (ctx.pages_domain or ctx.domain or "").rstrip("/")

        self.log(f"Format: {fmt} → {repo} ({branch})")

        # Load site style guide for brand-aware HTML generation
        style_guide = {}
        sg_rows = db_execute(
            "SELECT value FROM settings WHERE tenant_id=? AND key='site_style_guide'",
            (ctx.tenant_id,)
        )
        if sg_rows:
            try:
                style_guide = json.loads(sg_rows[0]["value"])
            except Exception:
                pass

        session = requests.Session()
        session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        })

        # Prereq check: need pages with status='ready'
        ready_check = db_execute(
            "SELECT COUNT(*) AS n FROM pages WHERE tenant_id=? AND status='ready' AND content IS NOT NULL AND content != ''",
            (ctx.tenant_id,)
        )
        ready_count = ready_check[0]["n"] if ready_check else 0
        if ready_count == 0:
            self.log("No ready pages to publish. Run Programmatic SEO and content agents first.", "warning")
            self.set_status("idle", "No ready pages — run content agents first")
            return

        pages = db_execute(
            "SELECT id, title, slug, type, meta_title, meta_description, content FROM pages "
            "WHERE tenant_id=? AND status='ready' AND content IS NOT NULL AND content != '' "
            "ORDER BY id",
            (ctx.tenant_id,)
        )

        if not pages:
            self.log("No ready pages to publish. Run content agents first.", "warning")
            self.set_status("idle", "No ready pages")
            return

        self.log(f"Publishing {len(pages)} pages ({fmt} format)...")

        # Load Next.js assets once if needed
        nextjs_assets = self._load_nextjs_assets() if fmt == "nextjs" else {}
        if fmt == "nextjs":
            from denzo.agents.layer4_publishing.nextjs_renderer import render_nextjs_page

        published = 0
        failed    = 0

        for page in pages:
            if self.should_stop():
                break

            page_dict = dict(page)
            title     = page_dict.get("title", "Untitled")
            slug      = page_dict.get("slug", "page").lstrip("/")
            ptype     = page_dict.get("type", "page")
            meta_desc = page_dict.get("meta_description", title)
            content   = page_dict.get("content", "")

            if fmt == "nextjs":
                file_content = render_nextjs_page(page_dict, ctx, nextjs_assets)
                file_path    = f"app/{slug}/page.jsx"
                public_url   = f"{_base}/{slug}" if _base else f"/{slug}"
            else:
                file_content = _build_html_page(
                    title=title,
                    meta_description=meta_desc,
                    content=content,
                    style_guide=style_guide,
                    ctx=ctx,
                )
                file_path  = f"{path_prefix}{ptype}s/{slug}.html"
                public_url = f"{_base}/{ptype}s/{slug}.html" if _base else file_path

            content_b64 = base64.b64encode(file_content.encode("utf-8")).decode("utf-8")
            commit_msg  = f"SEO: {title}"

            self.set_status("working", f"Publishing: {title[:50]}")

            ok = self._publish_file(session, repo, branch, file_path, content_b64, commit_msg)
            if ok:
                db_write(
                    "UPDATE pages SET status='published', publish_url=?, updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=? AND tenant_id=?",
                    (public_url, page_dict["id"], ctx.tenant_id)
                )
                self.log(f"✓ {file_path}", "success")
                published += 1
            else:
                self.log(f"✗ Failed: {title}", "error")
                failed += 1

            time.sleep(0.5)

        self.log(f"GitHub Publisher done: {published} published, {failed} failed.", "success")
        self.set_status("done", f"{published} pages published to GitHub")
