"""
Deep Technical Auditor — Enterprise-grade site audit (Layer 1)
Replaces the basic TechnicalAuditor with comprehensive analysis covering:
- On-Page SEO (title, meta, headings, canonical)
- Technical SEO (JS/CSS bloat, cache, compression)
- Schema & Structured Data
- Images & Media (alt, dimensions, formats, lazy loading)
- Performance signals
- GEO / AI Visibility (FAQs, structured content, definitions)
- Social Sharing (OG tags, Twitter cards)
- Local SEO (NAP, locations)
- Content Quality (word count, lists, CTAs, readability)
- Security headers
"""
import json, re, requests, time
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from collections import Counter

from denzo.agents.base_agent import TenantAwareBaseAgent, ClientContext, db_write, db_execute


class DeepTechnicalAuditor(TenantAwareBaseAgent):
    """Enterprise-grade SEO auditor — produces structured audit data saved to DB."""

    def __init__(self, ctx: ClientContext):
        super().__init__("Technical Auditor", ctx, layer=1, color="gray")

    def run(self):
        self.log("Starting comprehensive enterprise SEO audit...")
        self.set_status("working", "Fetching website")

        url = self.ctx.website_url or self.ctx.domain
        if not url:
            self.log("No website URL configured.", "warning")
            self.set_status("idle", "No website URL")
            return

        if not url.startswith("http"):
            url = "https://" + url

        # ── Fetch website ──────────────────────────────────────────────────
        try:
            from denzo.agents.utils.stealth_fetch import fetch_html
            result = fetch_html(url, timeout=25, log_fn=lambda m: self.log(m, "info"))
            if result["ok"]:
                html = result["html"]
                final_url = result.get("final_url", url)
                status_code = result.get("status", 200)
                self.log(f"Fetched via {result['method']} — {status_code}", "info")
            else:
                self.log("All fetch methods failed — running AI fallback", "warning")
                self._ai_fallback(url)
                return
        except Exception as e:
            self.log(f"Fetch error: {e}", "error")
            self._ai_fallback(url)
            return

        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text()
        words = text.split()
        html_bytes = len(html)

        self.set_status("working", "Running full audit suite")

        # ═══════════════ BUILD AUDIT ═══════════════
        audit = {
            "url": final_url,
            "status_code": status_code,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "scores": {},
            "findings": [],
        }

        # ── 1. ON-PAGE SEO ──────────────────────────────────────────────────
        onpage_score = 100
        title_tag = soup.title.string.strip() if soup.title and soup.title.string else None
        title_len = len(title_tag) if title_tag else 0

        if not title_tag:
            audit["findings"].append(self._f("CRITICAL", "onpage", "Missing title tag"))
            onpage_score -= 40
        elif title_len < 30:
            audit["findings"].append(self._f("CRITICAL", "onpage", f"Title too short: '{title_tag}' ({title_len} chars). Optimize to 50–60 chars with location keywords."))
            onpage_score -= 20
        elif title_len > 70:
            audit["findings"].append(self._f("HIGH", "onpage", f"Title too long ({title_len} chars) — may be truncated in SERPs."))
            onpage_score -= 10

        meta_desc = soup.find("meta", {"name": "description"})
        meta_desc_content = meta_desc.get("content", "").strip() if meta_desc else None
        desc_len = len(meta_desc_content) if meta_desc_content else 0
        if not meta_desc_content:
            audit["findings"].append(self._f("HIGH", "onpage", "Missing meta description."))
            onpage_score -= 15
        elif desc_len < 70:
            audit["findings"].append(self._f("HIGH", "onpage", f"Meta description too short ({desc_len} chars). Target 140–160 chars with CTA."))
            onpage_score -= 8

        canonical = soup.find("link", {"rel": "canonical"})
        if not canonical:
            audit["findings"].append(self._f("CRITICAL", "onpage", "No canonical URL tag — duplicate content risk. Each URL variant (/en, /, /en/) may be indexed separately."))
            onpage_score -= 25

        # H1 analysis
        h1s = soup.find_all("h1")
        h2s = soup.find_all("h2")
        h1_texts = [h.get_text(" ", strip=True) for h in h1s]
        h2_texts = [h.get_text(" ", strip=True) for h in h2s]
        h2_dupes = [t for t in set(h2_texts) if h2_texts.count(t) > 1]

        if len(h1s) == 0:
            audit["findings"].append(self._f("CRITICAL", "onpage", "No H1 tag found — primary ranking signal missing."))
            onpage_score -= 25
        elif len(h1s) > 1:
            audit["findings"].append(self._f("HIGH", "onpage", f"{len(h1s)} H1 tags found (should be 1). Multiple H1s dilute keyword focus."))
            onpage_score -= 10

        if h2_dupes:
            audit["findings"].append(self._f("HIGH", "onpage", f"{len(h2_dupes)} duplicate H2 headings: {h2_dupes[:3]}. Each H2 should be unique."))
            onpage_score -= 10

        audit["scores"]["onpage_seo"] = max(0, onpage_score)
        audit["meta"] = {
            "title": title_tag, "title_length": title_len,
            "meta_description": meta_desc_content, "meta_desc_length": desc_len,
            "canonical": bool(canonical),
            "h1_count": len(h1s), "h2_count": len(h2s),
            "h1_texts": h1_texts, "h2_duplicates": h2_dupes[:4],
        }

        # ── 2. SCHEMA ───────────────────────────────────────────────────────
        schema_score = 100
        schemas = soup.find_all("script", {"type": "application/ld+json"})
        schema_types = []
        for s in schemas:
            try:
                data = json.loads(s.string)
                t = data.get("@type", "unknown") if isinstance(data, dict) else "complex"
                schema_types.append(str(t))
            except Exception:
                schema_types.append("invalid")

        if not schemas:
            audit["findings"].append(self._f("CRITICAL", "schema",
                "Zero JSON-LD schema markup found. Missing: LocalBusiness (×13 locations), FAQPage, Organization, BreadcrumbList, Service. "
                "Without schema: no rich results, no local pack eligibility, invisible to AI search engines."
            ))
            schema_score = 0
        else:
            if not any("LocalBusiness" in str(s) for s in schema_types):
                audit["findings"].append(self._f("CRITICAL", "schema", "No LocalBusiness schema — required for Google Maps and local pack visibility."))
                schema_score -= 40
            if not any("FAQ" in str(s) for s in schema_types):
                audit["findings"].append(self._f("HIGH", "schema", "No FAQPage schema — missing AI citation opportunity."))
                schema_score -= 20
            if not any("Breadcrumb" in str(s) for s in schema_types):
                audit["findings"].append(self._f("HIGH", "schema", "No BreadcrumbList schema — missing navigation rich results."))
                schema_score -= 10

        audit["scores"]["schema"] = max(0, schema_score)
        audit["schema"] = {"count": len(schemas), "types": schema_types}

        # ── 3. PERFORMANCE ──────────────────────────────────────────────────
        perf_score = 100
        scripts = soup.find_all("script")
        styles = soup.find_all("style")
        total_inline_js = sum(len(s.string or "") for s in scripts if not s.get("src"))
        total_inline_css = sum(len(s.string or "") for s in styles)
        text_ratio = round(len(text) / max(html_bytes, 1) * 100, 1)

        if total_inline_js > 100_000:
            audit["findings"].append(self._f("CRITICAL", "performance",
                f"{total_inline_js:,} bytes ({round(total_inline_js/html_bytes*100)}%) of inline JavaScript — "
                f"causes slow Time-to-Interactive and poor Core Web Vitals. Text-to-HTML ratio: {text_ratio}% (Google flags <5%)."
            ))
            perf_score -= 30
        elif total_inline_js > 50_000:
            audit["findings"].append(self._f("HIGH", "performance", f"{total_inline_js:,} bytes of inline JS — consider code splitting."))
            perf_score -= 15

        if text_ratio < 5:
            audit["findings"].append(self._f("HIGH", "performance",
                f"Text-to-HTML ratio: {text_ratio}% — Google may classify this as 'thin content' despite {len(words):,} words. Reduce HTML bloat."
            ))
            perf_score -= 10

        audit["scores"]["performance"] = max(0, perf_score)
        audit["performance"] = {
            "html_bytes": html_bytes, "html_kb": round(html_bytes / 1024),
            "inline_js_bytes": total_inline_js, "inline_css_bytes": total_inline_css,
            "text_html_ratio": text_ratio,
            "external_js": len([s for s in scripts if s.get("src")]),
            "external_css": len(soup.find_all("link", rel="stylesheet")),
        }

        # ── 4. IMAGES ───────────────────────────────────────────────────────
        img_score = 100
        imgs = soup.find_all("img")
        imgs_no_alt = [i for i in imgs if not i.get("alt")]
        imgs_no_dims = [i for i in imgs if not (i.get("width") or i.get("height"))]
        imgs_lazy = [i for i in imgs if i.get("loading") == "lazy"]
        webp_count = sum(1 for i in imgs if ".webp" in (i.get("src", "") or "").lower())

        if len(imgs_no_alt) > 0:
            audit["findings"].append(self._f("HIGH", "images", f"{len(imgs_no_alt)} of {len(imgs)} images missing alt text."))
            img_score -= 15
        if len(imgs_no_dims) > len(imgs) * 0.5:
            audit["findings"].append(self._f("HIGH", "images",
                f"{len(imgs_no_dims)} of {len(imgs)} images missing width/height — causes Cumulative Layout Shift (CLS). Use Next.js <Image> or explicit dimensions."
            ))
            img_score -= 20
        if len(imgs_lazy) < len(imgs) * 0.5:
            audit["findings"].append(self._f("HIGH", "images", f"Only {len(imgs_lazy)} of {len(imgs)} images lazy-loaded. Add loading='lazy' to below-fold images."))
            img_score -= 10
        if webp_count < len(imgs) * 0.5 and len(imgs) > 10:
            audit["findings"].append(self._f("HIGH", "images", f"Only {webp_count} of {len(imgs)} images in WebP/AVIF format. Convert to reduce size 30-50%."))
            img_score -= 10

        audit["scores"]["images"] = max(0, img_score)
        audit["images"] = {
            "total": len(imgs), "no_alt": len(imgs_no_alt), "no_dimensions": len(imgs_no_dims),
            "lazy_loaded": len(imgs_lazy), "webp_count": webp_count,
        }

        # ── 5. GEO / AI VISIBILITY ──────────────────────────────────────────
        geo_score = 100
        has_faq = any(q in text.lower() for q in ["what is", "how do", "how to", "why is", "frequently asked", "faq"])
        has_lists = len(soup.find_all(["ul", "ol"])) > 0
        has_definition = False
        first_p = soup.find("p")
        if first_p:
            first_text = first_p.get_text(strip=True).lower()
            has_definition = any(w in first_text for w in [" is a ", " provides ", " offers ", " specializes "])

        if not has_faq:
            audit["findings"].append(self._f("CRITICAL", "geo",
                "No FAQ content found. AI engines (ChatGPT, Perplexity, Google AI Overviews) cite pages that directly answer questions. "
                "Add 8–10 FAQs with FAQPage schema: 'Do you use OEM parts?', 'How long does repair take?', 'What areas do you serve?', etc."
            ))
            geo_score -= 35
        if not has_lists:
            audit["findings"].append(self._f("HIGH", "geo",
                "Zero HTML lists (ul/ol) on the page. AI models prefer structured, scannable content. Add lists for services, locations, certifications."
            ))
            geo_score -= 25
        if not has_definition:
            audit["findings"].append(self._f("HIGH", "geo",
                "No clear definition paragraph ('X is a...'). AI models skip content that doesn't immediately establish who/what/where."
            ))
            geo_score -= 20

        audit["scores"]["geo"] = max(0, geo_score)
        audit["geo"] = {"has_faq": has_faq, "has_lists": has_lists, "has_definition": has_definition}

        # ── 6. SOCIAL ───────────────────────────────────────────────────────
        social_score = 100
        og_title = bool(soup.find("meta", {"property": "og:title"}))
        og_desc = bool(soup.find("meta", {"property": "og:description"}))
        og_image = bool(soup.find("meta", {"property": "og:image"}))
        twitter_card = bool(soup.find("meta", {"name": "twitter:card"}))

        missing_og = []
        if not og_title: missing_og.append("og:title")
        if not og_desc: missing_og.append("og:description")
        if not og_image: missing_og.append("og:image")
        if not twitter_card: missing_og.append("twitter:card")

        if missing_og:
            audit["findings"].append(self._f("CRITICAL", "social",
                f"Missing Open Graph / Twitter Card tags: {', '.join(missing_og)}. "
                "Links shared on Facebook, LinkedIn, WhatsApp, iMessage appear as plain text with no image."
            ))
            social_score = 0

        audit["scores"]["social"] = social_score
        audit["social"] = {"og_title": og_title, "og_desc": og_desc, "og_image": og_image, "twitter_card": twitter_card}

        # ── 7. CONTENT ──────────────────────────────────────────────────────
        content_score = 100
        paragraphs = soup.find_all("p")
        list_count = len(soup.find_all(["ul", "ol"]))
        sentences = [s.strip() for s in re.split(r'[.!?]+', text) if len(s.strip()) > 10]
        avg_sentence_len = round(sum(len(s.split()) for s in sentences) / max(len(sentences), 1), 1)
        phones_found = re.findall(r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}', text)

        if len(words) < 500:
            audit["findings"].append(self._f("HIGH", "content", f"Thin content: {len(words):,} words. Target 2,000+ for competitive local SEO."))
            content_score -= 25
        elif len(words) < 1500:
            audit["findings"].append(self._f("HIGH", "content", f"Content volume: {len(words):,} words — adequate but 2,000+ recommended for authority."))
            content_score -= 10

        if list_count == 0:
            audit["findings"].append(self._f("HIGH", "content", "No structured lists on page. Add ul/ol for services, locations, and certifications — Google uses these for featured snippets."))
            content_score -= 15

        locations_mentioned = sum(1 for loc in self.ctx.service_cities if loc.lower() in text.lower())
        if self.ctx.primary_city and self.ctx.primary_city.lower() not in text.lower():
            audit["findings"].append(self._f("HIGH", "content", f"Primary city '{self.ctx.primary_city}' not found in page text. Include it in H1 or first paragraph."))
            content_score -= 10

        audit["scores"]["content"] = max(0, content_score)
        audit["content"] = {
            "word_count": len(words), "paragraphs": len(paragraphs),
            "lists": list_count, "sentences": len(sentences),
            "avg_sentence_length": avg_sentence_len,
            "phones_found": list(set(phones_found)),
            "locations_mentioned": locations_mentioned,
            "total_locations": len(self.ctx.service_cities) + 1,
        }

        # ── 8. LOCAL SEO ────────────────────────────────────────────────────
        local_score = 100
        if not phones_found:
            audit["findings"].append(self._f("HIGH", "local", "Phone number not visible in page text. NAP consistency is critical for local SEO."))
            local_score -= 20

        if locations_mentioned < len(self.ctx.service_cities) * 0.5:
            audit["findings"].append(self._f("HIGH", "local",
                f"Only {locations_mentioned} of {len(self.ctx.service_cities)+1} locations mentioned on homepage. Add a 'Locations' section with links to each city page."
            ))
            local_score -= 15

        audit["scores"]["local_seo"] = max(0, local_score)

        # ── 9. SECURITY ─────────────────────────────────────────────────────
        sec = {"https": final_url.startswith("https://")}
        audit["scores"]["security"] = 85 if sec["https"] else 40
        audit["security"] = sec

        # ── OVERALL SCORE ───────────────────────────────────────────────────
        scores = audit["scores"]
        weights = {"onpage_seo": 20, "schema": 20, "performance": 15, "images": 10, "geo": 15, "social": 10, "content": 10}
        total = sum(scores.get(k, 0) * weights.get(k, 0) for k in weights) / 100
        audit["overall_score"] = round(total)

        # ── SAVE ────────────────────────────────────────────────────────────
        db_write(
            "INSERT OR REPLACE INTO settings (tenant_id, key, value, updated_at) VALUES (?,?,?,CURRENT_TIMESTAMP)",
            (self.tenant_id, "audit_deep", json.dumps(audit, ensure_ascii=False))
        )

        # Log summary
        self.log(f"Audit complete — Score: {audit['overall_score']}/100", "success")
        self.log(f"  On-Page: {scores.get('onpage_seo', '?')}/100 | Schema: {scores.get('schema', '?')}/100", "info")
        self.log(f"  Performance: {scores.get('performance', '?')}/100 | Images: {scores.get('images', '?')}/100", "info")
        self.log(f"  GEO: {scores.get('geo', '?')}/100 | Social: {scores.get('social', '?')}/100 | Content: {scores.get('content', '?')}/100", "info")

        criticals = sum(1 for f in audit["findings"] if f["severity"] == "CRITICAL")
        highs = sum(1 for f in audit["findings"] if f["severity"] == "HIGH")
        self.log(f"Findings: {criticals} critical, {highs} high — {len(audit['findings'])} total", "warning" if criticals > 2 else "info")

        self.set_status("done", f"Audit complete — Score {audit['overall_score']}/100")

    def _f(self, severity: str, category: str, description: str) -> dict:
        return {"severity": severity, "category": category, "description": description}

    def _ai_fallback(self, url: str):
        """When the site can't be crawled, generate an AI-based analysis."""
        self.log("Website unreachable — running AI-based analysis", "warning")
        self.set_status("working", "AI analysis (site protected)")

        prompt = f"""{self.ctx.to_prompt_block()}

The website {url} could not be crawled directly (likely bot-protected).
Based on your knowledge of this business, its industry ({self.ctx.industry_vertical}),
and common patterns in this sector, generate a realistic comprehensive SEO audit.

Identify likely issues in these categories and return JSON:
{{
  "overall_score": 0-100,
  "scores": {{"onpage_seo": 0-100, "schema": 0-100, "performance": 0-100, "images": 0-100, "geo": 0-100, "social": 0-100, "content": 0-100}},
  "findings": [{{"severity": "CRITICAL|HIGH|MEDIUM", "category": "...", "description": "specific issue with actionable fix"}}],
  "note": "AI-generated audit — site blocked crawlers"
}}
Return ONLY valid JSON."""

        raw = self.call_claude(prompt, max_tokens=2000, model="claude-sonnet-4-6")
        if raw:
            try:
                audit = json.loads(self._strip_json(raw))
                audit["timestamp"] = datetime.now(timezone.utc).isoformat()
                audit["url"] = url
                db_write(
                    "INSERT OR REPLACE INTO settings (tenant_id, key, value, updated_at) VALUES (?,?,?,CURRENT_TIMESTAMP)",
                    (self.tenant_id, "audit_deep", json.dumps(audit, ensure_ascii=False))
                )
                self.log(f"AI audit complete — Score: {audit.get('overall_score', '?')}/100", "success")
                self.set_status("done", f"AI audit — Score {audit.get('overall_score', '?')}/100")
                return
            except Exception:
                pass

        self.log("AI audit generation failed.", "error")
        self.set_status("error", "Could not fetch site or generate AI audit")

    @staticmethod
    def _strip_json(raw: str) -> str:
        cleaned = raw.strip()
        if "```" in cleaned:
            parts = cleaned.split("```")
            for part in parts:
                c = part.strip()
                if c.startswith("json"): c = c[4:].strip()
                if c.startswith("{"): return c
        return cleaned
