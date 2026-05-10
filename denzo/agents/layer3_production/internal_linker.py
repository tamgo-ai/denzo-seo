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
        self.log("Building internal link structure...")
        self.set_status("working", "Loading all pages")

        # Prereq check: need ready/published pages with content
        pages_check = db_execute(
            "SELECT COUNT(*) AS n FROM pages WHERE tenant_id=? AND content IS NOT NULL AND content != '' "
            "AND status IN ('ready', 'published')",
            (self.ctx.tenant_id,)
        )
        pages_count = pages_check[0]["n"] if pages_check else 0
        if pages_count == 0:
            self.log("No ready/published pages with content found. Run Programmatic SEO first.", "warning")
            self.set_status("idle", "No ready pages — run Programmatic SEO first")
            return

        pages = db_execute(
            "SELECT id, title, slug, type, target_keyword, content FROM pages "
            "WHERE tenant_id=? AND content IS NOT NULL AND content != '' "
            "AND status IN ('ready', 'published') ORDER BY id",
            (self.ctx.tenant_id,)
        )

        if not pages:
            self.log("No pages with content found.", "warning")
            self.set_status("idle", "No pages")
            return

        page_list = [{"id": r["id"], "title": r["title"], "slug": r["slug"], "type": r["type"], "keyword": r["target_keyword"]} for r in pages]
        total = len(page_list)
        self.log(f"Found {total} pages. Planning links in batches of {PLAN_BATCH}...")

        # Collect link plan across all batches
        link_plan = []
        batches = [page_list[i:i + PLAN_BATCH] for i in range(0, total, PLAN_BATCH)]
        for idx, batch in enumerate(batches):
            if self.should_stop():
                break
            self.set_status("working", f"AI planning links: batch {idx + 1}/{len(batches)}")
            instructions = self._plan_batch(batch, total)
            link_plan.extend(instructions)
            self.log(f"Batch {idx + 1}/{len(batches)}: {len(instructions)} instructions")

        if not link_plan:
            self.log("No link instructions from Claude (all batches returned empty/malformed JSON). Skipping internal linking.", "warning")
            self.set_status("done", "0 internal links — Claude returned no plan (pages may lack enough content for linking)")
            return

        # Re-fetch fresh content from DB before injecting links.
        # Content Optimizer may have rewritten pages while we were planning — using
        # the stale copy from the initial SELECT would silently overwrite those rewrites.
        fresh_rows = db_execute(
            "SELECT id, content, slug FROM pages "
            "WHERE tenant_id=? AND content IS NOT NULL AND content != '' "
            "AND status IN ('ready', 'published') ORDER BY id",
            (self.ctx.tenant_id,)
        )
        page_map = {r["id"]: {"content": r["content"], "slug": r["slug"]} for r in (fresh_rows or [])}

        injected = 0
        self.set_status("working", "Injecting links into pages")

        # Build a slug → canonical URL map to fix any /wp/ prefix issues
        slug_map = {r["id"]: r["slug"].lstrip("/") for r in pages}

        for instruction in link_plan:
            if self.should_stop():
                break
            pid = instruction.get("page_id")
            if pid not in page_map:
                continue

            content = page_map[pid]["content"] or ""
            links_to_add = instruction.get("add_links_to", [])

            for link in links_to_add:
                anchor = link.get("anchor_text", "")
                target_id = link.get("target_id")
                slug = link.get("target_slug", "")

                # Prefer canonical slug from DB over AI-generated slug
                if target_id and target_id in slug_map:
                    slug = "/" + slug_map[target_id]
                elif slug:
                    # Sanitize: strip any /wp/ prefix from AI-generated slugs
                    slug = re.sub(r'^/wp/', '/', slug)
                    if not slug.startswith("/"):
                        slug = "/" + slug

                if not anchor or not slug:
                    continue
                # Only inject if anchor text appears in content and isn't already a link
                if anchor.lower() in content.lower() and f'href="{slug}"' not in content:
                    pattern = re.compile(re.escape(anchor), re.IGNORECASE)
                    replacement = f'<a href="{slug}">{anchor}</a>'
                    content, n = pattern.subn(replacement, content, count=1)
                    if n:
                        injected += 1

            page_map[pid]["content"] = content

        # Save updated content
        saved = 0
        for pid, data in page_map.items():
            if data.get("content"):
                db_write(
                    "UPDATE pages SET content=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND tenant_id=?",
                    (data["content"], pid, self.ctx.tenant_id)
                )
                saved += 1

        self.log(f"Internal linking complete: {injected} links injected across {saved} pages.", "success")
        self.set_status("done", f"{injected} internal links added")
