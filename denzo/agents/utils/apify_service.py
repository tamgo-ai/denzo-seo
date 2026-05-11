"""
ApifyService — centralized Apify actor execution for DENZO SEO.

Design principles:
  - Single entry point: every agent calls this, never touches apify-client directly.
  - Graceful fallback: if key not set or actor fails → returns None/empty, caller handles.
  - Cost-aware: logs estimated cost per call so operators know what's happening.
  - Tenant-aware: reads API key from settings (global) so it's configured once.

Actor registry:
  serp        — apify/google-search-scraper        ($1.80 / 1K results)
  maps        — compass/crawler-google-places        ($2.10 / 1K places)
  reviews     — compass/google-maps-reviews-scraper  ($0.30 / 1K reviews)
  news        — andok/google-news-scraper            ($1.00 / 1K articles)
  seo_audit   — automation-lab/seo-audit-tool        ($0.035 + $0.01/URL)
"""
from __future__ import annotations
import json
import time
from typing import Callable, Optional


# ── Actor registry ────────────────────────────────────────────────────────────

ACTOR_IDS = {
    "serp":      "apify/google-search-scraper",
    "maps":      "compass/crawler-google-places",
    "reviews":   "compass/google-maps-reviews-scraper",
    "news":      "andok/google-news-scraper",
    "seo_audit": "automation-lab/seo-audit-tool",
}

# Estimated cost per item (USD)
COST_PER_ITEM = {
    "serp":      0.0018,
    "maps":      0.0021,
    "reviews":   0.0003,
    "news":      0.001,
    "seo_audit": 0.01,
}


# ── Key loader ────────────────────────────────────────────────────────────────

def _load_apify_key() -> str:
    """Load Apify API key from global settings table."""
    try:
        from denzo.agents.base_agent import db_execute
        rows = db_execute(
            "SELECT value FROM settings WHERE tenant_id='__global__' AND key='apify_api_key'", ()
        )
        return rows[0]["value"] if rows else ""
    except Exception:
        return ""


# ── Main service class ────────────────────────────────────────────────────────

