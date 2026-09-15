"""Submit public URLs for discovery. Acceptance does not establish indexation."""
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

        from denzo.urls import site_base_url
        from urllib.parse import urlsplit
        base = site_base_url(self.ctx)
        try:
            resp = requests.post(
                "https://api.indexnow.org/indexnow",
                json={
                    "host": urlsplit(base).hostname,
                    "key": key,
                    "keyLocation": f"{base}/{key}.txt",
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
        eligible = {r['publish_url'] for r in db_execute("SELECT publish_url,schema_markup FROM pages WHERE tenant_id=? AND status='published'", (self.tenant_id,)) if google_indexing_eligible(r['schema_markup'])}
        urls = [url for url in urls if url in eligible]
        if not urls:
            return 0

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

    def _publish_key_file(self, key):
        from denzo.urls import site_base_url
        from denzo.auditor.safe_fetch import fetch_html
        from denzo.agents.base_agent import build_llms_txt
        base = site_base_url(self.ctx)
        try:
            existing = fetch_html(f'{base}/{key}.txt')
            if existing.get('ok') and existing['html'].strip()==key:
                return True
            if self.ctx.publisher_type=='wordpress':
                response = requests.post(self.ctx.wp_url.rstrip('/')+'/wp-json/denzo-seo/v1/resources',
                    auth=(self.ctx.wp_user,self.ctx.wp_app_password),
                    json={'indexnow_key':key,'llms':build_llms_txt(self.ctx,base_url=base)},timeout=20)
                response.raise_for_status()
            elif self.ctx.github_repo and self.ctx.github_token:
                import base64
                from denzo.agents.layer4_publishing.github_publisher import GitHubPublisher
                publisher = GitHubPublisher(self.ctx)
                session = requests.Session()
                session.headers.update({'Authorization':f'Bearer {self.ctx.github_token}','Accept':'application/vnd.github+json'})
                prefix = (self.ctx.github_path_prefix or '').strip('/')
                prefix = (prefix+'/' if prefix else '') + ('public/' if self.ctx.github_format=='nextjs' else '')
                if not publisher._publish_file(session,self.ctx.github_repo,self.ctx.github_branch or 'main',prefix+key+'.txt',base64.b64encode(key.encode()).decode(),'SEO: IndexNow verification'):
                    return False
            else:
                return False
            live = fetch_html(f'{base}/{key}.txt')
            return bool(live.get('ok') and live['html'].strip()==key)
        except Exception as exc:
            self.log(f'IndexNow key not yet accessible: {type(exc).__name__}', 'warning')
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
        from denzo.urls import site_base_url
        self.set_status('working','Submitting verified public URLs for discovery')
        pages = [dict(r) for r in db_execute("""SELECT * FROM pages WHERE tenant_id=? AND status='published'
          AND deployment_status='verified' AND publish_url IS NOT NULL
          AND (notes IS NULL OR notes NOT LIKE '%[SUBMITTED]%') ORDER BY published_at LIMIT 200""", (self.tenant_id,))]
        if not pages:
            self.set_status('done','No new verified URLs to submit')
            return
        key = self._get_indexnow_key()
        key_ready = self._publish_key_file(key)
        acknowledged = set()
        if key_ready:
            for offset in range(0,len(pages),self.BATCH_SIZE):
                if self.should_stop():
                    break
                batch = pages[offset:offset+self.BATCH_SIZE]
                if self._indexnow_submit([p['publish_url'] for p in batch],key)==len(batch):
                    acknowledged.update(p['id'] for p in batch)
        for page in pages:
            if self.should_stop():
                break
            if google_indexing_eligible(page.get('schema_markup')) and self._google_indexing_notify([page['publish_url']])==1:
                acknowledged.add(page['id'])
        for page_id in acknowledged:
            db_write("UPDATE pages SET notes=COALESCE(notes,'')||' [SUBMITTED]' WHERE tenant_id=? AND id=?", (self.tenant_id,page_id))
        if not self.should_stop():
            suffix = '/wp-sitemap.xml' if self.ctx.publisher_type=='wordpress' else '/sitemap.xml'
            self._ping_sitemap(site_base_url(self.ctx)+suffix)
        self.set_status('done' if acknowledged else 'error', f'{len(acknowledged)}/{len(pages)} URLs accepted for discovery; indexation is not confirmed')


def google_indexing_eligible(schema):
    from denzo.urls import schema_json
    try:
        data = json.loads(schema_json(schema))
    except (ValueError,TypeError):
        return False
    def visit(node):
        if isinstance(node,list):
            return any(visit(x) for x in node)
        if not isinstance(node,dict):
            return False
        kind = node.get('@type')
        types = kind if isinstance(kind,list) else [kind]
        if 'JobPosting' in types and all(node.get(k) for k in ('title','datePosted','hiringOrganization')):
            return True
        if 'VideoObject' in types:
            events = node.get('publication',[])
            if isinstance(events,dict):
                events=[events]
            if any(isinstance(e,dict) and e.get('@type')=='BroadcastEvent' and e.get('isLiveBroadcast') is True for e in events):
                return True
        return any(visit(v) for v in node.values() if isinstance(v,(dict,list)))
    return visit(data)
