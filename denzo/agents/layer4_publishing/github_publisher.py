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


def _build_html_page(title, meta_description, content, style_guide=None, ctx=None, canonical_url=None):
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

    canonical = canonical_url or ""
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
  <style>:root{--primary:{c1};--cta:{c2};--accent:{c3};--text:#1a1a2e;--muted:#64748b;--bg:#ffffff;--bg2:#f8fafc;--border:#e2e8f0;--radius:10px;font-display:swap;}</style>
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
  document.querySelectorAll('main img:not([loading])').forEach(function(img){{img.setAttribute('loading','lazy');}});
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
            # Soft-skip rather than hard-fail. Missing publisher config is a
            # client-side setup gap, not an agent failure — the Director treats
            # status='done' as a green-light to move on to Layer 6 instead of
            # stalling the whole pipeline.
            self.log(
                "GitHub repo/token not configured. Skipping publish step. "
                "Pages remain in 'ready' state. Add github_repo + github_token "
                "in Settings → Publisher Configuration to enable real publishing.",
                "warning",
            )
            self.set_status("done", "Skipped — no GitHub config")
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

        # Prereq check: need pages with status='ready' AND reviewed
        ready_check = db_execute(
            "SELECT COUNT(*) AS n FROM pages WHERE tenant_id=? AND status='ready' AND content IS NOT NULL AND content != '' "
            "AND (notes IS NULL OR notes NOT LIKE '%[PENDING_REVIEW]%')",
            (ctx.tenant_id,)
        )
        ready_count = ready_check[0]["n"] if ready_check else 0
        if ready_count == 0:
            self.log("No reviewed pages to publish. Review pages in the dashboard first.", "warning")
            self.set_status("idle", "No reviewed pages — review them first")
            return

        pages = db_execute(
            "SELECT id, title, slug, type, meta_title, meta_description, content FROM pages "
            "WHERE tenant_id=? AND status='ready' AND content IS NOT NULL AND content != '' "
            "AND (notes IS NULL OR notes NOT LIKE '%[PENDING_REVIEW]%') "
            "ORDER BY id",
            (ctx.tenant_id,)
        )

        if not pages:
            self.log("No ready pages to publish. Run content agents first.", "warning")
            self.set_status("idle", "No ready pages")
            return

        self._load_velocity_settings()
        self.log(f"Publishing {len(pages)} pages ({fmt} format)...")
        self.log(
            f"Velocity control: max {self.MAX_PAGES_PER_DAY}/day, "
            f"{self.MIN_DELAY_SECONDS}-{self.MAX_DELAY_SECONDS}s between pages",
            "info"
        )

        # Load Next.js assets once if needed
        nextjs_assets = self._load_nextjs_assets() if fmt == "nextjs" else {}
        if fmt == "nextjs":
            from denzo.agents.layer4_publishing.nextjs_renderer import render_nextjs_page

        published = 0
        failed    = 0
        today_count = self._pages_published_today()

        for page in pages:
            if self.should_stop():
                break

            if today_count + published >= self.MAX_PAGES_PER_DAY:
                remaining = len(pages) - published - failed
                self.log(
                    f"Daily publishing limit reached ({self.MAX_PAGES_PER_DAY}/day). "
                    f"{remaining} pages remain in 'ready' state for next cycle.",
                    "warning"
                )
                break

            page_dict = dict(page)
            title     = page_dict.get("title", "Untitled")
            ptype     = page_dict.get("type", "page")

            # ── Quality gate ──
            issues = validate_page_quality(page_dict.get("content", ""), ptype)
            if issues:
                db_write(
                    "UPDATE pages SET notes=COALESCE(notes||' ','')||?, status='ready', "
                    "updated_at=CURRENT_TIMESTAMP WHERE id=? AND tenant_id=?",
                    (f"[QC_FAIL:{';'.join(issues[:3])}]", page_dict["id"], ctx.tenant_id)
                )
                self.log(f"✗ Quality gate failed: {title} — {', '.join(issues[:2])}", "warning")
                failed += 1
                continue
            slug      = page_dict.get("slug", "page").lstrip("/")
            ptype     = page_dict.get("type", "page")
            meta_desc = page_dict.get("meta_description", title)
            content   = page_dict.get("content", "")

            if fmt == "nextjs":
                file_content = render_nextjs_page(page_dict, ctx, nextjs_assets)
                file_path    = f"app/{slug}/page.jsx"
                public_url   = f"{_base}/{slug}" if _base else f"/{slug}"
            else:
                file_path  = f"{path_prefix}{ptype}s/{slug}.html"
                public_url = f"{_base}/{ptype}s/{slug}.html" if _base else file_path
                file_content = _build_html_page(
                    title=title,
                    meta_description=meta_desc,
                    content=content,
                    style_guide=style_guide,
                    ctx=ctx,
                    canonical_url=public_url,
                )

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

            delay = random.randint(self.MIN_DELAY_SECONDS, self.MAX_DELAY_SECONDS)
            for _ in range(delay):
                if self.should_stop():
                    break
                time.sleep(1)

        # Generate and publish sitemap.xml + robots.txt
        if published > 0 and _base:
            self._publish_sitemap(session, repo, branch, ctx, _base, path_prefix)
            self._publish_llms_txt(session, repo, branch, ctx, _base, path_prefix)

        self.log(f"GitHub Publisher done: {published} published, {failed} failed.", "success")
        self.set_status("done", f"{published} pages published to GitHub")

    def _publish_sitemap(self, session, repo, branch, ctx, base_url, path_prefix):
        """Generate sitemap.xml from all published pages and push to GitHub."""
        from datetime import datetime, timezone
        published_pages = db_execute(
            "SELECT slug, type, publish_url, updated_at FROM pages "
            "WHERE tenant_id=? AND status='published' AND publish_url IS NOT NULL "
            "ORDER BY type, slug",
            (ctx.tenant_id,)
        )
        if not published_pages:
            return

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        urls = []
        for p in published_pages:
            pub_url = p["publish_url"]
            ptype   = p["type"] or "page"
            priority = "1.0" if ptype in ("service", "location") else "0.8"
            changefreq = "weekly" if ptype in ("service", "location") else "monthly"
            urls.append(
                f"  <url>\n"
                f"    <loc>{pub_url}</loc>\n"
                f"    <lastmod>{today}</lastmod>\n"
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
