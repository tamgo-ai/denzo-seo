"""
WordPress Publisher — Layer 4
Publishes pages to WordPress via the REST API using Application Passwords.
Upserts: if the slug already exists in WP, updates it. Otherwise creates new.
"""
import json
import time
import random
import requests
from denzo.agents.base_agent import TenantAwareBaseAgent, ClientContext, db_execute, db_write, strip_html_wrappers, build_llms_txt, validate_page_quality


class WordPressPublisher(TenantAwareBaseAgent):

    PREREQUISITES = ["Programmatic SEO"]

    # Velocity control defaults — prevents mass-publishing that Google penalizes
    MAX_PAGES_PER_DAY = 30       # max pages published per 24h window
    MIN_DELAY_SECONDS = 30       # minimum delay between page publishes
    MAX_DELAY_SECONDS = 90       # maximum delay between page publishes

    def __init__(self, ctx: ClientContext):
        super().__init__("WordPress Publisher", ctx, layer=5, color="sky")

    def _load_velocity_settings(self):
        """Load velocity control overrides from settings, if configured."""
        rows = db_execute(
            "SELECT value FROM settings WHERE tenant_id=? AND key='publish_velocity'",
            (self.ctx.tenant_id,)
        )
        if rows:
            try:
                overrides = json.loads(rows[0]["value"])
                self.MAX_PAGES_PER_DAY = overrides.get("max_per_day", self.MAX_PAGES_PER_DAY)
                self.MIN_DELAY_SECONDS = overrides.get("min_delay", self.MIN_DELAY_SECONDS)
                self.MAX_DELAY_SECONDS = overrides.get("max_delay", self.MAX_DELAY_SECONDS)
            except Exception:
                pass

    def _pages_published_today(self) -> int:
        """Count pages published in the last 24 hours for velocity control."""
        rows = db_execute(
            """SELECT COUNT(*) n FROM pages
               WHERE tenant_id=? AND status='published'
               AND published_at >= datetime('now', '-24 hours')""",
            (self.ctx.tenant_id,)
        )
        return rows[0]["n"] if rows else 0

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
            # Soft-skip — same rationale as GitHub Publisher. Missing creds is
            # a setup gap, not an agent fault. Marking 'done' lets the Director
            # advance to Layer 6 instead of blocking the entire pipeline.
            self.log(
                "WordPress credentials not configured. Skipping publish step. "
                "Pages remain in 'ready' state. Add wp_url + wp_user + "
                "wp_app_password in Settings → Publisher Configuration to "
                "enable real publishing.",
                "warning",
            )
            self.set_status("done", "Skipped — no WordPress config")
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
        self.log(
            "Meta fields sent for both Yoast SEO and RankMath — whichever is active will use them.",
            "info"
        )

        # Prereq check: need pages with status='ready'
        ready_check = db_execute(
            "SELECT COUNT(*) AS n FROM pages WHERE tenant_id=? AND status='ready' AND content IS NOT NULL AND content != '' "
            "AND (notes IS NULL OR notes NOT LIKE '%[PENDING_REVIEW]%')",
            (ctx.tenant_id,)
        )
        ready_count = ready_check[0]["n"] if ready_check else 0
        if ready_count == 0:
            self.log("No ready pages to publish. Run Programmatic SEO and content agents first.", "warning")
            self.set_status("idle", "No ready pages — run content agents first")
            return

        pages = db_execute(
            "SELECT id, title, slug, meta_title, meta_description, content FROM pages "
            "WHERE tenant_id=? AND status='ready' AND content IS NOT NULL AND content != '' "
            "AND (notes IS NULL OR notes NOT LIKE '%[PENDING_REVIEW]%') "
            "ORDER BY id",
            (ctx.tenant_id,)
        )

        if not pages:
            self.log("No ready pages to publish.", "warning")
            self.set_status("idle", "No ready pages")
            return

        self._load_velocity_settings()
        self.log(f"Publishing {len(pages)} pages to WordPress (upsert mode)...")
        self.log(
            f"Velocity control: max {self.MAX_PAGES_PER_DAY}/day, "
            f"{self.MIN_DELAY_SECONDS}-{self.MAX_DELAY_SECONDS}s between pages",
            "info"
        )
        published = 0
        updated   = 0
        failed    = 0
        today_count = self._pages_published_today()

        for page in pages:
            if self.should_stop():
                break

            # Velocity gate: don't exceed daily publishing limit
            if today_count + published >= self.MAX_PAGES_PER_DAY:
                remaining = len(pages) - published - updated - failed
                self.log(
                    f"Daily publishing limit reached ({self.MAX_PAGES_PER_DAY}/day). "
                    f"{remaining} pages remain in 'ready' state for next cycle.",
                    "warning"
                )
                break

            page_dict = dict(page)
            title     = page_dict.get("title", "")
            slug      = page_dict.get("slug", "").lstrip("/")
            content   = page_dict.get("content", "")
            meta_desc = page_dict.get("meta_description", "")

            self.set_status("working", f"Publishing: {title[:50]}")

            # ── Quality gate: skip pages that don't meet minimum standards ──
            ptype = page_dict.get("type", "service")
            issues = validate_page_quality(content, ptype)
            if issues:
                db_write(
                    "UPDATE pages SET notes=COALESCE(notes||' ','')||?, status='ready', "
                    "updated_at=CURRENT_TIMESTAMP WHERE id=? AND tenant_id=?",
                    (f"[QC_FAIL:{';'.join(issues[:3])}]", page_dict["id"], ctx.tenant_id)
                )
                self.log(f"✗ Quality gate failed: {title} — {', '.join(issues[:2])}", "warning")
                failed += 1
                continue

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
            content = strip_html_wrappers(content)

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

            # Velocity-controlled delay: random 30-90s between publishes to look natural.
            # Google's algorithm flags sites that publish hundreds of pages simultaneously.
            delay = random.randint(self.MIN_DELAY_SECONDS, self.MAX_DELAY_SECONDS)
            self.log(f"Velocity delay: {delay}s before next page...", "info")
            for _ in range(delay):
                if self.should_stop():
                    break
                time.sleep(1)

        _published = published + updated

        # Sitemap discovery: handled by Indexation Accelerator (Layer 5 post-publish).
        # It submits via IndexNow (Bing/Yandex/Seznam), Google Indexing API, and
        # Google Search Console API. Sitemap is also discoverable via robots.txt.
        if _published > 0:
            self.log(
                f"Sitemap at {ctx.domain.rstrip('/')}/wp-sitemap.xml — "
                f"Indexation Accelerator will submit to search engines.",
                "info"
            )

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
        """Build llms.txt markdown content from ClientContext. Delegates to shared utility."""
        domain = getattr(ctx, "domain", "") or ""
        return build_llms_txt(ctx, base_url=domain)
