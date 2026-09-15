"""Measure images; fix evidence-backed alt text, actual dimensions and loading.
Existing responsive sources are preserved. Assets are not renamed or recompressed.
"""
import json
import re
from bs4 import BeautifulSoup
from denzo.agents.base_agent import (
    TenantAwareBaseAgent, ClientContext,
    db_write, db_execute, strip_json_fences
)


class VisualContentOptimizer(TenantAwareBaseAgent):

    PREREQUISITES = ["Programmatic SEO"]

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

        missing_alt = [img for img in images if not img.get("alt", "").strip() and not img.get("decorative")]
        generic_alt = [img for img in images if img.get("alt", "").strip()
                       and img["alt"].strip().lower() in ("image", "photo", "picture", "img", "banner")]
        missing_lazy = [img for i,img in enumerate(images) if i>0 and img.get("loading", "") not in ("lazy","eager")]
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

        return {
            "score": max(0, min(100, score)),
            "issues": issues,
            "fixes_needed": list(set(fixes))
        }

    def _generate_alt_texts(self, images, page_title, keyword):
        from urllib.parse import urljoin
        from denzo.urls import site_base_url
        output = {}
        for image in images[:10]:
            if self.should_stop():
                break
            # An explicitly empty alt may be intentional decoration.
            if image.get('decorative') or (image.get('alt','').strip() and image['alt'].lower() not in ('image','photo','picture','img','banner')):
                continue
            url = urljoin(site_base_url(self.ctx),image.get('src',''))
            prompt = ('Describe only what is visible in this image for an accessible alt attribute. '
                      'Do not infer identities, certification, location or before/after results. '
                      'Do not force a keyword. Return JSON {"alt":"..."}, at most 180 characters. '
                      f'Page context: {page_title}')
            try:
                from denzo.auditor.safe_fetch import validate_url
                validate_url(url)
                result = json.loads(strip_json_fences(self.call_claude_vision(url,prompt,max_tokens=180)))
                alt = result.get('alt','').strip()
                if alt:
                    output[image['src']] = alt[:180]
            except Exception as exc:
                self.log(f'Image description unavailable: {type(exc).__name__}','warning')
        return output

    def _apply_visual_fixes(self, html, page, alt_map, fixes_needed):
        from urllib.parse import urljoin
        from denzo.urls import site_base_url
        soup = BeautifulSoup(html,'html.parser')
        changed = False
        for index,img in enumerate(soup.find_all('img')):
            src = img.get('src','')
            decorative = img.get('role')=='presentation' or img.get('aria-hidden')=='true' or ('alt' in img.attrs and img['alt']=='')
            alt = alt_map.get(src) or alt_map.get(src.split('/')[-1].split('?')[0])
            if alt and not decorative and img.get('alt','').lower() in ('','image','photo','picture','img','banner'):
                img['alt']=alt; changed=True
            parent_classes = ' '.join(str(c) for p in img.parents if hasattr(p,'get') for c in (p.get('class',[]) if isinstance(p.get('class'),list) else [p.get('class','')]))
            hero = index==0 or any(word in parent_classes.lower() for word in ('hero','banner','above-fold'))
            loading = 'eager' if hero else 'lazy'
            if img.get('loading') != loading:
                img['loading']=loading; changed=True
            if hero and img.get('fetchpriority')!='high':
                img['fetchpriority']='high'; changed=True
            if not hero and img.get('decoding')!='async':
                img['decoding']='async'; changed=True
            if src and (not img.get('width') or not img.get('height')):
                try:
                    from denzo.media import image_dimensions
                    width,height = image_dimensions(urljoin(site_base_url(self.ctx),src))
                    img['width'],img['height']=str(width),str(height);changed=True
                except (ValueError,OSError):
                    pass
        return str(soup) if changed else html

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
                "decorative": img.get("role")=="presentation" or img.get("aria-hidden")=="true" or ("alt" in img.attrs and img["alt"]==""),
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
        MAX_ROUNDS = 1  # One measured pass; unresolved issues remain visible.
        from denzo.runtime_limits import setting

        while not self.should_stop() and round_num < MAX_ROUNDS:
            round_num += 1
            pages = db_execute(
                "SELECT id, title, slug, target_keyword, content FROM pages "
                "WHERE tenant_id=? AND status='ready' AND content IS NOT NULL "
                "AND (visual_score IS NULL OR visual_score < ?) "
                "ORDER BY id LIMIT ?",
                (self.ctx.tenant_id, self.MIN_VISUAL_SCORE, min(self.BATCH,setting('DENZO_PAGE_BATCH_SIZE',10,1,50)))
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
                        "UPDATE pages SET visual_score=50, updated_at=CURRENT_TIMESTAMP "
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

                if fixes_needed:
                    # Generate alt texts if needed
                    alt_map = {}
                    if any(f in fixes_needed for f in ("add_alt_text", "improve_alt_text", "add_keyword_alt")):
                        self.set_status("working", f"Generating alt text: {title[:40]}")
                        alt_map = self._generate_alt_texts(images, title, keyword)

                    # Apply visual fixes
                    new_html = self._apply_visual_fixes(html, page_dict, alt_map, fixes_needed)

                    if new_html != html:
                        # Bump score to reflect fixes applied (alt text, lazy loading, dims, filenames)
                        fixed_score = self._score_images(self._extract_images_from_html(new_html),keyword)["score"]
                        db_write(
                            "UPDATE pages SET content=?, visual_score=?, updated_at=CURRENT_TIMESTAMP "
                            "WHERE id=? AND tenant_id=?",
                            (new_html, fixed_score, page_dict["id"], self.ctx.tenant_id)
                        )
                        self.log(f"✓ Visual fixes applied: {title[:50]} ({score} → {fixed_score}/100)", "success")
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
