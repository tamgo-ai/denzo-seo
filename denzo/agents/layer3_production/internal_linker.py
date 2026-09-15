"""
Internal Linker — Layer 3
Builds an internal linking strategy and injects links into page content.
Hub-and-spoke model for local SEO authority.
"""
import json
import re
from denzo.agents.base_agent import TenantAwareBaseAgent, ClientContext, db_execute, db_write, strip_json_fences

PLAN_BATCH = 15        # initial batch size
PLAN_BATCH_RETRY = 5   # sub-batch size on retry after JSON parse failure


class InternalLinker(TenantAwareBaseAgent):

    PREREQUISITES = ["Programmatic SEO"]

    def __init__(self, ctx: ClientContext):
        super().__init__("Internal Linker", ctx, layer=4, color="green")

    def _plan_batch(self, batch: list, total: int) -> list:
        """Ask Claude to plan links for a batch of pages. Retries with smaller sub-batches on failure."""

        def _try(sub_batch: list) -> list | None:
                # Build dynamic example anchor text from actual business context
            industry = self.ctx.industry_vertical or "general"
            primary_svc = (self.ctx.services[0] if self.ctx.services else "service").lower()
            city1 = self.ctx.primary_city or "our city"
            city2 = (self.ctx.service_cities[0] if self.ctx.service_cities else city1)
            ex_anchor1 = f"{primary_svc} {city1}"
            ex_anchor2 = f"{primary_svc} near {city2}"

            prompt = f"""{self.ctx.to_prompt_block()}

I have {total} pages total. Build a hub-and-spoke internal linking strategy for this batch.

Pages in this batch:
{json.dumps(sub_batch, ensure_ascii=False)}

Design:
- Hub pages (most authoritative — service main pages, homepage)
- Spoke pages (location + service combos, blog posts)
- Each spoke links back to its hub
- Related spokes link to each other

Return a JSON array of linking instructions:
[
  {{
    "page_id": 1,
    "add_links_to": [
      {{"target_id": 2, "anchor_text": "{ex_anchor1}", "target_slug": "/services/main-service"}},
      {{"target_id": 3, "anchor_text": "{ex_anchor2}", "target_slug": "/locations/city"}}
    ]
  }}
]

Return ONLY valid JSON array. Max 3 links per page. Use natural anchor text relevant to {industry}.
"""
            for attempt in range(2):
                raw = self.call_claude(prompt, max_tokens=6000, model="claude-sonnet-4-6")
                if not raw:
                    self.log(f"Empty response on attempt {attempt + 1}", "warning")
                    continue
                try:
                    return json.loads(strip_json_fences(raw, "["))
                except Exception as e:
                    self.log(f"JSON parse error (attempt {attempt + 1}): {e} — raw[:200]: {raw[:200]}", "warning")
            return None

        result = _try(batch)
        if result is not None:
            return result

        # Full batch failed — retry in smaller sub-batches to avoid token/truncation issues
        if len(batch) > PLAN_BATCH_RETRY:
            self.log(
                f"Batch of {len(batch)} failed JSON parse — retrying as sub-batches of {PLAN_BATCH_RETRY}",
                "warning",
            )
            all_links: list = []
            for i in range(0, len(batch), PLAN_BATCH_RETRY):
                sub_result = _try(batch[i : i + PLAN_BATCH_RETRY])
                if sub_result:
                    all_links.extend(sub_result)
            return all_links

        return []

    def run(self):
        from bs4 import BeautifulSoup, NavigableString
        from denzo.urls import public_page_url
        from denzo.runtime_limits import setting
        self.set_status('working','Planning links to canonical page URLs')
        pages = [dict(r) for r in db_execute("SELECT id,title,slug,type,target_keyword,status,managed,publish_url,source_url FROM pages WHERE tenant_id=? AND status IN ('ready','published','live_external') ORDER BY id LIMIT 10000", (self.tenant_id,))]
        by_id = {p['id']:p for p in pages}
        eligible = [dict(r) for r in db_execute("SELECT * FROM pages WHERE tenant_id=? AND managed=1 AND status IN ('ready','published') AND content IS NOT NULL AND content!='' ORDER BY updated_at,id LIMIT ?", (self.tenant_id,setting('DENZO_PAGE_BATCH_SIZE',10,1,50)))]
        by_id.update({p['id']:p for p in eligible})
        plan = []
        for i in range(0,len(eligible),15):
            if self.should_stop():
                break
            plan.extend(self._plan_batch(eligible[i:i+15],len(pages)))
        count = 0
        for instruction in plan:
            page = by_id.get(instruction.get('page_id'))
            if not page or page not in eligible or self.should_stop():
                continue
            soup = BeautifulSoup(page['content'],'html.parser')
            changed = False
            for link in instruction.get('add_links_to',[]):
                target = by_id.get(link.get('target_id'))
                anchor = (link.get('anchor_text') or '').strip()
                if not target or target['id']==page['id'] or not anchor:
                    continue
                url = public_page_url(target,self.ctx)
                if soup.find('a',href=url):
                    continue
                for node in list(soup.find_all(string=True)):
                    if any(p.name in ('a','script','style','code','pre','h1','h2','h3') for p in node.parents):
                        continue
                    match = re.search(re.escape(anchor),str(node),re.I)
                    if not match:
                        continue
                    a = soup.new_tag('a',href=url);a.string=str(node)[match.start():match.end()]
                    node.replace_with(NavigableString(str(node)[:match.start()]),a,NavigableString(str(node)[match.end():]))
                    changed=True;count+=1;break
            if changed:
                # A concurrent revision is never overwritten.
                db_write('UPDATE pages SET content=?,updated_at=CURRENT_TIMESTAMP WHERE tenant_id=? AND id=? AND content=?',
                         (str(soup),self.tenant_id,page['id'],page['content']))
        self.set_status('done',f'{count} canonical internal links added')
