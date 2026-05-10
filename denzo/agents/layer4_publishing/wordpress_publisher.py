"""
WordPress Publisher — Layer 4
Publishes pages to WordPress via the REST API using Application Passwords.
Upserts: if the slug already exists in WP, updates it. Otherwise creates new.
"""
import json
import time
import requests
from denzo.agents.base_agent import TenantAwareBaseAgent, ClientContext, db_execute, db_write


class WordPressPublisher(TenantAwareBaseAgent):

    def __init__(self, ctx: ClientContext):
        super().__init__("WordPress Publisher", ctx, layer=5, color="sky")

    # Sentinel returned when the lookup fails due to a network/server error
    _LOOKUP_ERROR = object()

    def _find_wp_page_by_slug(self, api_base: str, auth: tuple, slug: str):
        """
        Return the WP page dict if a page with this slug exists.
        Returns None if not found (404/empty list).
        Returns _LOOKUP_ERROR if the request fails — callers must NOT create on error
        to avoid duplicating pages on transient network failures.
        """
        try:
            r = requests.get(
                f"{api_base}/pages",
                auth=auth, timeout=15,
                params={"slug": slug, "per_page": 1, "status": "any"}
            )
            r.raise_for_status()
            data = r.json()
            if isinstance(data, list) and data:
                return data[0]
            return None
        except Exception:
            return self._LOOKUP_ERROR

    def run(self):
        self.log("WordPress Publisher starting...")
        self.set_status("working", "Checking configuration")
        ctx = self.ctx

        if not ctx.wp_url or not ctx.wp_user or not ctx.wp_app_password:
            self.log("WordPress credentials not configured. Set them in Settings.", "error")
            self.set_status("error", "Missing WordPress config")
            return

        wp_url   = ctx.wp_url.rstrip("/")
        api_base = f"{wp_url}/wp-json/wp/v2"
        auth     = (ctx.wp_user, ctx.wp_app_password)

        # Test connection
        try:
            test = requests.get(f"{api_base}/posts", auth=auth, timeout=10, params={"per_page": 1})
            if test.status_code == 401:
                self.log("WordPress authentication failed. Check username and app password.", "error")
                self.set_status("error", "Auth failed")
                return
        except Exception as e:
            self.log(f"Cannot reach WordPress: {e}", "error")
            self.set_status("error", str(e))
            return

        self.log(f"Connected to WordPress at {wp_url}")

        # Detect SEO plugin — Yoast or RankMath needed for meta fields to persist
        seo_plugin = None
        try:
            plugins_r = requests.get(
                f"{wp_url}/wp-json/wp/v2/plugins",
                auth=auth, timeout=10,
                params={"search": "seo", "per_page": 10, "status": "active"}
            )
            if plugins_r.status_code == 200:
                plugins = plugins_r.json()
                for p in (plugins if isinstance(plugins, list) else []):
                    slug = (p.get("plugin") or "").lower()
                    if "yoast" in slug or "wordpress-seo" in slug:
                        seo_plugin = "Yoast SEO"
                        break
                    if "rank-math" in slug or "seo-by-rank-math" in slug:
                        seo_plugin = "RankMath"
                        break
        except Exception:
            pass

        if seo_plugin:
            self.log(f"SEO plugin detected: {seo_plugin} — meta titles and descriptions will be saved.", "success")
        else:
            self.log(
                "WARNING: No Yoast SEO or RankMath plugin detected. "
                "Meta titles/descriptions will be set on the page excerpt only — "
                "install Yoast SEO or RankMath on WordPress for full SEO meta support.",
                "warning"
            )

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
            "SELECT id, title, slug, meta_title, meta_description, content FROM pages "
            "WHERE tenant_id=? AND status='ready' AND content IS NOT NULL AND content != '' ORDER BY id",
            (ctx.tenant_id,)
        )

        if not pages:
            self.log("No ready pages to publish.", "warning")
            self.set_status("idle", "No ready pages")
            return

        self.log(f"Publishing {len(pages)} pages to WordPress (upsert mode)...")
        published = 0
        updated   = 0
        failed    = 0

        for page in pages:
            if self.should_stop():
                break

            page_dict = dict(page)
            title     = page_dict.get("title", "")
            slug      = page_dict.get("slug", "").lstrip("/")
            content   = page_dict.get("content", "")
            meta_desc = page_dict.get("meta_description", "")

            self.set_status("working", f"Publishing: {title[:50]}")

            # Auto-generate meta description from content if missing
            if not meta_desc and content:
                import re as _re
                # Extract first meaningful paragraph text
                texts = _re.findall(r'<p[^>]*>(.*?)</p>', content, _re.DOTALL)
                for t in texts:
                    clean = _re.sub(r'<[^>]+>', '', t).strip()
                    if len(clean) > 60:
                        meta_desc = clean[:155].rsplit(' ', 1)[0]
                        break

            # Build meta title: keep under 60 chars
            meta_title = page_dict.get("meta_title") or title
            if len(meta_title) > 60:
                meta_title = meta_title[:57] + "..."

            # Strip full HTML document wrappers — Claude occasionally generates these
            # even when told to output only fragments.
            import re as _re
            # Remove <!DOCTYPE ...>, <html ...>, <head>...</head>, <body ...>, </body>, </html>
            content = _re.sub(r'<!DOCTYPE[^>]*>', '', content, flags=_re.IGNORECASE)
            content = _re.sub(r'<html[^>]*>', '', content, flags=_re.IGNORECASE)
            content = _re.sub(r'</html>', '', content, flags=_re.IGNORECASE)
            content = _re.sub(r'<head>.*?</head>', '', content, flags=_re.IGNORECASE | _re.DOTALL)
            content = _re.sub(r'<body[^>]*>', '', content, flags=_re.IGNORECASE)
            content = _re.sub(r'</body>', '', content, flags=_re.IGNORECASE)
            content = content.strip()

            # Wrap in Gutenberg HTML block to prevent WordPress wpautop() from
            # mangling the custom HTML structure (divs, classes, nested elements).
            # Without this, WP converts double newlines to <p> tags and breaks the layout.
            if not content.startswith('<!-- wp:html -->'):
                content = f'<!-- wp:html -->\n{content}\n<!-- /wp:html -->'

            payload = {
                "title":   title,
                "slug":    slug,
                "content": content,
                "status":  "publish",
                "excerpt": meta_desc,
                # Yoast SEO meta (works when Yoast is installed)
                "meta": {
                    "_yoast_wpseo_metadesc":              meta_desc,
                    "_yoast_wpseo_title":                 meta_title,
                    "_yoast_wpseo_opengraph-description": meta_desc,
                    "_yoast_wpseo_opengraph-title":       meta_title,
                    # RankMath fallback
                    "rank_math_description":              meta_desc,
                    "rank_math_title":                    meta_title,
                },
            }

            try:
                # Check if page already exists by slug
                existing = self._find_wp_page_by_slug(api_base, auth, slug)

                if existing is self._LOOKUP_ERROR:
                    self.log(f"✗ Lookup error for '{slug}' — skipping to avoid duplicates", "error")
                    failed += 1
                    continue
                elif existing:
                    # UPDATE existing page
                    wp_id = existing["id"]
                    r = requests.post(f"{api_base}/pages/{wp_id}", auth=auth, json=payload, timeout=30)
                    action = "updated"
                else:
                    # CREATE new page
                    r = requests.post(f"{api_base}/pages", auth=auth, json=payload, timeout=30)
                    action = "published"

                if r.status_code == 429:
                    # Host rate limit (WP Engine, Kinsta throttle REST API)
                    retry_after = int(r.headers.get("Retry-After", 60))
                    self.log(f"Rate limited by WordPress — waiting {retry_after}s", "warning")
                    time.sleep(retry_after)
                    # Retry once
                    if action == "updated":
                        r = requests.post(f"{api_base}/pages/{wp_id}", auth=auth, json=payload, timeout=30)
                    else:
                        r = requests.post(f"{api_base}/pages", auth=auth, json=payload, timeout=30)

                if r.status_code in (200, 201):
                    wp_data    = r.json()
                    public_url = wp_data.get("link", "")
                    wp_post_id = str(wp_data.get("id", ""))
                    db_write(
                        "UPDATE pages SET status='published', publish_url=?, publish_ref=?, "
                        "updated_at=CURRENT_TIMESTAMP WHERE id=? AND tenant_id=?",
                        (public_url, wp_post_id, page_dict["id"], ctx.tenant_id)
                    )
                    self.log(f"✓ {action.capitalize()}: {title} → {public_url}", "success")
                    if action == "updated":
                        updated += 1
                    else:
                        published += 1
                else:
                    err_detail = ""
                    try:
                        err_detail = r.json().get("message", "")[:80]
                    except Exception:
                        pass
                    self.log(f"✗ Failed ({r.status_code}): {title} — {err_detail}", "error")
                    failed += 1

            except Exception as e:
                self.log(f"Error publishing {title}: {e}", "error")
                failed += 1

            time.sleep(0.8)  # Respectful pacing — prevent REST API flooding

        _published = published + updated

        # Ping Google / Bing with the native WP sitemap after publishing
        if _published > 0 and getattr(ctx, "domain", None):
            try:
                sitemap_url = f"{ctx.domain.rstrip('/')}/wp-sitemap.xml"
                requests.get(
                    f"https://www.google.com/ping?sitemap={sitemap_url}",
                    timeout=5
                )
                self.log(f"✓ Pinged Google with sitemap: {sitemap_url}", "info")
            except Exception:
                pass
            try:
                sitemap_url = f"{ctx.domain.rstrip('/')}/wp-sitemap.xml"
                requests.get(
                    f"https://www.bing.com/ping?sitemap={sitemap_url}",
                    timeout=5
                )
                self.log("✓ Pinged Bing with sitemap", "info")
            except Exception:
                pass

        # Publish llms.txt as a WordPress page (slug: llms-txt) so AI crawlers can
        # discover it at /llms-txt/ (or via a server rewrite to /llms.txt)
        if _published > 0:
            try:
                llms_content = self._build_llms_content(ctx)
                llms_payload = {
                    "title":   "llms.txt",
                    "slug":    "llms-txt",
                    "content": f"<!-- wp:html -->\n<pre>{llms_content}</pre>\n<!-- /wp:html -->",
                    "status":  "publish",
                    "excerpt": f"Structured business data for AI language models — {ctx.client_name}",
                }
                existing_llms = self._find_wp_page_by_slug(api_base, auth, "llms-txt")
                if existing_llms is self._LOOKUP_ERROR:
                    self.log("llms.txt page: lookup error — skipped", "warning")
                elif existing_llms:
                    r_llms = requests.post(
                        f"{api_base}/pages/{existing_llms['id']}",
                        auth=auth, json=llms_payload, timeout=30
                    )
                    if r_llms.status_code in (200, 201):
                        self.log("✓ llms.txt page updated in WordPress", "success")
                    else:
                        self.log(f"llms.txt update failed ({r_llms.status_code})", "warning")
                else:
                    r_llms = requests.post(
                        f"{api_base}/pages",
                        auth=auth, json=llms_payload, timeout=30
                    )
                    if r_llms.status_code in (200, 201):
                        self.log("✓ llms.txt page created in WordPress", "success")
                    else:
                        self.log(f"llms.txt creation failed ({r_llms.status_code})", "warning")
            except Exception as e:
                self.log(f"llms.txt page skipped: {e}", "info")

        self.log(
            f"WordPress Publisher done: {published} new, {updated} updated, {failed} failed.",
            "success"
        )
        self.set_status("done", f"{published} new · {updated} updated · {failed} failed")

    def _build_llms_content(self, ctx) -> str:
        """Build llms.txt markdown content from ClientContext.
        Shared format with GitHubPublisher._publish_llms_txt for consistency."""
        services_list = "\n".join(f"- {s}" for s in (ctx.services or []))
        cities = ([ctx.primary_city] if getattr(ctx, "primary_city", None) else []) + \
                 (getattr(ctx, "service_cities", None) or [])
        cities_list = "\n".join(f"- {c}" for c in cities if c)
        certs_list  = "\n".join(f"- {c}" for c in (getattr(ctx, "certifications", None) or []))
        diffs_list  = "\n".join(f"- {d}" for d in (getattr(ctx, "differentiators", None) or []))
        ins_list    = "\n".join(f"- {i}" for i in (getattr(ctx, "insurance_partners", None) or []))

        primary_city = getattr(ctx, "primary_city", "") or ""
        state        = getattr(ctx, "state", "") or ""
        address      = getattr(ctx, "address", "") or (f"{primary_city}, {state}".strip(", "))
        tagline      = getattr(ctx, "tagline", "") or ""
        description  = getattr(ctx, "description", "") or \
                       f"{ctx.client_name} is a trusted local business serving {primary_city} and surrounding areas."

        services_preview = ", ".join((ctx.services or [])[:2])
        default_tagline  = f"Professional {services_preview} services in {primary_city}, {state}".strip(", .")

        content = f"""# {ctx.client_name}

> {tagline or default_tagline}

## About
{description}

## Services
{services_list or '- Professional services'}

## Locations Served
{cities_list or f'- {primary_city}'}

## Certifications & Credentials
{certs_list or '- Licensed and insured'}

## Why Choose Us
{diffs_list or '- Quality service'}

## Contact
- Phone: {ctx.phone or 'Call for info'}
- Website: {ctx.domain or ''}
- Address: {address}
"""
        if ins_list:
            content += f"\n## Insurance Partners\n{ins_list}\n"

        domain = getattr(ctx, "domain", "") or ""
        if domain:
            content += f"\n## Sitemap\n- {domain.rstrip('/')}/wp-sitemap.xml\n"

        return content
