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
import random
import requests
from denzo.agents.base_agent import TenantAwareBaseAgent, ClientContext, db_execute, db_write, build_llms_txt, validate_page_quality


def _has_h1(content: str) -> bool:
    """Check if HTML content already contains an <h1> tag."""
    import re
    return bool(re.search(r'<h1[\s>]', content, re.IGNORECASE))


def _build_html_page(title, meta_description, content, style_guide=None, ctx=None, canonical_url=None):
    """
    Build a fully-styled, brand-aware HTML page.
    Uses style_guide (from site_style_guide setting) for brand colors/fonts.
    Falls back to sensible defaults if no style guide.
    """
    from html import escape
    import re
    title = escape(title or "", quote=True)
    meta_description = escape(meta_description or "", quote=True)
    sg = style_guide or {}
    primary_colors = sg.get("primary_colors") or []
    accent_colors  = sg.get("accent_colors") or []

    # Brand colors — primary first, then accent, then generic fallback
    c1  = primary_colors[0] if len(primary_colors) > 0 else "#101330"  # navy/dark
    c2  = primary_colors[1] if len(primary_colors) > 1 else "#20b69e"  # teal/cta
    c3  = primary_colors[2] if len(primary_colors) > 2 else "#6f42c1"  # accent
    ca  = accent_colors[0]  if len(accent_colors)  > 0 else c2

    c1,c2,c3,ca = [v if re.fullmatch(r'#[0-9a-fA-F]{3,8}',str(v)) else '#101330' for v in (c1,c2,c3,ca)]
    domain      = (ctx.domain if ctx else "").rstrip("/")
    client_name = escape(ctx.client_name if ctx else "Denzo Studios")
    phone       = escape(ctx.phone if ctx else "",quote=True)
    phone_raw   = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    tagline     = escape(ctx.tagline if ctx else "")
    certifications = getattr(ctx, "certifications", []) or []

    # Build logo text (e.g. "Denzo <span>Studios</span>")
    parts = client_name.split()
    if len(parts) >= 2:
        logo_text = parts[0] + " <span>" + " ".join(parts[1:]) + "</span>"
    else:
        logo_text = client_name

    # Phone line in footer
    phone_line = f'<p style="margin-top:.75rem;"><a href="tel:{phone_raw}" style="color:var(--cta);font-weight:600;">{phone}</a></p>' if phone else ""

    from html import escape
    from denzo.urls import public_page_url,site_base_url
    domain = site_base_url(ctx) if ctx else domain
    links=[]
    if ctx:
        for row in db_execute("SELECT * FROM pages WHERE tenant_id=? AND type='service' AND status IN ('ready','published','live_external') ORDER BY id LIMIT 6",(ctx.tenant_id,)):
            links.append(f'<a href="{escape(public_page_url(row,ctx),quote=True)}">{escape(row["title"])}</a>')
    nav_links=''.join(links[:4])
    footer_services=''.join(links)
    # Certifications line for footer bottom
    certs_line = escape(" · ".join(certifications[:3])) if certifications else ""

    canonical = escape(canonical_url or "",quote=True)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} | {client_name}</title>
  <meta name="description" content="{meta_description}">
  {f'<link rel="canonical" href="{canonical}">' if canonical else ''}
  <meta property="og:title" content="{title} | {client_name}">
  <meta property="og:description" content="{meta_description}">
  {f'<meta property="og:url" content="{canonical}">' if canonical else ''}
  <meta property="og:type" content="website">
  <meta name="twitter:card" content="summary">
  <meta name="twitter:title" content="{title} | {client_name}">
  <meta name="twitter:description" content="{meta_description}">
  <link rel="preconnect" href="https://fonts.googleapis.com" crossorigin>
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link rel="preload" as="font" type="font/woff2" href="https://fonts.gstatic.com/s/inter/v13/UcCO3FwrK3iLTeHuS_fvQtMwCp50KnMw2boKoduKmMEVuLyfAZ9hiJ-Ek-_EeA.woff2" crossorigin>
  <link rel="preload" as="style" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" onload="this.onload=null;this.rel='stylesheet'">
  <noscript><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap"></noscript>
  <style>:root{{--primary:{c1};--cta:{c2};--accent:{c3};--text:#1a1a2e;--muted:#64748b;--bg:#ffffff;--bg2:#f8fafc;--border:#e2e8f0;--radius:10px;font-display:swap;}}</style>
  <link rel="stylesheet" href="/site/css/denzo-pages.css">
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
{f'<h1>{title}</h1>' if not _has_h1(content) else ''}
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

<script>
  // CWV: Apply loading: lazy (loading="lazy") to all content images
  document.querySelectorAll('main img:not([loading])').forEach(function(img,i){{img.setAttribute('loading',i===0?'eager':'lazy');}});
</script>
</body>
</html>"""


def _build_html_wrapper_compat(title, meta_description, content, style_guide=None, ctx=None):
    """Compatibility wrapper keeping the old signature."""
    return _build_html_page(title, meta_description, content, style_guide, ctx)


class GitHubPublisher(TenantAwareBaseAgent):

    PREREQUISITES = ["Programmatic SEO"]

    MAX_PAGES_PER_DAY = 30
    MIN_DELAY_SECONDS = 30
    MAX_DELAY_SECONDS = 90

    def __init__(self, ctx: ClientContext):
        super().__init__("GitHub Publisher", ctx, layer=5, color="slate")

    def _load_velocity_settings(self):
        import json as _json
        rows = db_execute(
            "SELECT value FROM settings WHERE tenant_id=? AND key='publish_velocity'",
            (self.ctx.tenant_id,)
        )
        if rows:
            try:
                overrides = _json.loads(rows[0]["value"])
                self.MAX_PAGES_PER_DAY = overrides.get("max_per_day", self.MAX_PAGES_PER_DAY)
                self.MIN_DELAY_SECONDS = overrides.get("min_delay", self.MIN_DELAY_SECONDS)
                self.MAX_DELAY_SECONDS = overrides.get("max_delay", self.MAX_DELAY_SECONDS)
            except Exception:
                pass

    def _pages_published_today(self) -> int:
        rows = db_execute(
            """SELECT COUNT(*) n FROM pages
               WHERE tenant_id=? AND status='published'
               AND published_at >= datetime('now', '-24 hours')""",
            (self.ctx.tenant_id,)
        )
        return rows[0]["n"] if rows else 0

    def _check_path_ownership(self, path: str) -> str:
        """
        Check managed_paths table. Returns:
          'ours'     — DENZO created this, safe to overwrite
          'theirs'   — pre-existing client content, DO NOT TOUCH
          'unknown'  — not in manifest, needs discovery
        """
        rows = db_execute(
            "SELECT managed FROM managed_paths WHERE tenant_id=? AND publisher='github' AND path=?",
            (self.ctx.tenant_id, path)
        )
        if rows:
            return 'theirs' if rows[0]['managed'] == 0 else 'ours'
        return 'unknown'

    def _publish_file(self, session: requests.Session, repo: str, branch: str,
                      path: str, content_b64: str, message: str,
                      content_hash: str = None) -> bool:
        if any(segment in (".","..") for segment in path.split("/")):
            raise ValueError("Invalid repository path")
        content_hash = content_hash or self.compute_content_hash(base64.b64decode(content_b64).decode("utf-8"))
        api_url = f"https://api.github.com/repos/{repo}/contents/{path}"

        # ── Rule #1: Never overwrite what we don't own ────────────────────
        ownership = self._check_path_ownership(path)
        if ownership == 'theirs':
            self.log(f"⛔ SKIP {path}: pre-existing client content (managed=0)", "warning")
            return False

        try:
            r = session.get(api_url, params={"ref": branch}, timeout=20)
            if r.status_code not in (200,404):
                self.log(f"Cannot establish remote file ownership: HTTP {r.status_code}", "error")
                return False
            sha = r.json().get("sha") if r.status_code == 200 else None
        except Exception as e:
            self.log(f"GitHub GET error for {path}: {e}", "warning")
            return False

        # ── Rule #2: If file exists on GitHub but NOT in our manifest ─────
        if sha and ownership == 'unknown':
            # File exists on GitHub but we never tracked it → it's client content
            db_write(
                "INSERT OR REPLACE INTO managed_paths (tenant_id, publisher, path, managed, content_hash) "
                "VALUES (?, 'github', ?, 0, ?)",
                (self.ctx.tenant_id, path, content_hash or '')
            )
            self.log(f"⛔ SKIP {path}: discovered unmanaged file on GitHub → marked protected", "warning")
            return False

        # ── Rule #3: Idempotency — skip if content hasn't changed ─────────
        if content_hash and ownership == 'ours':
            existing_hash = db_execute(
                "SELECT content_hash FROM managed_paths WHERE tenant_id=? AND publisher='github' AND path=?",
                (self.ctx.tenant_id, path)
            )
            import hashlib
            intended = base64.b64decode(content_b64)
            remote_matches = sha == hashlib.sha1(b'blob '+str(len(intended)).encode()+b'\0'+intended).hexdigest()
            if existing_hash and existing_hash[0]['content_hash'] == content_hash and remote_matches:
                self.log(f"⚡ SKIP {path}: content unchanged (hash match)", "info")
                return True  # not an error — already published

        payload = {"message": message, "content": content_b64, "branch": branch}
        if sha:
            payload["sha"] = sha

        try:
            r = session.put(api_url, json=payload, timeout=30)
            ok = r.status_code in (200, 201)
            if ok and content_hash:
                # Update manifest
                db_write(
                    "INSERT OR REPLACE INTO managed_paths (tenant_id, publisher, path, page_id, managed, content_hash) "
                    "VALUES (?, 'github', ?, ?, 1, ?)",
                    (self.ctx.tenant_id, path, getattr(self, '_current_page_id', None), content_hash)
                )
            return ok
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
        from denzo.urls import site_base_url,public_page_url,page_directory,quality_document,schema_json
        from denzo.editorial import publishable,revision_hash
        from denzo.publication import reserve_publication,committed,failed,verify_publication,reconcile_publications
        from denzo.agents.layer4_publishing.nextjs_renderer import render_nextjs_page
        from bs4 import BeautifulSoup
        ctx=self.ctx
        if not ctx.github_repo or not ctx.github_token:
            self.set_status('skipped','GitHub repository/token not configured');return
        self.set_status('working','Checking approved revisions')
        repo,branch=ctx.github_repo,ctx.github_branch or 'main'
        fmt=ctx.github_format or 'html'
        if fmt not in ('html','nextjs'):
            self.set_status('error',f'Unsupported publisher format: {fmt}');return
        prefix=(ctx.github_path_prefix or '').strip('/')
        if prefix: prefix+='/'
        base=site_base_url(ctx)
        session=requests.Session()
        session.headers.update({'Authorization':f'Bearer {ctx.github_token}','Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'})
        if fmt=='nextjs':
            check=session.get(f'https://api.github.com/repos/{repo}/contents/{prefix}app/[locale]',params={'ref':branch},timeout=20)
            if check.status_code!=200:
                self.set_status('error','Configured Next.js app/[locale] directory was not found; check path prefix and locale routing');return
        reconcile_publications(ctx.tenant_id)
        self._load_velocity_settings()
        styles=self.load_output('site_style_guide') or {}
        assets=self._load_nextjs_assets() if fmt=='nextjs' else {}
        pages=[dict(r) for r in db_execute("SELECT * FROM pages WHERE tenant_id=? AND status='ready' ORDER BY id",(ctx.tenant_id,)) if publishable(r)]
        processed=errors=0
        for page in pages:
            if self.should_stop():break
            try:
                url=public_page_url(page,ctx)
                folder=page_directory(page.get('type'))
                slug=page['slug'].strip('/')
                if fmt=='nextjs':
                    path=f'{prefix}app/[locale]/{folder}/{slug}/page.jsx'
                    rendered=render_nextjs_page(page,ctx,assets)
                    quality_html=quality_document(page['content'],page,ctx)
                else:
                    path=f'{prefix}{folder}/{slug}.html'
                    from denzo.html_content import sanitize_fragment
                    rendered=_build_html_page(page.get('meta_title') or page['title'],page.get('meta_description') or '',sanitize_fragment(page['content']),styles,ctx,url)
                    soup=BeautifulSoup(rendered,'html.parser')
                    marker=soup.new_tag('meta',attrs={'name':'denzo-revision','content':revision_hash(page)})
                    soup.head.append(marker)
                    if page.get('schema_markup'):
                        script=soup.new_tag('script',type='application/ld+json');script.string=schema_json(page['schema_markup']);soup.head.append(script)
                    first=soup.find('img',src=True)
                    if first:
                        from urllib.parse import urljoin
                        soup.head.append(soup.new_tag('meta',attrs={'property':'og:image','content':urljoin(url,first['src'])}))
                    rendered=str(soup);quality_html=rendered
                issues=validate_page_quality(quality_html,page.get('type','page'),base_url=url)
                if issues:
                    self.log(f'Quality checks failed: {issues[:3]}','warning');errors+=1;continue
                attempt,current=reserve_publication(ctx.tenant_id,page['id'],'github',self.MAX_PAGES_PER_DAY,url)
                if revision_hash(current)!=revision_hash(page):
                    failed(attempt,'Content changed while rendering');continue
                self._current_page_id=page['id']
                if self.should_stop():
                    failed(attempt,'Cancelled before provider write');break
                ok=self._publish_file(session,repo,branch,path,base64.b64encode(rendered.encode()).decode(),f'SEO: {page["title"]}',self.compute_content_hash(rendered))
                if not ok:
                    failed(attempt,'GitHub file protected or write rejected');errors+=1;continue
                committed(attempt,url,f'{branch}:{path}')
                verify_publication(attempt)
                processed+=1
            except ValueError as exc:
                self.log(str(exc),'warning');errors+=1
        self._current_page_id=None
        # Static assets belong under public/ in Next.js projects.
        asset_prefix=prefix+'public/' if fmt=='nextjs' else prefix
        self._publish_sitemap(session,repo,branch,ctx,base,asset_prefix)
        self._publish_llms_txt(session,repo,branch,ctx,base,asset_prefix)
        self.set_status('error' if errors else 'done',f'{processed} committed; {errors} issues; deployment verification is separate')

    def _publish_sitemap(self, session, repo, branch, ctx, base_url, path_prefix):
        """Generate sitemap.xml from all published pages and push to GitHub."""
        from html import escape
        from datetime import datetime, timezone
        published_pages = db_execute(
            "SELECT slug, type, publish_url, updated_at FROM pages "
            "WHERE tenant_id=? AND status IN ('published','publishing') AND publish_url IS NOT NULL "
            "ORDER BY type, slug",
            (ctx.tenant_id,)
        )
        if not published_pages:
            return

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        urls = []
        for p in published_pages:
            pub_url = escape(p["publish_url"], quote=True)
            ptype   = p["type"] or "page"
            priority = "1.0" if ptype in ("service", "location") else "0.8"
            changefreq = "weekly" if ptype in ("service", "location") else "monthly"
            urls.append(
                f"  <url>\n"
                f"    <loc>{pub_url}</loc>\n"
                f"    <lastmod>{(p['updated_at'] or today)[:10]}</lastmod>\n"
                f"    <changefreq>{changefreq}</changefreq>\n"
                f"    <priority>{priority}</priority>\n"
                f"  </url>"
            )

        sitemap_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(urls)
            + "\n</urlset>"
        )

        sm_b64 = base64.b64encode(sitemap_xml.encode("utf-8")).decode("utf-8")
        sm_path = f"{path_prefix}sitemap.xml" if path_prefix else "sitemap.xml"
        ok = self._publish_file(session, repo, branch, sm_path, sm_b64, "SEO: update sitemap.xml")
        if ok:
            self.log(f"✓ sitemap.xml ({len(published_pages)} URLs)", "success")
        else:
            self.log("✗ sitemap.xml publish failed", "warning")

        # robots.txt — only create if it doesn't already exist
        robots_content = (
            f"User-agent: *\nAllow: /\n\nSitemap: {base_url}/sitemap.xml\n"
        )
        rb_b64  = base64.b64encode(robots_content.encode("utf-8")).decode("utf-8")
        rb_path = f"{path_prefix}robots.txt" if path_prefix else "robots.txt"
        self._publish_file(session, repo, branch, rb_path, rb_b64, "SEO: update robots.txt")

    def _publish_llms_txt(self, session, repo, branch, ctx, base_url, path_prefix):
        """Generate and publish llms.txt — structured business data for AI crawlers
        (ChatGPT, Perplexity, Claude, Gemini) per the emerging llms.txt standard."""
        content = build_llms_txt(ctx, base_url=base_url)

        txt_b64  = base64.b64encode(content.encode("utf-8")).decode("utf-8")
        txt_path = f"{path_prefix}llms.txt" if path_prefix else "llms.txt"
        ok = self._publish_file(session, repo, branch, txt_path, txt_b64, "SEO: update llms.txt")
        if ok:
            self.log(f"✓ llms.txt published ({len(content)} chars)", "success")
        else:
            self.log("✗ llms.txt publish failed", "warning")
