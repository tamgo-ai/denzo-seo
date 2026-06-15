"""
Video Engine — AI-powered video generation + YouTube publishing.

Generates short-form videos from page content, publishes to YouTube with
full SEO/GEO metadata, and embeds VideoObject schema on the source page.

Workflow:
1. Select top pages (by quality score) that don't have videos yet
2. Generate script + title + description + tags via Claude
3. Generate video via external API (Kling/Runway/HeyGen/Synthesia)
4. Upload to YouTube via YouTube Data API
5. Embed VideoObject schema + iframe on the source page

Per-plan: free=0, trial=1, starter=3/mo, pro=10/mo, agency=30/mo
"""

import json
import os
import time
from datetime import datetime, timezone

from denzo.agents.base_agent import TenantAwareBaseAgent


class VideoEngine(TenantAwareBaseAgent):
    """AI video generator + YouTube publisher."""

    PREREQUISITES = ["Programmatic SEO", "Content Optimizer"]
    MIN_KEYWORDS = 10

    VIDEOS_PER_RUN = 2  # Per execution (runs monthly or on-demand)

    def __init__(self, ctx):
        super().__init__(name="Video Engine", ctx=ctx, layer=5, color="rose")

    def run(self):
        self.log("Video Engine starting...")
        self.set_status("working", "Selecting pages for video generation")

        # ── Check plan limits ──────────────────────────────────────────────
        plan = self._get_plan()
        limits = {"free": 0, "trial": 1, "starter": 3, "pro": 10, "agency": 30}
        monthly_limit = limits.get(plan, 0)
        if monthly_limit == 0:
            self.set_status("done", f"Video generation not available on {plan} plan. Upgrade to starter+.")
            return

        # Check already generated this month
        generated_this_month = self._count_monthly_videos()
        remaining = monthly_limit - generated_this_month
        if remaining <= 0:
            self.set_status("done", f"Monthly video limit reached ({monthly_limit}). Resets next month.")
            return

        # ── Select top pages without videos ─────────────────────────────────
        pages = self._select_pages_for_video(limit=min(remaining, self.VIDEOS_PER_RUN))
        if not pages:
            self.set_status("done", "No pages available for video generation (all have videos or no content)")
            return

        self.log(f"Selected {len(pages)} pages for video generation (plan={plan}, remaining={remaining})")

        videos_generated = 0
        for page in pages:
            if self.should_stop():
                break
            try:
                result = self._produce_video_for_page(page)
                if result:
                    videos_generated += 1
                time.sleep(3)  # Rate limit between API calls
            except Exception as e:
                self.log(f"Video failed for page {page.get('slug', '?')}: {e}", "warning")

        self.set_status("done", f"Generated {videos_generated} videos ({generated_this_month + videos_generated}/{monthly_limit} this month)")

    # ── Page selection ─────────────────────────────────────────────────────

    def _get_plan(self) -> str:
        from denzo.agents.base_agent import db_execute
        rows = db_execute(
            "SELECT plan FROM users u JOIN clients c ON c.owner_user_id = u.id WHERE c.tenant_id=?",
            (self.tenant_id,)
        )
        return rows[0]["plan"] if rows else "free"

    def _count_monthly_videos(self) -> int:
        from denzo.agents.base_agent import db_execute
        rows = db_execute(
            """SELECT COUNT(*) as n FROM activity
               WHERE tenant_id=? AND type='video' AND agent='Video Engine'
               AND created_at >= datetime('now', 'start of month')""",
            (self.tenant_id,)
        )
        return rows[0]["n"] if rows else 0

    def _select_pages_for_video(self, limit: int = 2) -> list[dict]:
        from denzo.agents.base_agent import db_execute
        rows = db_execute(
            """SELECT id, title, slug, type, target_keyword, content, meta_description, publish_url
               FROM pages
               WHERE tenant_id=? AND status='published' AND content IS NOT NULL AND content != ''
               AND (notes IS NULL OR notes NOT LIKE '%has_video%')
               ORDER BY quality_score DESC, created_at DESC
               LIMIT ?""",
            (self.tenant_id, limit)
        )
        return [dict(r) for r in (rows or [])]

    # ── Video production pipeline ──────────────────────────────────────────

    def _produce_video_for_page(self, page: dict) -> bool:
        """Full pipeline: script → video → YouTube upload → schema embed."""
        slug = page.get("slug", "page")
        title = page.get("title", "Video")
        content = page.get("content", "")
        keyword = page.get("target_keyword", title)

        self.log(f"Producing video for: {title[:60]}")

        # Step 1: Generate optimized script + metadata
        metadata = self._generate_video_metadata(title, content, keyword, slug)

        # Step 2: Generate video via external API
        video_url = self._generate_video(metadata["script"], title, slug)

        # Step 3: Upload to YouTube (if OAuth configured)
        youtube_url = None
        if video_url or True:  # Proceed even without video gen API
            youtube_url = self._upload_to_youtube(metadata, video_url, page)

        # Step 4: Embed on source page
        if youtube_url:
            self._embed_video_on_page(page, youtube_url, metadata)

        # Log activity
        from denzo.agents.base_agent import db_write
        db_write(
            "INSERT INTO activity (tenant_id, type, message, agent, level, created_at) VALUES (?,?,?,?,?,datetime('now'))",
            (self.tenant_id, "video", f"Video: {title[:80]} → {youtube_url or 'skipped'}", "Video Engine", "success")
        )

        return bool(youtube_url)

    def _generate_video_metadata(self, title: str, content: str, keyword: str, slug: str) -> dict:
        """Use Claude to generate a 60-90s video script optimized for YouTube and AI citation."""
        name = self.ctx.client_name or "our business"
        city = self.ctx.primary_city or ""
        services = ", ".join(self.ctx.services[:3]) if self.ctx.services else "service"

        prompt = f"""Generate YouTube-optimized video metadata for a business.

BUSINESS: {name} in {city}
SERVICES: {services}
PAGE TITLE: {title}
TARGET KEYWORD: {keyword}

Return ONLY valid JSON:
{{
  "title": "YouTube title (max 70 chars, include keyword, compelling)",
  "script": "60-90 second video script. Natural, conversational. Include hook in first 5s, value in middle, CTA at end.",
  "description": "YouTube description (200-300 chars, include keyword, link to website, services, city)",
  "tags": ["tag1", "tag2", ...],  // 10-15 relevant YouTube tags
  "thumbnail_text": "Short overlay text for thumbnail"
}}"""

        try:
            result = self.call_claude(prompt, max_tokens=800)
            data = json.loads(self.strip_json_fences(result)) if hasattr(self, 'strip_json_fences') else json.loads(result)
            return data
        except Exception:
            # Fallback metadata
            return {
                "title": f"{title} | {name} {city}",
                "script": f"Welcome! Today we're talking about {keyword}. At {name} in {city}, we specialize in {services}. We've helped hundreds of customers with top-quality service. Ready to learn more? Visit our website or call us today!",
                "description": f"{title}. {name} in {city} explains {keyword}. {services}. Visit us at {self.ctx.website_url or self.ctx.domain}",
                "tags": [keyword, city, name, services.split(",")[0].strip() if services else "service", f"{city} business", "how to", "tips", "local"],
                "thumbnail_text": f"{name} | {city}",
            }

    def _generate_video(self, script: str, title: str, slug: str) -> str | None:
        """Generate video via external API. Supports Kling, Runway, HeyGen, Synthesia.

        Priority: Kling (best AI-generated), HeyGen (avatar), Runway (creative).
        Set VIDEO_API_PROVIDER in .env: kling | heygen | runway | synthesia
        """
        provider = os.getenv("VIDEO_API_PROVIDER", "").strip().lower()

        if provider == "kling":
            return self._gen_kling(script, title)
        elif provider == "heygen":
            return self._gen_heygen(script, title)
        elif provider == "runway":
            return self._gen_runway(script, title)
        elif provider == "synthesia":
            return self._gen_synthesia(script)
        else:
            self.log("No VIDEO_API_PROVIDER configured. Skipping video generation (metadata + YouTube still proceed).", "info")
            return None

    def _gen_kling(self, script: str, title: str) -> str | None:
        """Kling AI video generation."""
        key = os.getenv("KLING_API_KEY", "").strip()
        if not key:
            self.log("KLING_API_KEY not configured", "warning")
            return None
        import requests
        r = requests.post(
            "https://api.klingai.com/v1/videos/text2video",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"prompt": script[:500], "duration": "5", "mode": "std"},
            timeout=60
        )
        if r.status_code == 200:
            return r.json().get("data", {}).get("video_url")
        self.log(f"Kling error: {r.status_code}", "warning")
        return None

    def _gen_heygen(self, script: str, title: str) -> str | None:
        """HeyGen avatar video generation."""
        key = os.getenv("HEYGEN_API_KEY", "").strip()
        if not key:
            return None
        import requests
        r = requests.post(
            "https://api.heygen.com/v2/video/generate",
            headers={"X-Api-Key": key, "Content-Type": "application/json"},
            json={
                "video_name": title[:80],
                "dimension": {"width": 1920, "height": 1080},
                "avatar": {"avatar_id": "default", "avatar_style": "normal"},
                "voice": {"voice_id": "default"},
                "script": {"type": "text", "input": script[:2000]}
            },
            timeout=60
        )
        if r.status_code == 200:
            return r.json().get("data", {}).get("video_url")
        return None

    def _gen_runway(self, script: str, title: str) -> str | None:
        """Runway Gen-3 video generation."""
        key = os.getenv("RUNWAY_API_KEY", "").strip()
        if not key:
            return None
        import requests
        r = requests.post(
            "https://api.runwayml.com/v1/generate",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"prompt": script[:500], "seconds": 5},
            timeout=60
        )
        return r.json().get("output_url") if r.status_code == 200 else None

    def _gen_synthesia(self, script: str) -> str | None:
        """Synthesia AI avatar video generation."""
        key = os.getenv("SYNTHESIA_API_KEY", "").strip()
        if not key:
            return None
        import requests
        r = requests.post(
            "https://api.synthesia.io/v2/videos",
            headers={"Authorization": key, "Content-Type": "application/json"},
            json={"scriptText": script[:2000], "avatar": "anna_costume1_cameraA", "voice": "en-US-1"},
            timeout=60
        )
        return r.json().get("download") if r.status_code in (200, 201) else None

    # ── YouTube upload ─────────────────────────────────────────────────────

    def _upload_to_youtube(self, metadata: dict, video_url: str | None, page: dict) -> str | None:
        """Upload video to YouTube via YouTube Data API v3.

        Requires OAuth scope: https://www.googleapis.com/auth/youtube.upload
        Set DENZO_YOUTUBE_CHANNEL_ID in settings per tenant.
        """
        try:
            from denzo.agents.utils.google_oauth import get_access_token
            token = get_access_token(self.tenant_id, "youtube")
        except Exception:
            self.log("YouTube OAuth not connected. Add youtube.upload scope in Google Cloud Console.", "info")
            return None

        if not video_url:
            # No video to upload, but we can still create metadata-only listing
            self.log("No video URL — YouTube requires a video file. Skipping upload.", "warning")
            return None

        import requests

        # Step 1: Create video resource (metadata)
        snippet = {
            "title": metadata["title"][:100],
            "description": metadata.get("description", "")[:5000],
            "tags": metadata.get("tags", [])[:20],
        }
        body = {
            "snippet": snippet,
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
        }

        try:
            # Create the video entry
            r = requests.post(
                "https://www.googleapis.com/upload/youtube/v3/videos?part=snippet,status",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=body,
                timeout=15
            )
            if r.status_code == 200:
                video_data = r.json()
                video_id = video_data.get("id", "")
                youtube_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else None
                self.log(f"YouTube: {metadata['title'][:50]} → {youtube_url or 'created'}")
                return youtube_url
            else:
                self.log(f"YouTube API error: {r.status_code} {r.text[:100]}", "warning")
        except Exception as e:
            self.log(f"YouTube upload error: {e}", "warning")

        return None

    # ── Schema embedding ────────────────────────────────────────────────────

    def _embed_video_on_page(self, page: dict, youtube_url: str, metadata: dict):
        """Embed VideoObject schema + iframe on the source page, then re-publish."""
        from denzo.agents.base_agent import db_write, db_execute

        video_schema = {
            "@context": "https://schema.org",
            "@type": "VideoObject",
            "name": metadata.get("title", page.get("title", "")),
            "description": metadata.get("description", "")[:200],
            "thumbnailUrl": f"https://img.youtube.com/vi/{youtube_url.split('v=')[-1]}/maxresdefault.jpg" if "youtube.com" in youtube_url else "",
            "contentUrl": youtube_url,
            "embedUrl": youtube_url.replace("watch?v=", "embed/") if "youtube.com" in youtube_url else youtube_url,
            "uploadDate": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        }

        video_embed = f"""
<!-- DENZO Video Engine -->
<div class="video-embed" style="margin:2rem 0">
  <iframe width="100%" height="480" src="{youtube_url.replace('watch?v=', 'embed/') if 'youtube.com' in youtube_url else youtube_url}"
          frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
          allowfullscreen loading="lazy"></iframe>
</div>
"""

        # Append video embed to page content and mark as having video
        current_content = page.get("content", "") or ""
        new_content = current_content + video_embed

        current_schema = ""
        schema_rows = db_execute(
            "SELECT schema_markup FROM pages WHERE id=? AND tenant_id=?",
            (page["id"], self.tenant_id)
        )
        if schema_rows and schema_rows[0]["schema_markup"]:
            current_schema = schema_rows[0]["schema_markup"]

        new_schema = current_schema
        if "VideoObject" not in current_schema:
            new_schema = current_schema + "\n" + json.dumps(video_schema, ensure_ascii=False)

        db_write(
            """UPDATE pages SET content=?, schema_markup=?,
               notes=COALESCE(notes,'')||' [has_video]',
               status='ready', updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND tenant_id=?""",
            (new_content, new_schema, page["id"], self.tenant_id)
        )

        self.log(f"VideoObject schema embedded on page {page.get('slug', '?')}")
