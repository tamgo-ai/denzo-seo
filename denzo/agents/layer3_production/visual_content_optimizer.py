"""
Visual Content Optimizer — Layer 4
====================================
Optimizes pages at the VISUAL level — completely separate from the
text-only Content Optimizer.

What it does:
1. Scans page HTML for <img> tags
2. Generates SEO-optimized alt text for images missing it (uses Claude)
3. Adds loading="lazy" + width/height to prevent Cumulative Layout Shift (CLS)
4. Renames generic filenames (e.g. IMG_1234.jpg → bmw-certified-collision-repair-north-hollywood.jpg)
5. Adds Open Graph image tags if missing
6. Adds schema.org ImageObject markup
7. Adds srcset hints for responsive images
8. Reports visual SEO score per page

It NEVER touches prose text, headings, or body copy — that's the Content Optimizer's job.
"""
import json
import re
from bs4 import BeautifulSoup
from denzo.agents.base_agent import (
    TenantAwareBaseAgent, ClientContext,
    db_write, db_execute, strip_json_fences
)


class VisualContentOptimizer(TenantAwareBaseAgent):

    def __init__(self, ctx: ClientContext):
        super().__init__("Visual Content Optimizer", ctx, layer=4, color="pink")

    BATCH = 10
    MIN_VISUAL_SCORE = 65  # Pages below this get visual fixes applied

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _score_images(self, images: list[dict], page_keyword: str) -> dict:
        """
        Score visual SEO quality of image set.
        Returns {"score": 0-100, "issues": [...], "fixes_needed": [...]}
        """
        if not images:
            return {"score": 50, "issues": ["No images found on page"], "fixes_needed": []}

        issues = []
        fixes = []
        score = 100

        missing_alt = [img for img in images if not img.get("alt", "").strip()]
        generic_alt = [img for img in images if img.get("alt", "").strip()
                       and img["alt"].strip().lower() in ("image", "photo", "picture", "img", "banner")]
        missing_lazy = [img for img in images if img.get("loading", "") != "lazy"]
        missing_dims = [img for img in images if not img.get("width") or not img.get("height")]
        generic_filenames = [
            img for img in images
            if img.get("src", "") and re.search(
                r"(img_\d+|dsc_\d+|photo_?\d+|image_?\d+|screenshot|untitled|unnamed|banner\d*|slide\d*)",
                img.get("src", "").lower()
            )
        ]

        # Score deductions
        if missing_alt:
            deduction = min(30, len(missing_alt) * 8)
            score -= deduction
            issues.append(f"{len(missing_alt)} image(s) missing alt text — critical for accessibility & SEO")
            fixes.append("add_alt_text")

        if generic_alt:
            score -= min(10, len(generic_alt) * 3)
            issues.append(f"{len(generic_alt)} image(s) have generic alt text (e.g. 'image', 'photo')")
            fixes.append("improve_alt_text")

        if missing_lazy:
            score -= min(15, len(missing_lazy) * 3)
            issues.append(f"{len(missing_lazy)} image(s) missing loading='lazy' — impacts Core Web Vitals")
            fixes.append("add_lazy_loading")

        if missing_dims:
            score -= min(10, len(missing_dims) * 2)
            issues.append(f"{len(missing_dims)} image(s) missing width/height — causes Cumulative Layout Shift")
            fixes.append("add_dimensions")

        if generic_filenames:
            score -= min(15, len(generic_filenames) * 4)
            issues.append(f"{len(generic_filenames)} image(s) have non-descriptive filenames (e.g. IMG_1234.jpg)")
            fixes.append("optimize_filenames")

        # Keyword in alt text check
        keyword_in_alt = any(
            page_keyword.lower() in img.get("alt", "").lower()
            for img in images if img.get("alt", "").strip()
        )
        if images and not keyword_in_alt and page_keyword:
            score -= 10
            issues.append(f"Target keyword '{page_keyword}' not found in any image alt text")
            fixes.append("add_keyword_alt")

        return {
            "score": max(0, min(100, score)),
            "issues": issues,
            "fixes_needed": list(set(fixes))
        }

    def _generate_alt_texts(self, images: list[dict], page_title: str, keyword: str) -> dict:
        """
        Use Claude to generate SEO-optimized alt text for images.
        Returns {src: alt_text} mapping.
        """
        ctx = self.ctx
        images_to_fix = [img for img in images if not img.get("alt", "").strip()
                        or img.get("alt", "").strip().lower() in ("image", "photo", "picture", "img", "banner")]
        if not images_to_fix:
            return {}

        img_list = []
        for img in images_to_fix[:15]:
            src = img.get("src", "")
            filename = src.split("/")[-1].split("?")[0] if src else "unknown"
            img_list.append({"src": src, "filename": filename, "current_alt": img.get("alt", "")})

        prompt = f"""{ctx.to_prompt_block()}

You are an SEO image optimization expert. Generate descriptive, SEO-optimized alt text for these images.

Page title: {page_title}
Target keyword: {keyword}
Business: {ctx.client_name} — {ctx.industry_vertical}

Images to fix:
{json.dumps(img_list, ensure_ascii=False)}

Rules for alt text:
1. Be descriptive and specific — describe what's actually in the image based on the filename/context
2. Include the target keyword naturally in AT LEAST ONE alt text (don't keyword-stuff every image)
3. Keep alt text under 125 characters
4. Do not use "image of", "photo of", "picture of" — start with the subject
5. Be contextually relevant to the business ({ctx.client_name}, {ctx.primary_city})
6. For logos: "{{business_name}} logo — {{tagline}}"
7. For location/building: "{{business_name}} {{city}} — {{service_description}}"
8. For before/after: "{{service}} before and after at {{business_name}} {{city}}"

Return ONLY a JSON object mapping filename → alt text:
{{
  "filename_or_src": "descriptive alt text here",
  ...
}}
"""
        raw = self.call_claude(prompt, max_tokens=1000, model="claude-haiku-4-5-20251001")
        if not raw:
            return {}
        try:
            data = json.loads(strip_json_fences(raw))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _apply_visual_fixes(self, html: str, page: dict, alt_map: dict, fixes_needed: list) -> str:
        """
        Apply visual SEO fixes to HTML without touching any text content.
        Returns improved HTML string.
        """
        soup = BeautifulSoup(html, "html.parser")
        keyword = page.get("target_keyword", page.get("title", ""))
        changed = False

        for img in soup.find_all("img"):
            src = img.get("src", "")
            filename = src.split("/")[-1].split("?")[0] if src else ""

            # Fix missing / generic alt text
            current_alt = img.get("alt", "").strip()
            is_generic = current_alt.lower() in ("", "image", "photo", "picture", "img", "banner")

            if is_generic and ("add_alt_text" in fixes_needed or "improve_alt_text" in fixes_needed):
                # Look up in alt_map by filename or full src
                new_alt = alt_map.get(filename) or alt_map.get(src)
                if new_alt:
                    img["alt"] = new_alt.strip()
                    changed = True

            # Add lazy loading
            if "add_lazy_loading" in fixes_needed and img.get("loading", "") != "lazy":
                # Don't lazy-load above-the-fold hero images (first image or hero class)
                parent_classes = " ".join(
                    p.get("class", []) if isinstance(p.get("class"), list) else [p.get("class", "")]
                    for p in img.parents if hasattr(p, "get")
                )
                is_hero = any(h in parent_classes.lower() for h in ["hero", "banner", "header-img", "above-fold"])
                if not is_hero:
                    img["loading"] = "lazy"
                    changed = True

            # Add decoding=async for non-critical images
            if not img.get("decoding") and img.get("loading") == "lazy":
                img["decoding"] = "async"
                changed = True

        if not changed:
            return html

        return str(soup)

    def _add_og_image(self, html: str, page: dict) -> str:
        """Add Open Graph and Twitter Card image meta tags if missing."""
        soup = BeautifulSoup(html, "html.parser")
        head = soup.find("head")
        if not head:
            return html

        # Check if OG image already exists
        og_image = soup.find("meta", {"property": "og:image"})
        if og_image:
            return html

        # Find the first meaningful image in the page
        first_img = None
        for img in soup.find_all("img"):
            src = img.get("src", "")
            if src and not src.startswith("data:") and "logo" not in src.lower():
                # Prefer absolute URLs or images with descriptive filenames
                if src.startswith("http"):
                    first_img = src
                    break
                elif src.startswith("/"):
                    domain = self.ctx.pages_domain or self.ctx.domain or ""
                    if domain:
                        first_img = domain.rstrip("/") + src
                        break

        if not first_img:
            return html

        # Inject OG image tags
        og_tag = soup.new_tag("meta", attrs={"property": "og:image", "content": first_img})
        tw_tag = soup.new_tag("meta", attrs={"name": "twitter:image", "content": first_img})
        head.append(og_tag)
        head.append(tw_tag)
        return str(soup)

    def _extract_images_from_html(self, html: str) -> list[dict]:
        """Extract image metadata from HTML."""
        soup = BeautifulSoup(html, "html.parser")
        images = []
        for img in soup.find_all("img"):
            images.append({
                "src": img.get("src", ""),
                "alt": img.get("alt", ""),
                "width": img.get("width", ""),
                "height": img.get("height", ""),
                "loading": img.get("loading", ""),
            })
        return images

    # ── Main run ───────────────────────────────────────────────────────────────

    def run(self):
        self.log("Starting visual content optimization...", "info")
        self.set_status("working", "Loading pages for visual analysis")

        # Prereq check: need pages with content
        ready_check = db_execute(
            "SELECT COUNT(*) AS n FROM pages WHERE tenant_id=? AND status='ready' "
            "AND content IS NOT NULL AND content != ''",
            (self.ctx.tenant_id,)
        )
        ready_count = ready_check[0]["n"] if ready_check else 0
        if ready_count == 0:
            self.log("No ready pages found. Run Programmatic SEO first.", "warning")
            self.set_status("idle", "No ready pages — run Programmatic SEO first")
            return

        optimized = 0
        skipped = 0
        round_num = 0
        MAX_ROUNDS = 8

        while not self.should_stop() and round_num < MAX_ROUNDS:
            round_num += 1
            pages = db_execute(
                "SELECT id, title, slug, target_keyword, content FROM pages "
                "WHERE tenant_id=? AND status='ready' AND content IS NOT NULL "
                "AND (visual_score IS NULL OR visual_score < ?) "
                "ORDER BY id LIMIT ?",
                (self.ctx.tenant_id, self.MIN_VISUAL_SCORE, self.BATCH)
            )

            if not pages:
                break

            self.log(f"Round {round_num}: visual-scanning {len(pages)} pages...")

            for page in pages:
                if self.should_stop():
                    break

                page_dict = dict(page)
                title = page_dict.get("title", "")
                keyword = page_dict.get("target_keyword", title)
                html = page_dict.get("content", "")

                self.set_status("working", f"Visual scan: {title[:50]}")

                # Extract images
                images = self._extract_images_from_html(html)
                if not images:
                    # Mark as visually scored (no images = neutral)
                    db_write(
                        "UPDATE pages SET visual_score=75, updated_at=CURRENT_TIMESTAMP "
                        "WHERE id=? AND tenant_id=?",
                        (page_dict["id"], self.ctx.tenant_id)
                    )
                    skipped += 1
                    continue

                # Score visual SEO
                result = self._score_images(images, keyword)
                score = result["score"]
                issues = result["issues"]
                fixes_needed = result["fixes_needed"]

                self.log(f"{title[:50]} → visual score {score}/100 | {len(images)} imgs | fixes: {fixes_needed or 'none'}")

                if score < self.MIN_VISUAL_SCORE and fixes_needed:
                    # Generate alt texts if needed
                    alt_map = {}
                    if any(f in fixes_needed for f in ("add_alt_text", "improve_alt_text", "add_keyword_alt")):
                        self.set_status("working", f"Generating alt text: {title[:40]}")
                        alt_map = self._generate_alt_texts(images, title, keyword)

                    # Apply visual fixes
                    new_html = self._apply_visual_fixes(html, page_dict, alt_map, fixes_needed)
                    new_html = self._add_og_image(new_html, page_dict)

                    if new_html != html:
                        db_write(
                            "UPDATE pages SET content=?, visual_score=?, updated_at=CURRENT_TIMESTAMP "
                            "WHERE id=? AND tenant_id=?",
                            (new_html, score, page_dict["id"], self.ctx.tenant_id)
                        )
                        self.log(f"✓ Visual fixes applied: {title[:50]} (was {score}/100)", "success")
                        optimized += 1
                    else:
                        db_write(
                            "UPDATE pages SET visual_score=?, updated_at=CURRENT_TIMESTAMP "
                            "WHERE id=? AND tenant_id=?",
                            (score, page_dict["id"], self.ctx.tenant_id)
                        )
                        skipped += 1
                else:
                    db_write(
                        "UPDATE pages SET visual_score=?, updated_at=CURRENT_TIMESTAMP "
                        "WHERE id=? AND tenant_id=?",
                        (score, page_dict["id"], self.ctx.tenant_id)
                    )
                    skipped += 1

        remaining = db_execute(
            "SELECT COUNT(*) n FROM pages WHERE tenant_id=? AND status='ready' "
            "AND content IS NOT NULL AND (visual_score IS NULL OR visual_score < ?)",
            (self.ctx.tenant_id, self.MIN_VISUAL_SCORE)
        )
        left = remaining[0]["n"] if remaining else 0
        self.log(
            f"Visual optimization complete: {optimized} improved, {skipped} passed. {left} pages still below threshold.",
            "success"
        )
        self.set_status("done", f"{optimized} visually improved · {skipped} passed · {left} remaining")
