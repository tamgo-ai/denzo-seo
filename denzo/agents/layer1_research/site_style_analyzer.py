"""
Site Style Analyzer — Layer 1
Crawls the client website, extracts ALL images, analyzes each one with AI Vision,
and builds a complete brand style guide. Results stored in site_images table.
"""
import json
import re
import time
import requests
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from denzo.agents.base_agent import TenantAwareBaseAgent, ClientContext, db_execute, db_write, strip_json_fences


# Max images to analyze with vision (cost/time control)
MAX_VISION_ANALYSIS = 30
# Min image dimensions to consider "real" (skip icons, pixels)
MIN_WIDTH  = 60
MIN_HEIGHT = 60


class SiteStyleAnalyzer(TenantAwareBaseAgent):

    def __init__(self, ctx: ClientContext):
        super().__init__("Site Style Analyzer", ctx, layer=1, color="teal")
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Extraction helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _fetch(self, url: str, timeout: int = 20) -> tuple:
        """Fetch a URL using stealth_fetch (4-pass Cloudflare bypass).
        Returns (soup, html, final_url) or (None, '', url)."""
        from denzo.agents.utils.stealth_fetch import fetch_html
        result = fetch_html(url, timeout=timeout, log_fn=lambda m: self.log(m, "info"))
        if result["ok"]:
            html = result["html"]
            return BeautifulSoup(html, "html.parser"), html, url
        self.log(f"All fetch passes failed for {url}: {result.get('error', 'unknown error')}", "warning")
        return None, "", url

    def _is_junk_image(self, url: str, width: str, height: str) -> bool:
        """True if the image is a tracking pixel, icon, or noise."""
        # Tracking pixels and analytics
        junk_domains = [
            "facebook.com/tr", "google-analytics", "doubleclick",
            "scorecardresearch", "pixel.gif", "beacon", "tracker",
            "googletagmanager", "analytics", "stat.", "count."
        ]
        if any(j in url.lower() for j in junk_domains):
            return True
        if url.startswith("data:"):
            return True
        # Tiny images
        try:
            w = int(str(width).replace("px", "").strip()) if width else 0
            h = int(str(height).replace("px", "").strip()) if height else 0
            if (w > 0 and w < MIN_WIDTH) or (h > 0 and h < MIN_HEIGHT):
                return True
        except (ValueError, TypeError):
            pass
        # SVG icons and favicon patterns
        lower = url.lower()
        if any(lower.endswith(ext) for ext in [".ico", ".svg", ".cur"]):
            return True
        if "favicon" in lower or "icon-" in lower or "/icons/" in lower:
            return True
        return False

    def _resolve_url(self, src: str, base_url: str) -> str:
        if not src:
            return ""
        src = src.strip()
        if src.startswith("//"):
            return "https:" + src
        if src.startswith("http"):
            return src
        return urljoin(base_url, src)

    def _image_context(self, img_tag, base_url: str) -> dict | None:
        src = (
            img_tag.get("src") or
            img_tag.get("data-src") or
            img_tag.get("data-lazy-src") or
            img_tag.get("data-original") or ""
        )
        url = self._resolve_url(src, base_url)
        if not url:
            return None

        alt    = img_tag.get("alt", "").strip()
        width  = img_tag.get("width", "")
        height = img_tag.get("height", "")

        if self._is_junk_image(url, width, height):
            return None

        # Determine context by walking DOM
        context = "general"
        parent = img_tag.parent
        for _ in range(8):
            if parent is None:
                break
            tag_name  = getattr(parent, "name", "") or ""
            tag_id    = (parent.get("id") or "").lower()
            tag_class = " ".join(parent.get("class") or []).lower()
            combined  = f"{tag_name} {tag_id} {tag_class}"
            if any(k in combined for k in ["header", "navbar", "nav ", "logo", "brand"]):
                context = "header"; break
            elif any(k in combined for k in ["hero", "banner", "jumbotron", "slider", "carousel", "cover"]):
                context = "hero"; break
            elif any(k in combined for k in ["gallery", "portfolio", "grid", "masonry", "photos"]):
                context = "gallery"; break
            elif any(k in combined for k in ["footer"]):
                context = "footer"; break
            elif any(k in combined for k in ["about", "team", "staff", "who-we", "history"]):
                context = "about"; break
            elif any(k in combined for k in ["service", "feature", "benefit", "offer"]):
                context = "services"; break
            elif any(k in combined for k in ["testimonial", "review", "client"]):
                context = "testimonials"; break
            elif any(k in combined for k in ["blog", "article", "post", "news"]):
                context = "blog"; break
            parent = getattr(parent, "parent", None)

        return {"url": url, "alt": alt, "context": context, "width": str(width), "height": str(height)}

    def _extract_og_images(self, soup: BeautifulSoup, base_url: str) -> list:
        images = []
        for prop in ["og:image", "og:image:secure_url", "twitter:image"]:
            tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
            if tag:
                content = tag.get("content", "").strip()
                if content:
                    url = self._resolve_url(content, base_url)
                    if url and not self._is_junk_image(url, "", ""):
                        images.append({"url": url, "alt": "og:image", "context": "hero", "width": "", "height": ""})
        return images

    def _extract_srcset_images(self, soup: BeautifulSoup, base_url: str) -> list:
        """Extract images from srcset attributes (responsive images)."""
        images = []
        for tag in soup.find_all(srcset=True):
            for part in tag.get("srcset", "").split(","):
                src = part.strip().split(" ")[0]
                if src:
                    url = self._resolve_url(src, base_url)
                    if url and not self._is_junk_image(url, "", ""):
                        images.append({"url": url, "alt": tag.get("alt", ""), "context": "general", "width": "", "height": ""})
        return images

    def _extract_css_backgrounds(self, soup: BeautifulSoup, base_url: str) -> list:
        """Extract background-image URLs from inline styles."""
        images = []
        bg_pattern = re.compile(r'url\(["\']?([^"\')\s]+)["\']?\)', re.IGNORECASE)
        for tag in soup.find_all(style=True):
            for match in bg_pattern.findall(tag.get("style", "")):
                url = self._resolve_url(match, base_url)
                if url and not self._is_junk_image(url, "", ""):
                    images.append({"url": url, "alt": "", "context": "background", "width": "", "height": ""})
        for style_tag in soup.find_all("style"):
            for match in bg_pattern.findall(style_tag.get_text()):
                url = self._resolve_url(match, base_url)
                if url and not self._is_junk_image(url, "", ""):
                    images.append({"url": url, "alt": "", "context": "background", "width": "", "height": ""})
        return images

    def _extract_colors(self, soup: BeautifulSoup, external_css: str = "") -> list:
        color_pattern = re.compile(
            r'#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b|'
            r'rgb\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*\)'
        )
        colors = set()
        for style_tag in soup.find_all("style"):
            colors.update(color_pattern.findall(style_tag.get_text()))
        for tag in soup.find_all(style=True):
            colors.update(color_pattern.findall(tag["style"]))
        if external_css:
            colors.update(color_pattern.findall(external_css))
        noise = {'#fff', '#ffffff', '#000', '#000000', '#ccc', '#cccccc',
                 '#eee', '#eeeeee', '#333', '#333333', '#666', '#666666'}
        return [c for c in colors if c.lower() not in noise][:25]

    def _extract_fonts(self, soup: BeautifulSoup, external_css: str = "") -> list:
        font_pattern = re.compile(r'font-family\s*:\s*([^;}{]+)', re.IGNORECASE)
        fonts = set()
        sources = [style_tag.get_text() for style_tag in soup.find_all("style")]
        sources += [tag.get("style", "") for tag in soup.find_all(style=True)]
        if external_css:
            sources.append(external_css)
        for src in sources:
            for match in font_pattern.findall(src):
                clean = match.strip().strip("'\"").split(",")[0].strip().strip("'\"")
                if clean and len(clean) > 1:
                    fonts.add(clean)
        # Google Fonts links
        for link in soup.find_all("link", {"rel": "stylesheet"}):
            href = link.get("href", "")
            if "fonts.googleapis.com" in href:
                for gf in re.findall(r'family=([^&:+|]+)', href):
                    fonts.add(gf.replace("+", " ").split(":")[0])
        return list(fonts)[:10]

    def _fetch_external_css(self, soup: BeautifulSoup, base_url: str) -> str:
        combined = ""
        for link in soup.find_all("link", {"rel": "stylesheet"})[:6]:
            href = link.get("href", "")
            if not href or "fonts.googleapis.com" in href:
                continue
            css_url = self._resolve_url(href, base_url)
            if not css_url.startswith(("http://", "https://")):
                continue
            try:
                r = requests.get(css_url, timeout=8, headers=self._headers)
                if r.status_code == 200:
                    combined += r.text[:60000]
            except Exception:
                pass
        return combined

    def _crawl_internal_pages(self, base_url: str, homepage_soup: BeautifulSoup) -> list:
        """Crawl up to 5 internal pages to gather more images."""
        parsed = urlparse(base_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        candidates = set()

        for a in homepage_soup.find_all("a", href=True)[:60]:
            href = a["href"]
            if href.startswith("/") and not href.startswith("//") and "." not in href.split("/")[-1]:
                candidates.add(href)
            elif href.startswith(origin):
                path = href.replace(origin, "")
                if path and "." not in path.split("/")[-1]:
                    candidates.add(path)

        # Priority paths
        for p in ["/about", "/services", "/gallery", "/work", "/portfolio",
                  "/contact", "/blog", "/team", "/sitemap.xml"]:
            candidates.add(p)

        all_images = []
        crawled = 0
        for path in list(candidates)[:8]:
            if crawled >= 5 or self.should_stop():
                break
            try:
                url = origin + path if not path.startswith("http") else path
                soup, html, final_url = self._fetch(url, timeout=10)
                if not soup:
                    continue

                # Parse sitemap XML
                if path.endswith(".xml"):
                    img_urls = re.findall(r'<image:loc>(.*?)</image:loc>', html)
                    img_urls += re.findall(r'<loc>(.*?\.(?:jpg|jpeg|png|webp|gif))</loc>', html, re.IGNORECASE)
                    for img_url in img_urls[:15]:
                        all_images.append({"url": img_url.strip(), "alt": "", "context": "sitemap", "width": "", "height": ""})
                else:
                    for img in soup.find_all("img"):
                        img_data = self._image_context(img, final_url)
                        if img_data:
                            all_images.append(img_data)
                    all_images.extend(self._extract_og_images(soup, final_url))
                    all_images.extend(self._extract_srcset_images(soup, final_url))

                crawled += 1
                self.log(f"Crawled {path} — +{len(all_images)} images total", "info")
            except Exception:
                pass
        return all_images

    # ──────────────────────────────────────────────────────────────────────────
    # Vision analysis
    # ──────────────────────────────────────────────────────────────────────────

    def _analyze_image(self, img: dict) -> dict:
        """Use Claude Vision to describe an image and tag it for content use."""
        prompt = f"""You are analyzing a website image for a business called "{self.ctx.client_name}" ({self.ctx.industry_vertical}).

Analyze this image and respond with ONLY a JSON object (no markdown):
{{
  "description": "2-3 sentence description of what is shown in the image",
  "subject": "main subject in 3-5 words",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
  "suitable_for": ["hero", "blog_post", "service_page", "about_page", "gallery", "testimonials"],
  "mood": "professional|friendly|dynamic|calm|technical|luxury",
  "has_people": true or false,
  "has_text": true or false,
  "color_palette": ["#hex1", "#hex2"]
}}

suitable_for should list ALL page types where this image would be appropriate.
Return ONLY valid JSON, no explanation."""

        raw = self.call_claude_vision(img["url"], prompt, max_tokens=400)

        if not raw or raw.startswith("__"):
            return {}

        try:
            raw_clean = strip_json_fences(raw)
            return json.loads(raw_clean)
        except Exception:
            return {}

    # ──────────────────────────────────────────────────────────────────────────
    # Main run
    # ──────────────────────────────────────────────────────────────────────────

    def run(self):
        self.log("Starting comprehensive site analysis — extracting all images...")
        self.set_status("working", "Fetching homepage")

        url = self.ctx.website_url or self.ctx.domain
        if not url:
            self.log("No website URL configured. Add it in client Settings.", "warning")
            self.set_status("idle", "No website URL")
            return

        if not url.startswith(("http://", "https://")):
            url = "https://" + url.lstrip("/")

        soup, html, base_url = self._fetch(url)
        if not soup:
            # Fallback: site is bot-protected (Cloudflare enterprise, etc.)
            # Generate a synthetic style guide from brand knowledge via Claude
            self.log("Site is bot-protected — generating synthetic brand style guide via AI", "warning")
            self.set_status("working", "Generating AI brand style guide (site blocked)")
            fallback_prompt = f"""{self.ctx.to_prompt_block()}

The website {url} is protected and cannot be crawled.
Based on your knowledge of this brand and industry, generate a realistic brand style guide.

Return ONLY this JSON, no markdown:
{{
  "primary_colors": ["#hex1", "#hex2"],
  "accent_colors": ["#hex3"],
  "fonts": ["Font Name 1", "Font Name 2"],
  "tone_adjectives": ["adj1", "adj2", "adj3"],
  "writing_tone": "1-2 sentence description of brand voice",
  "layout_style": "modern-minimal|corporate-clean|bold-vibrant|warm-friendly|technical-detailed",
  "key_sections": ["hero", "inventory", "services", "financing", "contact"],
  "content_notes": "Brief notes for content writers based on this brand"
}}"""
            raw = self.call_claude(fallback_prompt, max_tokens=600)
            style_guide = {}
            if raw:
                try:
                    style_guide = json.loads(strip_json_fences(raw))
                except Exception:
                    pass
            if not style_guide:
                style_guide = {"layout_style": "corporate-clean", "tone_adjectives": ["professional"],
                               "writing_tone": f"Professional {self.ctx.industry_vertical} brand.", "content_notes": ""}
            db_write(
                "INSERT OR REPLACE INTO settings (tenant_id, key, value, updated_at) VALUES (?,?,?,CURRENT_TIMESTAMP)",
                (self.tenant_id, "site_style_guide", json.dumps(style_guide))
            )
            self.log(f"Complete (AI fallback). 0 images crawled · style guide generated from brand knowledge.", "success")
            self.set_status("done", "Complete (AI fallback — site bot-protected)")
            return

        self.log(f"Fetched {base_url} — {len(html):,} bytes")
        is_spa = len(soup.get_text(" ", strip=True)) < 600
        if is_spa:
            self.log("SPA/JS-rendered site detected — expanding extraction strategy", "info")

        # ── Step 1: Extract style signals ─────────────────────────────────────
        self.set_status("working", "Extracting CSS colors and fonts")
        external_css = self._fetch_external_css(soup, base_url)
        colors = self._extract_colors(soup, external_css)
        fonts  = self._extract_fonts(soup, external_css)
        self.log(f"Style signals: {len(colors)} colors, {len(fonts)} fonts", "info")

        # ── Step 2: Collect ALL images from homepage ───────────────────────────
        self.set_status("working", "Collecting images from homepage")
        all_images = []
        seen_urls  = set()

        def _add(img_list):
            for img in img_list:
                if img and img.get("url") and img["url"] not in seen_urls:
                    seen_urls.add(img["url"])
                    all_images.append(img)

        # OG/Twitter meta (most reliable for SPAs)
        _add(self._extract_og_images(soup, base_url))
        # Regular img tags
        _add([self._image_context(img, base_url) for img in soup.find_all("img")])
        # Srcset responsive images
        _add(self._extract_srcset_images(soup, base_url))
        # CSS background images
        _add(self._extract_css_backgrounds(soup, base_url))

        self.log(f"Homepage: {len(all_images)} images found", "info")

        # ── Step 3: Crawl sub-pages ────────────────────────────────────────────
        if not self.should_stop():
            self.set_status("working", "Crawling internal pages for more images")
            extra = self._crawl_internal_pages(base_url, soup)
            _add(extra)

        self.log(f"Total unique images found: {len(all_images)}", "info")

        # ── Step 4: Save all images to DB (unanalyzed) ────────────────────────
        self.set_status("working", f"Saving {len(all_images)} images to database")
        saved = 0
        for img in all_images:
            try:
                db_write(
                    """INSERT OR IGNORE INTO site_images
                       (tenant_id, url, alt, width, height, context, analyzed)
                       VALUES (?,?,?,?,?,?,0)""",
                    (self.tenant_id, img["url"], img.get("alt",""),
                     img.get("width",""), img.get("height",""), img.get("context","general"))
                )
                saved += 1
            except Exception:
                pass
        self.log(f"Saved {saved} images to database", "success")

        # ── Step 5: Vision analysis on each image ─────────────────────────────
        to_analyze = all_images[:MAX_VISION_ANALYSIS]
        self.log(f"Analyzing {len(to_analyze)} images with AI Vision...", "info")
        analyzed_ok = 0
        analyzed_fail = 0

        for i, img in enumerate(to_analyze):
            if self.should_stop():
                break
            self.set_status("working", f"Analyzing image {i+1}/{len(to_analyze)}: {img['url'][-50:]}")

            analysis = self._analyze_image(img)

            if analysis:
                db_write(
                    """UPDATE site_images SET
                       description=?, tags=?, suitable_for=?, analyzed=1
                       WHERE tenant_id=? AND url=?""",
                    (
                        analysis.get("description", ""),
                        json.dumps(analysis.get("tags", [])),
                        json.dumps(analysis.get("suitable_for", [])),
                        self.tenant_id,
                        img["url"]
                    )
                )
                analyzed_ok += 1
                subject = analysis.get("subject", "")
                self.log(f"✓ [{img['context']}] {subject or img['url'][-40:]}", "success")
            else:
                analyzed_fail += 1
                self.log(f"✗ Could not analyze: {img['url'][-50:]}", "warning")

        # ── Step 6: Build style guide ──────────────────────────────────────────
        self.set_status("working", "Generating brand style guide")

        headings = [{"level": h.name, "text": h.get_text(" ", strip=True)[:100]}
                    for level in ["h1","h2","h3"]
                    for h in soup.find_all(level)[:4]]
        nav_items = []
        nav = soup.find("nav") or soup.find(id=re.compile(r"nav|menu", re.I))
        if nav:
            nav_items = [a.get_text(" ", strip=True) for a in nav.find_all("a", limit=12) if a.get_text(strip=True)]

        style_summary = {
            "colors": colors[:15],
            "fonts": fonts,
            "headings": headings,
            "nav_items": nav_items,
            "total_images_found": len(all_images),
            "images_analyzed": analyzed_ok,
        }

        prompt = f"""{self.ctx.to_prompt_block()}

Visual style signals extracted from {base_url}:
{json.dumps(style_summary, indent=2, ensure_ascii=False)}

As a brand strategist, produce a concise style guide for AI content generation.
Return ONLY this JSON, no markdown:
{{
  "primary_colors": ["#hex1", "#hex2"],
  "accent_colors": ["#hex3"],
  "fonts": ["Font Name 1"],
  "tone_adjectives": ["professional", "trustworthy"],
  "writing_tone": "1-2 sentence description of brand voice",
  "layout_style": "modern-minimal|corporate-clean|bold-vibrant|warm-friendly|technical-detailed",
  "key_sections": ["hero", "services", "about", "testimonials", "contact"],
  "content_notes": "Brief notes for content writers"
}}"""

        raw = self.call_claude(prompt, max_tokens=600)
        style_guide = {}
        if raw:
            try:
                style_guide = json.loads(strip_json_fences(raw))
            except Exception:
                pass

        if not style_guide:
            style_guide = {
                "primary_colors": colors[:3],
                "accent_colors": [],
                "fonts": fonts,
                "tone_adjectives": [],
                "writing_tone": "",
                "layout_style": "corporate-clean",
                "key_sections": [],
                "content_notes": "",
            }

        # Save style guide
        db_write(
            "INSERT OR REPLACE INTO settings (tenant_id, key, value, updated_at) VALUES (?,?,?,CURRENT_TIMESTAMP)",
            (self.tenant_id, "site_style_guide", json.dumps(style_guide))
        )

        self.log(
            f"Complete. {len(all_images)} images found · {analyzed_ok} analyzed · "
            f"{len(colors)} colors · {len(fonts)} fonts extracted",
            "success"
        )
        self.set_status(
            "done",
            f"{len(all_images)} images · {analyzed_ok} analyzed · style guide ready"
        )
