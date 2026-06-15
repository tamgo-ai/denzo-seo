"""
SiteInventoryAgent — Discovery layer. Crawls the client's existing site
and builds a complete inventory of URLs, titles, keywords, and content hashes.

Before this agent runs, DENZO knows nothing about the client's existing site.
After it runs, every URL is in pages with origin='existing', managed=0.

Part of Capa 0.5 (Discovery & Reconciliation). Prerequisite for all generation.
"""

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

from denzo.agents.base_agent import TenantAwareBaseAgent


class SiteInventoryAgent(TenantAwareBaseAgent):
    """Crawl the client's site to build an inventory of existing content."""

    PREREQUISITES = []
    MIN_KEYWORDS = 0  # This agent creates its own data

    MAX_PAGES = 100       # Safety cap — don't crawl giant sites
    MAX_DEPTH = 3         # BFS depth limit
    REQUEST_DELAY = 1.0   # Be nice to the server

    def __init__(self, ctx):
        super().__init__(name="Site Inventory", ctx=ctx, layer=1, color="stone")

    def run(self):
        try:
            self._run_impl()
        except Exception as e:
            import traceback
            self.log(f"CRASH: {e}", "error")
            self.log(traceback.format_exc()[-300:], "error")
            self.set_status("error", f"SiteInventory crashed: {str(e)[:150]}")

    def _run_impl(self):
        self.log("SiteInventoryAgent: crawling existing site...")
        self.set_status("working", "Discovering existing site structure")

        domain = self.ctx.domain or self.ctx.website_url
        if not domain:
            self.set_status("done", "No domain configured — nothing to inventory")
            return

        base_url = domain if domain.startswith("http") else f"https://{domain}"
        base_url = base_url.rstrip("/")

        # ── Phase 1: Try sitemap.xml ─────────────────────────────────────────
        urls = self._crawl_sitemap(base_url)
        if urls:
            self.log(f"Found {len(urls)} URLs via sitemap")
        else:
            self.log("No sitemap found, falling back to BFS crawl from homepage")
            urls = self._crawl_bfs(base_url)

        if not urls:
            self.set_status("done", "Could not discover any pages — site may be blocking crawlers")
            return

        # ── Phase 2: Fetch and inventory each URL ───────────────────────────
        existing_slugs = set()
        new_pages = 0
        errors = 0

        for url in urls[:self.MAX_PAGES]:
            if self.should_stop():
                break

            try:
                result = self._fetch_page_data(url)
                if not result:
                    errors += 1
                    continue

                slug = result["slug"]
                if slug in existing_slugs:
                    continue
                existing_slugs.add(slug)

                # Insert into pages as existing, unmanaged content
                self._insert_existing_page(result)
                new_pages += 1

                time.sleep(self.REQUEST_DELAY)

            except Exception as e:
                errors += 1
                self.log(f"Error inventorying {url}: {e}", "warning")

        # Save inventory snapshot to settings for downstream agents
        self.save_output("site_inventory", {
            "total_urls_found": len(urls),
            "pages_inventoried": new_pages,
            "errors": errors,
            "base_url": base_url,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })

        self.set_status("done",
            f"Inventoried {new_pages} existing pages from {domain} ({len(urls)} URLs found, {errors} errors)")

    # ── Internal helpers ────────────────────────────────────────────────────

    def _crawl_sitemap(self, base_url: str) -> list[str]:
        """Try sitemap.xml and sitemap_index.xml. Returns list of URLs or empty list."""
        from denzo.agents.utils.stealth_fetch import fetch_html

        sitemap_urls = [
            f"{base_url}/sitemap.xml",
            f"{base_url}/sitemap_index.xml",
            f"{base_url}/wp-sitemap.xml",  # WordPress
        ]

        for sitemap_url in sitemap_urls:
            try:
                result = fetch_html(sitemap_url, timeout=15)
                if not result["ok"]:
                    continue

                html = result["html"]
                # Try XML parsing
                try:
                    root = ET.fromstring(html)
                    # Handle both sitemap and sitemap index
                    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
                    urls = []
                    for loc in root.findall(".//sm:url/sm:loc", ns):
                        urls.append(loc.text.strip())
                    if not urls:
                        for loc in root.findall(".//sm:sitemap/sm:loc", ns):
                            urls.append(loc.text.strip())
                    if urls:
                        return urls
                except ET.ParseError:
                    # Might be a text sitemap (one URL per line)
                    lines = html.strip().split("\n")
                    urls = [l.strip() for l in lines if l.strip().startswith("http")]
                    if urls:
                        return urls

            except Exception:
                continue

        return []

    def _crawl_bfs(self, base_url: str) -> list[str]:
        """BFS crawl from homepage. Returns up to MAX_PAGES URLs."""
        from denzo.agents.utils.stealth_fetch import fetch_html, parse_html

        visited = set()
        queue = [base_url]
        discovered = [base_url]
        depth = 0
        parsed_base = urlparse(base_url)
        base_domain = parsed_base.netloc

        while queue and len(discovered) < self.MAX_PAGES and depth < self.MAX_DEPTH:
            depth += 1
            next_queue = []

            for url in queue[:10]:  # max 10 per depth level
                if url in visited:
                    continue
                visited.add(url)

                try:
                    result = fetch_html(url, timeout=15)
                    if not result["ok"]:
                        continue

                    parsed = parse_html(result["html"])
                    for link in parsed.get("links", []):
                        href = link.get("href", "")
                        if not href:
                            continue
                        # Resolve relative URLs
                        full_url = urljoin(url, href)
                        full_url = full_url.split("#")[0].split("?")[0]  # strip fragment + query

                        parsed_full = urlparse(full_url)
                        if (parsed_full.netloc == base_domain
                                and full_url not in visited
                                and full_url not in discovered
                                and not full_url.endswith((".jpg", ".png", ".pdf", ".css", ".js", ".ico", ".svg"))
                                and len(discovered) < self.MAX_PAGES):
                            discovered.append(full_url)
                            next_queue.append(full_url)

                except Exception:
                    continue

                time.sleep(self.REQUEST_DELAY)
            queue = next_queue

        return discovered

    def _fetch_page_data(self, url: str) -> dict | None:
        """Fetch and parse a single page. Returns dict or None on failure."""
        from denzo.agents.utils.stealth_fetch import fetch_html, parse_html

        result = fetch_html(url, timeout=15)
        if not result["ok"] or not result.get("html"):
            return None

        parsed = parse_html(result["html"])
        title = parsed.get("title", "")
        h1 = parsed.get("h1", "")
        meta_desc = parsed.get("meta_desc", "")

        # Infer target keyword from title or H1
        target_keyword = title or h1
        if target_keyword:
            # Clean up common suffixes
            target_keyword = re.sub(r'\s*[|\-–—]\s*.+$', '', target_keyword).strip()
            target_keyword = re.sub(r'\s*—\s*.+$', '', target_keyword).strip()

        # Extract slug from URL
        parsed_url = urlparse(url)
        slug = parsed_url.path.rstrip("/").split("/")[-1] or "home"
        # Remove extension
        slug = re.sub(r'\.[^.]+$', '', slug)

        # Compute content hash
        all_text = parsed.get("all_text", "")
        content_hash = hashlib.sha256(
            re.sub(r'\s+', ' ', all_text.strip()).encode('utf-8')
        ).hexdigest()

        return {
            "url": url,
            "slug": slug,
            "title": title,
            "h1": h1,
            "meta_description": meta_desc,
            "target_keyword": target_keyword,
            "word_count": parsed.get("word_count", 0),
            "content_hash": content_hash,
            "source_url": url,
        }

    def _insert_existing_page(self, data: dict):
        """Insert an existing page with origin='existing', managed=0."""
        from denzo.agents.base_agent import db_execute, db_write

        # Check if this slug already exists
        existing = db_execute(
            "SELECT id FROM pages WHERE tenant_id=? AND slug=?",
            (self.tenant_id, data["slug"])
        )
        if existing:
            # Update the existing row with discovered metadata
            db_write(
                """UPDATE pages SET
                   source_url=?, content_hash=?, origin='existing', managed=0,
                   status='live_external', word_count=?
                   WHERE id=? AND tenant_id=?""",
                (data["source_url"], data["content_hash"],
                 data.get("word_count", 0),
                 existing[0]["id"], self.tenant_id)
            )
            return

        db_write(
            """INSERT INTO pages
               (tenant_id, title, slug, type, target_keyword,
                meta_description, source_url, content_hash,
                origin, managed, status, created_at, updated_at)
               VALUES (?, ?, ?, 'page', ?, ?, ?, ?, 'existing', 0, 'live_external',
                       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
            (self.tenant_id, data["title"], data["slug"],
             data["target_keyword"], data["meta_description"],
             data["source_url"], data["content_hash"])
        )
