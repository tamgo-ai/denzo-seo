"""
Indexation Accelerator — Layer 5 (runs after publishing)
=========================================================
Submits newly published pages to search engines for instant crawling:
- Google Indexing API (service account JSON key required)
- IndexNow protocol (Bing, Yandex, Seznam) — no auth needed
- Sitemap ping to Google + Bing

Velocity-controlled: submits in batches of 10-20 with randomized delays
to simulate natural publishing cadence. Google's algorithm detects
mass page dumps and may throttle crawling or apply algorithmic penalties.
"""
import json
import time
import random
import requests
import os
from datetime import datetime, timezone
from denzo.agents.base_agent import TenantAwareBaseAgent, ClientContext, db_execute, db_write


class IndexationAccelerator(TenantAwareBaseAgent):
    """Submits published pages to search engines for rapid indexation."""

    PREREQUISITES = ["Programmatic SEO"]  # runs after content exists

    BATCH_SIZE = 15      # pages per submission batch
    MIN_DELAY = 30       # minimum seconds between batches
    MAX_DELAY = 120      # maximum seconds between batches

    def __init__(self, ctx: ClientContext):
        super().__init__("Indexation Accelerator", ctx, layer=5, color="emerald")

    def _get_indexnow_key(self) -> str:
        """Load or generate the IndexNow API key. One key per tenant, stored in settings."""
        rows = db_execute(
            "SELECT value FROM settings WHERE tenant_id=? AND key='indexnow_key'",
            (self.ctx.tenant_id,)
        )
        if rows:
            try:
                return json.loads(rows[0]["value"]).get("key", "")
            except Exception:
                pass
        # Generate a new key (random 32-char hex)
        key = os.urandom(16).hex()
        db_write(
            "INSERT OR REPLACE INTO settings (tenant_id, key, value) VALUES (?,?,?)",
            (self.ctx.tenant_id, "indexnow_key",
             json.dumps({"key": key, "generated_at": datetime.now(timezone.utc).isoformat()}))
        )
        return key

    def _indexnow_submit(self, urls: list[str], key: str) -> int:
        """Submit URLs to IndexNow. Returns count of successfully submitted URLs.
        IndexNow notifies Bing, Yandex, Seznam simultaneously."""
        if not urls:
            return 0

        try:
            resp = requests.post(
                "https://api.indexnow.org/indexnow",
                json={
                    "host": (self.ctx.pages_domain or self.ctx.domain or "").replace("https://", "").replace("http://", "").split("/")[0],
                    "key": key,
                    "keyLocation": f"{(self.ctx.pages_domain or self.ctx.domain or '').rstrip('/')}/{key}.txt",
                    "urlList": urls,
                },
                headers={"Content-Type": "application/json"},
                timeout=15,
            )
            if resp.status_code in (200, 202, 204):
                return len(urls)
            else:
                self.log(f"IndexNow returned {resp.status_code}: {resp.text[:100]}", "warning")
                return 0
        except Exception as e:
            self.log(f"IndexNow submission error: {e}", "warning")
            return 0

    def _google_indexing_notify(self, urls: list[str]) -> int:
        """Submit URLs to Google Indexing API via organization-level service account.
        Automatically checks if domain is verified. Uses IndexNow as fallback.
        ONE service account covers ALL tenants. Zero per-tenant setup."""
        from denzo.agents.utils.google_verification import _get_credentials, is_domain_verified

        domain = (self.ctx.pages_domain or self.ctx.domain or "").replace("https://", "").replace("http://", "").split("/")[0]
        if not domain or not is_domain_verified(domain):
            return 0  # Domain not verified — IndexNow handles Bing/Yandex instead

        creds = _get_credentials(scopes=["https://www.googleapis.com/auth/indexing"])
        if not creds:
            return 0

        credentials = creds

        submitted = 0
        for url in urls:
            try:
                resp = requests.post(
                    "https://indexing.googleapis.com/v3/urlNotifications:publish",
                    json={"url": url, "type": "URL_UPDATED"},
                    headers={
                        "Authorization": f"Bearer {credentials.token}",
                        "Content-Type": "application/json",
                    },
                    timeout=10,
                )
                if resp.status_code == 200:
                    submitted += 1
                elif resp.status_code == 429:
                    self.log("Google Indexing API rate limited — pausing 60s", "warning")
                    time.sleep(60)
                elif resp.status_code == 403:
                    self.log(f"Google Indexing: domain not verified. Skipping.", "warning")
                    break
                else:
                    self.log(f"Google Indexing: HTTP {resp.status_code}", "warning")
            except Exception:
                pass

        return submitted

    def _publish_key_file(self, key: str) -> bool:
        """Publish the IndexNow key verification file to the domain root.
        IndexNow requires {key}.txt to be accessible at the domain root.
        For GitHub: commits the file to the repo. For WordPress: creates a page."""
        domain = (self.ctx.pages_domain or self.ctx.domain or "").rstrip("/")
        if not domain:
            return False

        publisher = self.ctx.publisher_type or "github"
        key_content = key  # the file only contains the key itself

        if publisher == "github" and self.ctx.github_repo and self.ctx.github_token:
            try:
                import base64
                session = requests.Session()
                session.headers.update({
                    "Authorization": f"Bearer {self.ctx.github_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28"
                })
                path = f"{key}.txt"
                api_url = f"https://api.github.com/repos/{self.ctx.github_repo}/contents/{path}"
                content_b64 = base64.b64encode(key_content.encode("utf-8")).decode("utf-8")

                # Check if file exists (for SHA)
                try:
                    r = session.get(api_url, params={"ref": self.ctx.github_branch or "main"}, timeout=15)
                    sha = r.json().get("sha") if r.status_code == 200 else None
                except Exception:
                    sha = None

                payload = {"message": "SEO: publish IndexNow key for Bing/Yandex indexation",
                          "content": content_b64, "branch": self.ctx.github_branch or "main"}
                if sha:
                    payload["sha"] = sha

                r = session.put(api_url, json=payload, timeout=20)
                if r.status_code in (200, 201):
                    self.log(f"✓ IndexNow key published: {domain}/{key}.txt", "success")
                    return True
                else:
                    self.log(f"IndexNow key publish failed: HTTP {r.status_code}", "warning")
            except Exception as e:
                self.log(f"IndexNow key publish error: {e}", "warning")

        elif publisher == "wordpress" and self.ctx.wp_url and self.ctx.wp_user and self.ctx.wp_app_password:
            try:
                wp_url = self.ctx.wp_url.rstrip("/")
                api_base = f"{wp_url}/wp-json/wp/v2"
                auth = (self.ctx.wp_user, self.ctx.wp_app_password)

                # Create a page with slug = the key
                r = requests.post(
                    f"{api_base}/pages",
                    auth=auth, timeout=15,
                    json={
                        "title": f"IndexNow Key: {key[:8]}...",
                        "slug": key,
                        "content": f"<!-- wp:html --><pre>{key_content}</pre><!-- /wp:html -->",
                        "status": "publish",
                    }
                )
                if r.status_code in (200, 201):
                    self.log(f"✓ IndexNow key page created: {wp_url}/{key}", "success")
                    return True
                elif r.status_code == 400 and "already exists" in (r.json().get("message", "") if r.ok else ""):
                    self.log(f"IndexNow key page already exists: {wp_url}/{key}", "info")
                    return True
                else:
                    self.log(f"IndexNow key page creation: HTTP {r.status_code}", "warning")
            except Exception as e:
                self.log(f"IndexNow key WP publish error: {e}", "warning")

        else:
            man_url = f"{domain}/{key}.txt"
            self.log(
                f"IndexNow key file must be published MANUALLY at: {man_url} (content: '{key}'). "
                f"No publisher credentials configured to do this automatically.",
                "warning"
            )

        return False

    def _ping_sitemap(self, sitemap_url: str) -> None:
        """Submit sitemap to Google Search Console via global service account."""
        from denzo.agents.utils.google_verification import _get_credentials, is_domain_verified

        domain = (self.ctx.pages_domain or self.ctx.domain or "").replace("https://", "").replace("http://", "").split("/")[0]
        if not domain or not is_domain_verified(domain):
            self.log("Sitemap discoverable via robots.txt + IndexNow.", "info")
            return

        creds = _get_credentials(scopes=["https://www.googleapis.com/auth/webmasters"])
        if not creds:
            return

        try:
            import urllib.parse
            credentials = creds

            site_url = (self.ctx.pages_domain or self.ctx.domain or "").rstrip("/")
            encoded_site = urllib.parse.quote(site_url, safe="")
            encoded_sitemap = urllib.parse.quote(sitemap_url, safe="")

            resp = requests.put(
                f"https://www.googleapis.com/webmasters/v3/sites/{encoded_site}/sitemaps/{encoded_sitemap}",
                headers={"Authorization": f"Bearer {credentials.token}"},
                timeout=15,
            )
            if resp.status_code in (200, 204):
                self.log(f"✓ Sitemap submitted to Google: {sitemap_url}", "success")
            else:
                self.log(f"GSC sitemap: HTTP {resp.status_code}", "info")
        except ImportError:
            pass  # google-auth not installed — IndexNow still works
        except Exception as e:
            self.log(f"GSC sitemap: {str(e)[:80]}", "info")

    def run(self):
        self.log("Indexation Accelerator starting — submitting URLs to search engines...")
        self.set_status("working", "Finding newly published pages")

        domain = self.ctx.pages_domain or self.ctx.domain or ""
        if not domain:
            self.log("No domain configured. Add pages_domain or domain in Settings.", "warning")
            self.set_status("idle", "No domain — skip")
            return

        # Load IndexNow key
        indexnow_key = self._get_indexnow_key()

        # Find pages published but not yet submitted for indexation
        # We track this via notes tag [INDEXED]
        pages = db_execute(
            """SELECT id, title, slug, publish_url FROM pages
               WHERE tenant_id=? AND status='published'
               AND publish_url IS NOT NULL
               AND (notes IS NULL OR notes NOT LIKE '%[INDEXED]%')
               ORDER BY published_at DESC LIMIT 200""",
            (self.ctx.tenant_id,)
        )

        if not pages:
            self.log("All published pages already submitted for indexation.", "info")
            self.set_status("done", "All pages up to date")
            return

        total = len(pages)
        self.log(f"Found {total} pages to submit for indexation (batches of {self.BATCH_SIZE})")

        # Build full URLs
        url_map = {}
        for p in pages:
            pub_url = p["publish_url"] or ""
            if pub_url.startswith("/"):
                pub_url = domain.rstrip("/") + pub_url
            elif not pub_url.startswith("http"):
                pub_url = domain.rstrip("/") + "/" + pub_url
            url_map[p["id"]] = pub_url

        all_urls = list(url_map.values())

        # ── Step 0: Publish IndexNow key file so verification works ──────
        key_published = self._publish_key_file(indexnow_key)

        # ── Step 1: IndexNow (fast, no auth) ──────────────────────────────
        self.set_status("working", "Submitting to IndexNow (Bing/Yandex)")
        inow_submitted = 0
        inow_batches = [all_urls[i:i + self.BATCH_SIZE] for i in range(0, len(all_urls), self.BATCH_SIZE)]

        for batch_idx, batch in enumerate(inow_batches):
            if self.should_stop():
                break
            n = self._indexnow_submit(batch, indexnow_key)
            inow_submitted += n
            self.log(f"IndexNow batch {batch_idx + 1}/{len(inow_batches)}: {n} URLs", "info")

            # Velocity control: random delay between batches
            if batch_idx < len(inow_batches) - 1:
                delay = random.randint(self.MIN_DELAY, self.MAX_DELAY)
                self.log(f"Waiting {delay}s before next batch (velocity control)...", "info")
                for _ in range(delay):
                    if self.should_stop():
                        break
                    time.sleep(1)

        self.log(f"IndexNow: {inow_submitted}/{total} URLs submitted", "success" if inow_submitted > 0 else "warning")

        # ── Step 2: Google Indexing API (if configured) ───────────────────
        if not self.should_stop():
            self.set_status("working", "Submitting to Google Indexing API")
            google_submitted = self._google_indexing_notify(all_urls[:100])  # cap at 100 (Google quotas)
            if google_submitted > 0:
                self.log(f"Google Indexing API: {google_submitted}/{min(total, 100)} URLs submitted", "success")

        # ── Step 3: Sitemap ping ─────────────────────────────────────────
        if not self.should_stop():
            self.set_status("working", "Pinging sitemaps")
            sitemap_url = f"{domain.rstrip('/')}/sitemap.xml"
            self._ping_sitemap(sitemap_url)
            # Also ping WordPress sitemap if present
            wp_sitemap = f"{domain.rstrip('/')}/wp-sitemap.xml"
            self._ping_sitemap(wp_sitemap)

        # ── Step 4: Mark pages as indexed ─────────────────────────────────
        for pid in url_map:
            db_write(
                "UPDATE pages SET notes=COALESCE(notes||' ','')||'[INDEXED]', "
                "updated_at=CURRENT_TIMESTAMP WHERE id=? AND tenant_id=?",
                (pid, self.ctx.tenant_id)
            )

        self.log(
            f"Indexation complete: {inow_submitted} IndexNow + {google_submitted if 'google_submitted' in dir() else 0} Google + sitemap pings. "
            f"Total pages: {total}",
            "success"
        )
        self.set_status(
            "done",
            f"{inow_submitted} submitted to search engines"
        )