class ApifyService:
    """
    Thin wrapper around the Apify Python SDK.
    Each agent instantiates this and checks .available() before calling anything.
    """

    def __init__(self, log_fn: Callable = None):
        self._log  = log_fn or (lambda msg, level="info": None)
        self._key  = _load_apify_key()

    def available(self) -> bool:
        """True if an Apify API key is configured."""
        return bool(self._key)

    # ── Raw actor execution ───────────────────────────────────────────────────

    def run_actor(self, actor_key: str, run_input: dict,
                  timeout_secs: int = 180, max_items: int = 100) -> list:
        """
        Execute an Apify actor and return the dataset items.
        Returns [] on any failure — callers must handle empty lists gracefully.
        """
        if not self._key:
            return []

        actor_id = ACTOR_IDS.get(actor_key)
        if not actor_id:
            self._log(f"[Apify] Unknown actor key: {actor_key}", "warning")
            return []

        try:
            from apify_client import ApifyClient
            client = ApifyClient(self._key)

            self._log(f"[Apify] Running {actor_id}...", "info")
            t0 = time.time()

            run = client.actor(actor_id).call(
                run_input=run_input,
                timeout_secs=timeout_secs,
                memory_mbytes=256,
            )

            if not run:
                self._log(f"[Apify] {actor_id} returned no run object", "warning")
                return []

            items = list(
                client.dataset(run["defaultDatasetId"]).iterate_items()
            )[:max_items]

            elapsed  = round(time.time() - t0, 1)
            est_cost = round(len(items) * COST_PER_ITEM.get(actor_key, 0.001), 4)
            self._log(
                f"[Apify] {actor_id} → {len(items)} items in {elapsed}s (~${est_cost} est.)",
                "success"
            )
            return items

        except ImportError:
            self._log("[Apify] apify-client not installed — run: pip install apify-client", "error")
            return []
        except Exception as e:
            self._log(f"[Apify] {actor_id} error: {str(e)[:120]}", "warning")
            return []

    # ── High-level domain methods ─────────────────────────────────────────────

    def check_serp_rankings(self, keywords: list[str], domain: str,
                             location: str = "United States",
                             country_code: str = "us") -> list[dict]:
        """
        Check real SERP positions for a list of keywords.

        Returns list of:
        {keyword, position (1-100 or None), url_found, title, serp_features}
        """
        if not keywords or not self._key:
            return []

        # Run one query per keyword group (up to 20 at once)
        batched = [keywords[i:i+20] for i in range(0, len(keywords), 20)]
        all_results = []

        for batch in batched:
            items = self.run_actor("serp", {
                "queries":           "\n".join(batch),
                "resultsPerPage":    100,
                "maxPagesPerQuery":  1,
                "countryCode":       country_code.lower(),
                "languageCode":      "en",
                "mobileResults":     False,
            }, timeout_secs=120)

            for item in items:
                kw        = item.get("searchQuery", {}).get("term", "")
                organics  = item.get("organicResults", [])
                position  = None
                found_url = ""
                title     = ""

                domain_clean = domain.replace("https://", "").replace("http://", "").replace("www.", "").split("/")[0]

                for idx, r in enumerate(organics, 1):
                    r_url = (r.get("url") or r.get("link") or "").lower()
                    if domain_clean.lower() in r_url:
                        position  = idx
                        found_url = r.get("url") or r.get("link") or ""
                        title     = r.get("title") or ""
                        break

                # Detect SERP features
                serp_features = []
                if item.get("peopleAlsoAsk"):
                    serp_features.append("People Also Ask")
                if item.get("relatedSearches"):
                    serp_features.append("Related Searches")
                if item.get("topStories"):
                    serp_features.append("Top Stories")
                if item.get("localPack"):
                    serp_features.append("Local Pack")
                featured = item.get("featuredSnippet") or item.get("answerBox")
                if featured:
                    serp_features.append("Featured Snippet")

                all_results.append({
                    "keyword":       kw,
                    "position":      position,
                    "url_found":     found_url,
                    "title":         title,
                    "serp_features": serp_features,
                    "total_results": item.get("numberOfResults"),
                    "paa_questions": [p.get("question", "") for p in (item.get("peopleAlsoAsk") or [])[:5]],
                    "source":        "apify_real",
                })

        return all_results

    def find_local_businesses(self, search_queries: list[str],
                               max_per_query: int = 20,
                               location_query: str | None = None,
                               timeout_secs: int = 180) -> list[dict]:
        """
        Find local businesses via Google Maps.
        Returns list of {name, url, address, city, phone, rating, reviews_count, place_id}.

        location_query: optional "City, State" or "City, Country" string passed
        to the Apify actor as `locationQuery`. This tells Google Maps to focus
        the geolocation on that specific city instead of crawling the whole
        country (USA-wide search takes 3+ minutes and returns 3 noisy results;
        city-scoped search takes ~30s and returns 15-20 relevant ones).
        """
        if not search_queries or not self._key:
            return []

        actor_input = {
            "searchStringsArray":          search_queries,
            "maxCrawledPlacesPerSearch":   max_per_query,
            "language":                    "en",
            "countryCode":                 "us",
            "exportPlaceUrls":             False,
        }
        if location_query:
            actor_input["locationQuery"] = location_query

        items = self.run_actor("maps", actor_input,
            timeout_secs=timeout_secs,
            max_items=max_per_query * len(search_queries))

        results = []
        for item in items:
            # Skip places without a name
            name = item.get("title") or item.get("name") or ""
            if not name:
                continue

            address = item.get("address") or item.get("street") or ""
            city    = item.get("city") or ""
            if not city and address:
                # Try to extract city from address string
                parts = [p.strip() for p in address.split(",")]
                if len(parts) >= 2:
                    city = parts[-2].strip()

            loc = item.get("location") or {}
            results.append({
                "name":          name,
                "url":           item.get("website") or item.get("url") or "",
                "address":       address,
                "city":          city,
                "phone":         item.get("phone") or item.get("phoneUnformatted") or "",
                "rating":        item.get("totalScore") or item.get("rating"),
                "reviews_count": item.get("reviewsCount") or item.get("reviewCount") or 0,
                "place_id":      item.get("placeId") or "",
                "categories":    item.get("categoryName") or item.get("categories") or "",
                "lat":           loc.get("lat") if isinstance(loc, dict) else None,
                "lng":           loc.get("lng") if isinstance(loc, dict) else None,
                "source":        "apify_maps",
            })

        return results

    def get_reviews(self, place_urls: list[str],
                    max_reviews_per_place: int = 50) -> list[dict]:
        """
        Scrape Google Maps reviews for a list of place URLs.
        Returns flat list of review dicts, each with {place_name, text, rating, date}
        """
        if not place_urls or not self._key:
            return []

        items = self.run_actor("reviews", {
            "startUrls":       [{"url": u} for u in place_urls[:10]],
            "maxReviews":      max_reviews_per_place,
            "reviewsSort":     "newest",
            "language":        "en",
        }, timeout_secs=180, max_items=max_reviews_per_place * len(place_urls))

        results = []
        for item in items:
            results.append({
                "place_name": item.get("title") or item.get("name") or "",
                "text":       item.get("text") or item.get("reviewBody") or "",
                "rating":     item.get("stars") or item.get("rating") or 0,
                "date":       item.get("publishedAtDate") or item.get("date") or "",
                "likes":      item.get("likesCount") or 0,
                "source":     "apify_reviews",
            })

        return results

    def get_news(self, query: str, max_items: int = 20) -> list[dict]:
        """
        Fetch recent news articles for a query.
        Returns list of {title, description, url, date, source}
        """
        if not query or not self._key:
            return []

        items = self.run_actor("news", {
            "keyword":  query,   # andok/google-news-scraper uses 'keyword', not 'query'
            "maxItems": max_items,
        }, timeout_secs=60, max_items=max_items)

        results = []
        for item in items:
            results.append({
                "title":       item.get("title") or "",
                "description": item.get("description") or item.get("snippet") or "",
                "url":         item.get("url") or item.get("link") or "",
                "date":        item.get("publishedAt") or item.get("date") or "",
                "source":      item.get("source") or item.get("publisher") or "",
            })

        return results

    def audit_url(self, url: str) -> Optional[dict]:
        """
        Run a technical SEO audit on a URL.
        Returns {score, issues, categories} or None on failure.
        """
        if not url or not self._key:
            return None

        items = self.run_actor("seo_audit", {
            "urls":      [url],  # automation-lab/seo-audit-tool expects 'urls' array
            "fullAudit": True,
        }, timeout_secs=90, max_items=1)

        if not items:
            return None

        item = items[0]
        return {
            "score":      item.get("score") or item.get("totalScore"),
            "grade":      item.get("grade"),
            "issues":     item.get("issues") or [],
            "categories": item.get("categories") or {},
            "url":        url,
            "source":     "apify_seo_audit",
        }
