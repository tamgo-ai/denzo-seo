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

    def _find_wp_page_by_slug(self, api_base: str, auth: tuple, slug: str, resource: str = "pages"):
        """
        Return the WP page dict if a page with this slug exists.
        Returns None if not found (404/empty list).
        Returns _LOOKUP_ERROR if the request fails — callers must NOT create on error
        to avoid duplicating pages on transient network failures.
        """
        try:
            r = requests.get(
                f"{api_base}/{resource}",
                auth=auth, timeout=15,
                params={"slug": slug, "per_page": 1, "status": "any", "context": "edit"}
            )
            r.raise_for_status()
            data = r.json()
            if isinstance(data, list) and data:
                return data[0]
            return None
        except Exception:
            return self._LOOKUP_ERROR

    def run(self):
        from denzo.urls import site_base_url,public_page_url,quality_document,schema_json
        from denzo.editorial import publishable,revision_hash
        from denzo.publication import reserve_publication,committed,failed,verify_publication,reconcile_publications
        ctx=self.ctx
        if not all((ctx.wp_url,ctx.wp_user,ctx.wp_app_password)):
            self.set_status('skipped','WordPress credentials are not configured')
            return
        self.set_status('working','Checking WordPress connector capabilities')
        base=site_base_url(ctx)
        auth=(ctx.wp_user,ctx.wp_app_password)
        api_base=ctx.wp_url.rstrip('/')+'/wp-json/wp/v2'
        try:
            response=requests.get(ctx.wp_url.rstrip('/')+'/wp-json/denzo-seo/v1/capabilities',auth=auth,timeout=15)
            response.raise_for_status()
            capabilities=response.json()
            if not capabilities.get('seo_metadata'):
                raise ValueError('Install/activate the DENZO SEO Connector plugin')
        except Exception as exc:
            self.set_status('error',f'WordPress connector unavailable: {exc}')
            return
        reconcile_publications(ctx.tenant_id)
        self._load_velocity_settings()
        pages=[dict(r) for r in db_execute("SELECT * FROM pages WHERE tenant_id=? AND status='ready'",(ctx.tenant_id,)) if publishable(r)]
        processed=errors=0
        for page in pages:
            if self.should_stop():
                break
            resource='posts' if page.get('type') in ('blog','article','post') else 'pages'
            if not capabilities.get('publish_'+resource):
                self.log(f'Account cannot publish {resource}','error');errors+=1;continue
            slug=page['slug'].strip('/')
            if '/' in slug:
                self.log('WordPress generated slugs must be a single segment','error');errors+=1;continue
            existing=self._find_wp_page_by_slug(api_base,auth,slug,resource)
            if existing is self._LOOKUP_ERROR:
                self.log(f'Lookup failed for {slug}; no write attempted','error');errors+=1;continue
            path=f'{resource}/{slug}'
            ownership=db_execute("SELECT managed FROM managed_paths WHERE tenant_id=? AND publisher='wordpress' AND path IN (?,?)",(ctx.tenant_id,path,slug))
            remote_owner=(existing or {}).get('meta',{}).get('denzo_tenant')
            if (ownership and not ownership[0]['managed']) or (existing and not ownership and remote_owner!=ctx.tenant_id):
                self.log(f'Protected pre-existing WordPress content: {slug}','warning');continue
            if existing:
                page['publish_url']=existing.get('link') or public_page_url(page,ctx)
            from denzo.html_content import sanitize_fragment
            content=sanitize_fragment(page['content'])
            try:
                schema=schema_json(page.get('schema_markup'))
                url=public_page_url(page,ctx)
                issues=validate_page_quality(quality_document(content,page,ctx),page.get('type','page'),base_url=url)
                if issues:
                    self.log(f'Quality checks failed: {issues[:3]}','warning');errors+=1;continue
                prepared_revision=revision_hash(page)
                attempt,page=reserve_publication(ctx.tenant_id,page['id'],'wordpress',self.MAX_PAGES_PER_DAY,url)
            except ValueError as exc:
                self.log(str(exc),'warning');continue
            if revision_hash(page)!=prepared_revision:
                failed(attempt,'Content changed while preparing publication');continue
            payload={'title':page['title'],'slug':slug,'status':'publish',
                     'content':'<!-- wp:html --><div class="denzo-content">'+content+'</div><!-- /wp:html -->',
                     'meta':{'denzo_title':page.get('meta_title') or page['title'],
                             'denzo_description':page.get('meta_description') or '',
                             'denzo_schema':schema,'denzo_revision':revision_hash(page),'denzo_tenant':ctx.tenant_id}}
            remote_url=None
            try:
                if self.should_stop():
                    failed(attempt,'Cancelled before provider write');break
                endpoint=f'{api_base}/{resource}'+(f'/{existing["id"]}' if existing else '')
                response=requests.post(endpoint,auth=auth,json=payload,timeout=30)
                if response.status_code not in (200,201):
                    failed(attempt,f'WordPress HTTP {response.status_code}');errors+=1;continue
                result=response.json()
                remote_url=result['link']
                committed(attempt,remote_url,str(result['id']))
                db_write("INSERT OR REPLACE INTO managed_paths(tenant_id,publisher,path,page_id,managed,content_hash) VALUES (?,'wordpress',?,?,1,?)",
                         (ctx.tenant_id,path,page['id'],revision_hash(page)))
                verify_publication(attempt)
                processed+=1
            except Exception as exc:
                # An ambiguous provider timeout must be verified, not retried as a new create.
                if remote_url is None:
                    committed(attempt,url)
                self.log(f'Publication needs verification: {type(exc).__name__}','warning');errors+=1
        self.set_status('error' if errors else 'done',f'{processed} sent; {errors} issues; live status requires revision verification')

    def _build_llms_content(self, ctx) -> str:
        """Build llms.txt markdown content from ClientContext. Delegates to shared utility."""
        domain = getattr(ctx, "domain", "") or ""
        return build_llms_txt(ctx, base_url=domain)
