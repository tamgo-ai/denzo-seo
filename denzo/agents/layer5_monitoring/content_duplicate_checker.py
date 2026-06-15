"""
Content Duplicate Checker — Layer 6 (Analytics)
Detects near-duplicate content between pages using text similarity.
Flags pairs >85% similar as cannibalization risks.

Uses difflib.SequenceMatcher (stdlib, no dependencies).
Compares pages within the same type to avoid false positives across categories.
"""
import re
import json
from difflib import SequenceMatcher
from denzo.agents.base_agent import TenantAwareBaseAgent, ClientContext, db_execute, db_write


SIMILARITY_THRESHOLD = 0.85   # Flag pairs above this ratio
MAX_PAGES = 200               # Hard cap to avoid O(n²) explosion
MIN_TEXT_LENGTH = 200         # Skip pages with very little text


def _strip_html(text: str) -> str:
    """Remove HTML tags and normalize whitespace for comparison."""
    cleaned = re.sub(r'<[^>]+>', ' ', text or "")
    cleaned = re.sub(r'\s+', ' ', cleaned).strip().lower()
    return cleaned


def _similarity(a: str, b: str) -> float:
    """Return 0.0-1.0 similarity ratio between two text strings."""
    return SequenceMatcher(None, a, b).ratio()


class ContentDuplicateChecker(TenantAwareBaseAgent):

    def __init__(self, ctx: ClientContext):
        super().__init__("Content Duplicate Checker", ctx, layer=6, color="rose")

    def run(self):
        self.log("Scanning for near-duplicate content...")
        self.set_status("working", "Loading pages for comparison")

        pages = db_execute(
            "SELECT id, title, slug, type, content FROM pages "
            "WHERE tenant_id=? AND content IS NOT NULL AND content != '' "
            "AND status IN ('ready','published') "
            "ORDER BY type, id LIMIT ?",
            (self.ctx.tenant_id, MAX_PAGES)
        )

        if not pages or len(pages) < 2:
            self.log("Not enough pages with content to compare (< 2).", "warning")
            self.set_status("idle", "Not enough pages")
            return

        self.log(f"Loaded {len(pages)} pages. Extracting plain text...")

        # Extract plain text once
        page_texts = []
        for p in pages:
            text = _strip_html(p["content"] or "")
            if len(text) >= MIN_TEXT_LENGTH:
                page_texts.append({
                    "id": p["id"],
                    "title": p["title"],
                    "slug": p["slug"],
                    "type": p["type"] or "page",
                    "text": text,
                })

        self.log(f"{len(page_texts)} pages with sufficient text (≥{MIN_TEXT_LENGTH} chars)")

        # Compare within same type only — avoids false positives across categories
        by_type: dict[str, list] = {}
        for pt in page_texts:
            by_type.setdefault(pt["type"], []).append(pt)

        duplicates_found = 0
        total_pairs_checked = 0

        for ptype, group in by_type.items():
            n = len(group)
            if n < 2:
                continue

            self.set_status("working", f"Comparing {n} {ptype} pages...")
            self.log(f"Comparing {n} {ptype} pages ({n*(n-1)//2} pairs)...")

            for i in range(n):
                if self.should_stop():
                    break
                for j in range(i + 1, n):
                    total_pairs_checked += 1
                    a, b = group[i], group[j]

                    # Quick pre-filter: if titles are very different, skip expensive comparison
                    if a["slug"] == b["slug"]:
                        continue

                    ratio = _similarity(a["text"], b["text"])
                    if ratio >= SIMILARITY_THRESHOLD:
                        duplicates_found += 1
                        # Save to cannibalization_risks table
                        try:
                            db_write(
                                """INSERT OR IGNORE INTO cannibalization_risks
                                   (tenant_id, page_slug_a, page_title_a, page_slug_b, page_title_b,
                                    shared_keyword, risk_level, suggestion)
                                   VALUES (?,?,?,?,?,?,?,?)""",
                                (
                                    self.ctx.tenant_id,
                                    a["slug"], a["title"],
                                    b["slug"], b["title"],
                                    f"duplicate-{ptype}",
                                    "high" if ratio >= 0.92 else "medium",
                                    f"Pages are {ratio*100:.0f}% similar. "
                                    f"Consider consolidating into one authoritative page or "
                                    f"adding unique differentiators to each."
                                )
                            )
                        except Exception:
                            pass  # duplicate row — already flagged

                        self.log(
                            f"DUPLICATE ({ratio*100:.0f}%): \"{a['title']}\" ↔ \"{b['title']}\"",
                            "warning" if ratio >= 0.92 else "info"
                        )

            if self.should_stop():
                break

        self.log(
            f"Complete. {total_pairs_checked} pairs checked · "
            f"{duplicates_found} near-duplicate pairs found (≥{SIMILARITY_THRESHOLD*100:.0f}%).",
            "success" if duplicates_found == 0 else "warning"
        )
        self.set_status("done", f"{duplicates_found} duplicates found in {total_pairs_checked} pairs")
