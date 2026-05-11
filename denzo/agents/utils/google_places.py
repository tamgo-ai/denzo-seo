"""
Google Places API (New) v1 client — for local competitor discovery.

Why this over Apify:
  - 200-500ms per call vs 60-120s of Apify Maps scraping
  - Official Google data, not scraping (no breakage, no cache misses)
  - locationBias.circle gives precise geographic control via lat/lng + radius
  - Returns lat/lng, rating, userRatingCount, types directly in one shot
  - $32 per 1000 calls (~$0.032) — comparable cost, 100x faster

Docs: https://developers.google.com/maps/documentation/places/web-service/text-search
"""
import os
import json
import logging
import urllib.request
import urllib.parse
import urllib.error

logger = logging.getLogger(__name__)

BASE_URL = "https://places.googleapis.com/v1/places:searchText"

# Default field mask — controls cost (more fields = higher tier per Google docs).
# This mask hits the "Pro" SKU which includes ratings/reviews_count — what we
# need for SEO weighting. ~$0.032 per call.
DEFAULT_FIELDS = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.shortFormattedAddress",
    "places.location",
    "places.types",
    "places.primaryType",
    "places.primaryTypeDisplayName",
    "places.nationalPhoneNumber",
    "places.websiteUri",
    "places.rating",
    "places.userRatingCount",
    "places.businessStatus",
    "places.googleMapsUri",
])


class PlacesError(Exception):
    pass


def is_configured() -> bool:
    return bool(os.getenv("GOOGLE_PLACES_API_KEY"))


def search_text(
    *, text_query: str,
    location_bias: dict | None = None,
    page_size: int = 20,
    language_code: str = "en",
    field_mask: str = DEFAULT_FIELDS,
    timeout_secs: int = 15,
) -> list[dict]:
    """Call Places API searchText. Returns normalized list of places.

    location_bias example:
        {"circle": {"center": {"latitude": 33.56, "longitude": -117.21},
                    "radius": 30000}}  # 30km

    Each returned dict has: id, name, address, lat, lng, rating, reviews_count,
    primary_type, website, phone, maps_url.
    """
    api_key = os.getenv("GOOGLE_PLACES_API_KEY")
    if not api_key:
        raise PlacesError("GOOGLE_PLACES_API_KEY not set")

    body = {
        "textQuery": text_query,
        "pageSize": max(1, min(20, page_size)),
        "languageCode": language_code,
    }
    if location_bias:
        body["locationBias"] = location_bias

    req = urllib.request.Request(
        BASE_URL,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": field_mask,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_secs) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise PlacesError(f"Places API {e.code}: {err_body[:200]}")
    except Exception as e:
        raise PlacesError(f"Places API call failed: {e}")

    places = payload.get("places", []) or []
    return [_normalize(p) for p in places]


def search_nearby(
    *, query: str, lat: float, lng: float, radius_m: int = 20000,
    page_size: int = 15, language_code: str = "en",
    timeout_secs: int = 15,
) -> list[dict]:
    """Convenience wrapper: text search biased to a point + radius."""
    bias = {"circle": {
        "center": {"latitude": lat, "longitude": lng},
        "radius": radius_m,
    }}
    return search_text(
        text_query=query, location_bias=bias,
        page_size=page_size, language_code=language_code,
        timeout_secs=timeout_secs,
    )


def _normalize(p: dict) -> dict:
    loc = p.get("location") or {}
    name = (p.get("displayName") or {}).get("text", "")
    primary_type_dn = (p.get("primaryTypeDisplayName") or {}).get("text", "")
    return {
        "place_id":     p.get("id", ""),
        "name":         name,
        "address":      p.get("formattedAddress") or p.get("shortFormattedAddress") or "",
        "lat":          loc.get("latitude"),
        "lng":          loc.get("longitude"),
        "rating":       p.get("rating"),
        "reviews_count": p.get("userRatingCount") or 0,
        "primary_type": p.get("primaryType") or "",
        "primary_type_display": primary_type_dn,
        "phone":        p.get("nationalPhoneNumber") or "",
        "website":      p.get("websiteUri") or "",
        "maps_url":     p.get("googleMapsUri") or "",
        "business_status": p.get("businessStatus") or "",
        "types":        p.get("types") or [],
        "source":       "google_places",
    }
