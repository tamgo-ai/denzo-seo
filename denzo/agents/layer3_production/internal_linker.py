"""
Internal Linker — Layer 3
Builds an internal linking strategy and injects links into page content.
Hub-and-spoke model for local SEO authority.
"""
import json
import re
from denzo.agents.base_agent import TenantAwareBaseAgent, ClientContext, db_execute, db_write, strip_json_fences

PLAN_BATCH = 15   # pages per Claude call — keep batches small to avoid truncated JSON


class InternalLinker(TenantAwareBaseAgent):

    def __init__(self, ctx: ClientContext):
        super().__init__("Internal Linker", ctx, layer=4, color="green")

    def _plan_batch(self, batch: list, total: int) -> list:
        """Ask Claude to plan links for one batch of pages. Returns list of instructions."""
        prompt = f"""{self.ctx.to_prompt_block()}

I have {total} pages total. Build a hub-and-spoke internal linking strategy for this batch.

Pages in this batch:
{json.dumps(batch, ensure_ascii=False)}

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
      {{"target_id": 2, "anchor_text": "auto body repair North Hollywood", "target_slug": "/services/auto-body"}},
      {{"target_id": 3, "anchor_text": "collision repair Burbank", "target_slug": "/locations/burbank"}}
    ]
  }}
]

Return ONLY valid JSON array. Max 3 links per page. Use natural anchor text.
"""
        for attempt in range(2):
            raw = self.call_claude(prompt, max_tokens=6000, model="claude-sonnet-4-6")
            if not raw:
                self.log(f"Empty response from Claude on attempt {attempt+1}", "warning")
                continue
            try:
                return json.loads(strip_json_fences(raw, "["))
            except Exception as e:
                self.log(f"JSON parse error (attempt {attempt+1}): {e} — raw[:200]: {raw[:200]}", "warning")
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

        # Build a page_id → content map
        page_map = {r["id"]: {"content": r["content"], "slug": r["slug"]} for r in pages}

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
