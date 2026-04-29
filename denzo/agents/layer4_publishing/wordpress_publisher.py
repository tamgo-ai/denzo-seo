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
                    self.log(f"✗ Failed ({r.status_code}): {title}", "error")
                    failed += 1

            except Exception as e:
                self.log(f"Error publishing {title}: {e}", "error")
                failed += 1

            time.sleep(0.5)

        self.log(
            f"WordPress Publisher done: {published} new, {updated} updated, {failed} failed.",
            "success"
        )
        self.set_status("done", f"{published} new · {updated} updated · {failed} failed")
