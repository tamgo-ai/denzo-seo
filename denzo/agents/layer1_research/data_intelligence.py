"""
Data Intelligence — Layer 1
Finds original data (news, Reddit trends, competitor reviews, industry stats)
and packages it as citation bait for content agents.
"""
import json
import time
import urllib.parse
from denzo.agents.base_agent import TenantAwareBaseAgent, ClientContext, db_execute, db_write, strip_json_fences


class DataIntelligence(TenantAwareBaseAgent):

    def __init__(self, ctx: ClientContext):
        super().__init__("Data Intelligence", ctx, layer=1, color="indigo")

    # ── Source scrapers ───────────────────────────────────────────────────────

    def _fetch_news(self, query: str, api_key: str) -> list:
        """NewsAPI free tier — returns list of article dicts."""
        import requests
        try:
            url = (
                f"https://newsapi.org/v2/everything"
                f"?q={urllib.parse.quote(query)}&language=en"
                f"&sortBy=publishedAt&pageSize=10&apiKey={api_key}"
            )
            resp = requests.get(url, timeout=12, headers={"User-Agent": "DenzoSEO/1.0"})
            resp.raise_for_status()
            data = resp.json()
            articles = data.get("articles", [])
            return [
                {
                    "title": a.get("title", ""),
                    "description": a.get("description", ""),
                    "url": a.get("url", ""),
                    "publishedAt": a.get("publishedAt", ""),
                    "source": a.get("source", {}).get("name", ""),
                }
                for a in articles if a.get("title")
            ]
        except Exception as e:
            self.log(f"NewsAPI error: {str(e)[:80]}", "warning")
            return []

    def _fetch_reddit(self, query: str) -> list:
        """Reddit search — no API key needed. Returns top posts."""
        import requests
        try:
            url = (
                f"https://www.reddit.com/search.json"
                f"?q={urllib.parse.quote(query)}&sort=top&t=month&limit=10"
            )
            headers = {"User-Agent": "Mozilla/5.0 (compatible; DenzoSEO/1.0)"}
            resp = requests.get(url, timeout=12, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            posts = data.get("data", {}).get("children", [])
            results = []
            for p in posts:
                d = p.get("data", {})
                results.append({
                    "title": d.get("title", ""),
                    "score": d.get("score", 0),
                    "subreddit": d.get("subreddit", ""),
                    "selftext": (d.get("selftext", "") or "")[:400],
                    "url": f"https://reddit.com{d.get('permalink', '')}",
                    "num_comments": d.get("num_comments", 0),
                })
            return results
        except Exception as e:
            self.log(f"Reddit scrape error: {str(e)[:80]}", "warning")
            return []

    def _fetch_google_trends(self) -> list:
        """Google Trends — try RSS feed, fall back gracefully."""
        import requests
        # Try multiple known endpoints
        urls = [
            "https://trends.google.com/trending/rss?geo=US",
            "https://trends.google.com/trends/trendingsearches/daily/rss?geo=US&hl=en-US",
        ]
        for url in urls:
            try:
                headers = {"User-Agent": "Mozilla/5.0 (compatible; DenzoSEO/1.0)"}
                resp = requests.get(url, timeout=10, headers=headers)
                resp.raise_for_status()
                import xml.etree.ElementTree as ET
                root = ET.fromstring(resp.content)
                trends = []
                for item in root.iter("item"):
                    title_el = item.find("title")
                    if title_el is not None and title_el.text:
                        trends.append(title_el.text.strip())
                if trends:
                    return trends[:20]
            except Exception:
                continue
        self.log("Google Trends unavailable — skipping", "warning")
        return []

    def _fetch_duckduckgo_stats(self, query: str) -> list:
        """DuckDuckGo Lite search for government/industry statistics."""
        import requests
        from html.parser import HTMLParser

        class _LinkParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.results = []
                self._current = {}
                self._in_result = False

            def handle_starttag(self, tag, attrs):
                attrs_dict = dict(attrs)
                if tag == "a" and attrs_dict.get("href", "").startswith("//duckduckgo.com/l"):
                    self._in_result = True
                    # Extract actual URL from redirect
                    href = attrs_dict.get("href", "")
                    if "uddg=" in href:
                        import urllib.parse as up
                        qs = up.parse_qs(up.urlparse("https:" + href).query)
                        self._current["url"] = up.unquote(qs.get("uddg", [""])[0])

            def handle_data(self, data):
                if self._in_result and data.strip():
                    self._current["text"] = data.strip()
                    self.results.append(dict(self._current))
                    self._current = {}
                    self._in_result = False

        try:
            full_query = f"{query} statistics site:gov OR site:bls.gov OR site:census.gov"
            url = f"https://lite.duckduckgo.com/lite/?q={urllib.parse.quote(full_query)}&kl=us-en"
            headers = {"User-Agent": "Mozilla/5.0 (compatible; DenzoSEO/1.0)"}
            resp = requests.get(url, timeout=15, headers=headers)
            resp.raise_for_status()
            parser = _LinkParser()
            parser.feed(resp.text)
            return parser.results[:10]
        except Exception as e:
            self.log(f"DuckDuckGo stats error: {str(e)[:80]}", "warning")
            return []

    # ── AI synthesis ─────────────────────────────────────────────────────────

    def _synthesize(self, raw_data: dict) -> dict:
        """Send raw data to AI to extract insights."""
        ctx = self.ctx

        news_titles = [a["title"] for a in raw_data.get("news", [])[:8]]
        reddit_posts = [
            f"[{p['score']} upvotes] {p['title']}"
            for p in raw_data.get("reddit", [])[:8]
        ]
        trends = raw_data.get("trends", [])[:10]
        stats_links = [r.get("text", "") for r in raw_data.get("stats", [])[:8]]

        prompt = f"""You are a content strategist analyzing industry data for {ctx.client_name},
a {ctx.industry_vertical} business based in {ctx.primary_city}, {ctx.state}.

RAW DATA COLLECTED:

INDUSTRY NEWS (recent headlines):
{chr(10).join(f"- {t}" for t in news_titles) if news_titles else "No news data available."}

REDDIT TOP POSTS (what real customers discuss):
{chr(10).join(f"- {p}" for p in reddit_posts) if reddit_posts else "No Reddit data available."}

GOOGLE TRENDS:
{chr(10).join(f"- {t}" for t in trends) if trends else "No trends data."}

STATISTICS SOURCES FOUND:
{chr(10).join(f"- {s}" for s in stats_links) if stats_links else "No stats sources found."}

TASK:
Analyze this data and produce a content intelligence report. Return ONLY valid JSON:

{{
  "data_stories": [
    {{
      "headline": "Compelling data-driven insight title",
      "finding": "The specific data point or trend",
      "source_type": "news|reddit|trends|stats",
      "content_angle": "How to use this in a blog post"
    }}
  ],
  "pain_points": [
    "Pain point 1 from Reddit/news",
    "Pain point 2",
    "Pain point 3",
    "Pain point 4",
    "Pain point 5"
  ],
  "citation_bait_paragraphs": [
    "A complete paragraph with specific data, percentages, and authoritative language that AI systems will cite. Reference the industry and location. Include a named framework or methodology.",
    "A second paragraph focusing on a different data angle.",
    "A third paragraph with a surprising statistic or counterintuitive finding."
  ],
  "suggested_titles": [
    "Data-driven blog post title 1",
    "Data-driven blog post title 2",
    "Data-driven blog post title 3",
    "Data-driven blog post title 4",
    "Data-driven blog post title 5"
  ]
}}

Return ONLY valid JSON. No explanation outside the JSON."""

        raw = self.call_claude(prompt, max_tokens=2500, model="claude-sonnet-4-6")
        if not raw:
            self.log("AI returned empty response during synthesis", "warning")
            return {}
        try:
            return json.loads(strip_json_fences(raw))
        except Exception as e:
            self.log(f"JSON parse error in synthesis: {str(e)[:120]}", "warning")
            self.log(f"Raw response preview: {raw[:300]}", "warning")
            return {}

    # ── Main run ──────────────────────────────────────────────────────────────

    def run(self):
        self.log("Starting Data Intelligence scan...")
        self.set_status("working", "Collecting industry data")

        ctx = self.ctx

        # Build human-readable search terms (never use raw industry_vertical codes)
        services = ctx.services[:2] if ctx.services else []
        location = ctx.primary_city or ""
        client_name = ctx.client_name

        # Primary search: use first service or client name + location
        primary_query = f"{services[0]} {location}".strip() if services else f"{client_name} {location}".strip()
        # Broad query: use all services or client name
        broad_query = " ".join(services) if services else client_name

        self.log(f"Search focus: '{primary_query}'")

        raw_data: dict = {
            "news": [],
            "reddit": [],
            "trends": [],
            "stats": [],
        }

        # 1. News — Apify Google News first, fallback to NewsAPI
        from denzo.agents.utils.apify_service import ApifyService
        apify = ApifyService(log_fn=lambda m, l="info": self.log(m, l))

        if apify.available():
            self.log(f"[APIFY REAL] Fetching news: '{primary_query}'")
            self.set_status("working", "Fetching news via Apify Google News")
            apify_news = apify.get_news(primary_query, max_items=15)
            if apify_news:
                # Normalize to same format as NewsAPI
                raw_data["news"] = [
                    {
                        "title":       a.get("title", ""),
                        "description": a.get("description", ""),
                        "url":         a.get("url", ""),
                        "publishedAt": a.get("date", ""),
                        "source":      a.get("source", ""),
                    }
                    for a in apify_news
                ]
                self.log(f"[APIFY REAL] News: {len(raw_data['news'])} articles found.")
            else:
                self.log("Apify news returned no results — trying NewsAPI fallback", "warning")

        if not raw_data["news"]:
            newsapi_key_row = db_execute(
                "SELECT value FROM settings WHERE tenant_id='__global__' AND key='newsapi_key'", ()
            )
            newsapi_key = newsapi_key_row[0]["value"] if newsapi_key_row else ""
            if newsapi_key:
                self.log(f"Fetching news via NewsAPI: '{primary_query}'")
                self.set_status("working", "Fetching industry news (NewsAPI)")
                raw_data["news"] = self._fetch_news(primary_query, newsapi_key)
                self.log(f"NewsAPI: {len(raw_data['news'])} articles found.")
            else:
                self.log("No news source configured (set apify_api_key or newsapi_key in Settings)", "warning")

        if self.should_stop():
            self.set_status("idle", "Stopped")
            return

        # 2. Reddit — search by customer problem language, not industry code
        reddit_query = f"{broad_query} problems issues complaints"
        self.log(f"Scanning Reddit: '{reddit_query}'")
        self.set_status("working", "Scanning Reddit discussions")
        raw_data["reddit"] = self._fetch_reddit(reddit_query)
        # If few results, try broader
        if len(raw_data["reddit"]) < 3:
            raw_data["reddit"] += self._fetch_reddit(primary_query)
        self.log(f"Reddit: {len(raw_data['reddit'])} posts found.")
        time.sleep(1)

        if self.should_stop():
            self.set_status("idle", "Stopped")
            return

        # 3. Google Trends — try multiple endpoints, fail gracefully
        self.log("Checking Google Trends")
        self.set_status("working", "Checking Google Trends")
        raw_data["trends"] = self._fetch_google_trends()
        self.log(f"Trends: {len(raw_data['trends'])} trending topics found.")

        if self.should_stop():
            self.set_status("idle", "Stopped")
            return

        # 4. Industry statistics via DuckDuckGo
        self.log(f"Mining industry statistics: '{broad_query}'")
        self.set_status("working", "Mining industry statistics")
        raw_data["stats"] = self._fetch_duckduckgo_stats(broad_query)
        self.log(f"Stats sources: {len(raw_data['stats'])} found.")

        if self.should_stop():
            self.set_status("idle", "Stopped")
            return

        # 5. Synthesize with AI
        total_raw = sum(len(v) for v in raw_data.values())
        self.log(f"Synthesizing {total_raw} data points with AI...")
        self.set_status("working", "AI synthesis of data stories")
        report = self._synthesize(raw_data)

        if not report:
            self.log("AI synthesis failed — saving empty placeholder report", "warning")
            report = {
                "data_stories": [],
                "pain_points": [],
                "citation_bait_paragraphs": [],
                "suggested_titles": [],
            }

        # Always save, even if partial
        db_write(
            "INSERT OR REPLACE INTO settings (tenant_id, key, value, updated_at) "
            "VALUES (?, 'data_intelligence_report', ?, CURRENT_TIMESTAMP)",
            (self.tenant_id, json.dumps(report))
        )

        data_stories = report.get("data_stories", [])
        pain_count   = len(report.get("pain_points", []))
        bait_count   = len(report.get("citation_bait_paragraphs", []))
        title_count  = len(report.get("suggested_titles", []))

        for story in data_stories[:3]:
            headline = story.get("headline", story) if isinstance(story, dict) else str(story)
            self.log(f"Story: {headline}", "success")

        for pp in report.get("pain_points", [])[:3]:
            self.log(f"Pain point: {pp}", "info")

        self.log(
            f"Intelligence complete: {len(data_stories)} data stories, "
            f"{pain_count} pain points, {bait_count} citation paragraphs, "
            f"{title_count} title ideas.",
            "success"
        )
        self.log("Report saved → view at /clients/{}/data-intel".format(self.tenant_id), "info")
        self.set_status("done", f"{len(data_stories)} stories · {pain_count} pain points · {bait_count} citation paragraphs")
