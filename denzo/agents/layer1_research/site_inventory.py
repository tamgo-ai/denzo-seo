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
    REQUEST_DELAY = 0.3   # Be nice to the server (reduced — SPA sites return instantly)
    FETCH_TIMEOUT = 6     # Quick timeout — modern sites load fast or are SPAs

    def __init__(self, ctx):
        super().__init__(name="Site Inventory", ctx=ctx, layer=1, color="stone")

    def run(self):
        from denzo.urls import site_base_url
        self.set_status('working','Discovering site URLs')
        base = site_base_url(self.ctx)
        urls = self._crawl_sitemap(base)
        method = 'sitemap' if urls else 'bounded_bfs'
        urls = urls or self._crawl_bfs(base)
        if base not in urls:
            urls.insert(0,base)
        previous = self.load_output('site_inventory') or {}
        offset = int(previous.get('next_offset',0)) if previous.get('base_url') == base else 0
        if offset >= len(urls):
            offset = 0
        budget = 500
        batch = urls[offset:offset+budget]
        completed, errors = 0, []
        for url in batch:
            if self.should_stop():
                break
            try:
                data = self._fetch_page_data(url)
                if not data:
                    raise ValueError('Page unavailable')
                self._insert_existing_page(data)
                completed += 1
            except Exception as exc:
                errors.append({'url':url,'error':str(exc)[:160]})
            offset += 1
        self.save_output('site_inventory', {
            'total_urls_found':len(urls),'pages_inventoried':completed,'errors':len(errors),
            'failed_urls':errors,'base_url':base,'next_offset':offset if offset<len(urls) else 0,
            'coverage':'partial' if method=='bounded_bfs' or errors or offset<len(urls) or len(urls)>=10000 else 'complete_for_discovered_urls',
            'discovery_method':method,
            'page_budget':budget,'completed_at':datetime.now(timezone.utc).isoformat(),
        })
        self.set_status('done',f'{completed} pages read; {max(0,len(urls)-offset)} remaining; {len(errors)} unavailable')

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
        content_hashes = set()  # Early dedup: skip same-content pages instantly
        new_pages = 0
        errors = 0

        for i, url in enumerate(urls[:self.MAX_PAGES]):
            if self.should_stop():
                break

            # Progress every 20 URLs
            if i > 0 and i % 20 == 0:
                self.log(f"Inventory progress: {i}/{len(urls[:self.MAX_PAGES])} URLs ({new_pages} new pages)")

            try:
                result = self._fetch_page_data(url)
                if not result:
                    errors += 1
                    continue

                # Early exit: skip duplicate content (common on SPAs like Next.js)
                ch = result.get("content_hash", "")
                if ch and ch in content_hashes:
                    continue
                if ch:
                    content_hashes.add(ch)

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

    def _crawl_sitemap(self, base_url):
        from collections import deque
        from urllib.parse import urljoin, urlsplit, urldefrag
        from denzo.auditor.safe_fetch import fetch_html
        allowed = urlsplit(base_url).hostname
        queue = deque((f'{base_url}/sitemap.xml',f'{base_url}/sitemap_index.xml',f'{base_url}/wp-sitemap.xml'))
        visited, found = set(), dict()
        while queue and len(visited)<1000 and len(found)<10000 and not self.should_stop():
            sitemap = queue.popleft()
            if sitemap in visited or urlsplit(sitemap).hostname != allowed:
                continue
            visited.add(sitemap)
            try:
                result = fetch_html(sitemap)
                if not result.get('ok'):
                    continue
                root = ET.fromstring(result['html'])
                kind = root.tag.rsplit('}',1)[-1]
                for child in root:
                    loc = next((x.text for x in child if x.tag.rsplit('}',1)[-1]=='loc'),None)
                    if not loc:
                        continue
                    url = urldefrag(urljoin(sitemap,loc.strip()))[0]
                    if urlsplit(url).hostname != allowed:
                        continue
                    if kind == 'sitemapindex':
                        queue.append(url)
                    elif kind == 'urlset':
                        found[url] = None
                    if len(found)>=10000:
                        break
            except (ValueError, OSError, ET.ParseError):
                continue
        return list(found)

    def _crawl_bfs(self, base_url):
        from collections import deque
        from urllib.parse import urljoin,urlsplit,urldefrag
        from denzo.auditor.safe_fetch import fetch_html
        from denzo.agents.utils.stealth_fetch import parse_html
        queue = deque([(base_url,0)])
        seen = set()
        host = urlsplit(base_url).hostname
        while queue and len(seen)<500 and not self.should_stop():
            url, depth = queue.popleft()
            if url in seen:
                continue
            seen.add(url)
            if depth>=8:
                continue
            try:
                result = fetch_html(url)
                if not result.get('ok'):
                    continue
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(result['html'],'html.parser')
                for a in soup.find_all('a',href=True):
                    target = urldefrag(urljoin(url,a['href']))[0]
                    parts = urlsplit(target)
                    if parts.scheme in ('http','https') and parts.hostname==host and not parts.query and target not in seen:
                        queue.append((target,depth+1))
            except (ValueError,OSError):
                continue
        return sorted(seen)

    def _fetch_page_data(self, url: str) -> dict | None:
        """Fetch and parse a single page. Returns dict or None on failure."""
        from denzo.auditor.safe_fetch import fetch_html
        from denzo.agents.utils.stealth_fetch import parse_html

        result = fetch_html(url, timeout=self.FETCH_TIMEOUT)
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
        slug = parsed_url.path.strip("/") or "home"
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
            "source_url": result.get("final_url", url),
            "content": result["html"],
        }

    def _insert_existing_page(self, data):
        from denzo.db import get_db
        db = get_db()
        try:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT * FROM pages WHERE tenant_id=? AND (source_url=? OR publish_url=?)',
                                  (self.tenant_id,data['source_url'],data['source_url'])).fetchone()
            if existing:
                if existing['managed']:
                    db.execute('UPDATE pages SET source_url=? WHERE id=?',(data['source_url'],existing['id']))
                else:
                    db.execute('UPDATE pages SET title=?,meta_description=?,content_hash=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                               (data['title'],data['meta_description'],data['content_hash'],existing['id']))
            else:
                slug = data['slug']
                if db.execute('SELECT 1 FROM pages WHERE tenant_id=? AND slug=?',(self.tenant_id,slug)).fetchone():
                    slug += '-external-' + hashlib.sha256(data['source_url'].encode()).hexdigest()[:10]
                db.execute("""INSERT INTO pages(tenant_id,title,slug,type,target_keyword,meta_description,
                          source_url,content_hash,content,origin,managed,status)
                          VALUES (?,?,?,'page',?,?,?,?,?,'existing',0,'live_external')""",
                           (self.tenant_id,data['title'],slug,data['target_keyword'],data['meta_description'],
                            data['source_url'],data['content_hash'],data.get('content','')))
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
