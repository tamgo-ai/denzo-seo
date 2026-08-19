"""
stealth_fetch.py — Silicon Valley-grade URL fetcher with 4-pass Cloudflare bypass.

Pass 0: curl-impersonate via subprocess (fastest, best TLS fingerprint)
Pass 1: requests with randomized realistic browser headers
Pass 2: cloudscraper (handles Cloudflare JS challenges)
Pass 3: Playwright + playwright-stealth (handles Cloudflare Turnstile v2/v3)
"""
from __future__ import annotations
import random
import re
import subprocess
import time
from typing import Optional


# ── Realistic User-Agent pool (Chrome, Edge, Firefox — Windows/Mac/Linux) ──────
_UA_POOL = [
    # Chrome Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    # Chrome Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # Edge Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    # Firefox Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    # Firefox Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:125.0) Gecko/20100101 Firefox/125.0",
    # Chrome Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Safari Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
]

# ── Accept-Language pool ─────────────────────────────────────────────
_ACCEPT_LANG_POOL = [
    "en-US,en;q=0.9",
    "en-US,en;q=0.9,es;q=0.8",
    "en-GB,en;q=0.9",
    "en-US,en;q=0.9,fr;q=0.8",
    "en-US,en;q=0.8",
]


def _build_headers(ua: str = None) -> dict:
    """Build randomized, realistic browser headers for a given UA."""
    ua = ua or random.choice(_UA_POOL)
    lang = random.choice(_ACCEPT_LANG_POOL)
    is_firefox = "Firefox" in ua
    is_safari = "Safari" in ua and "Chrome" not in ua

    if is_firefox:
        return {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": lang,
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        }
    elif is_safari:
        return {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": lang,
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
    else:
        # Chrome / Edge
        chrome_ver = re.search(r"Chrome/(\d+)", ua)
        cv = chrome_ver.group(1) if chrome_ver else "124"
        return {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": lang,
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-CH-UA": f'"Chromium";v="{cv}", "Google Chrome";v="{cv}", "Not(A:Brand";v="24"',
            "Sec-CH-UA-Mobile": "?0",
            "Sec-CH-UA-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        }


# Keep BROWSER_HEADERS for backward compat (other files may import it)
BROWSER_HEADERS = _build_headers(_UA_POOL[0])


def _is_cloudflare_block(status: int, html: str) -> bool:
    """Detect Cloudflare challenge/block pages."""
    if status in (403, 503, 429):
        # 429 might be legitimate rate limit — check for CF markers before returning True
        if status == 429:
            cf_429 = ["cloudflare", "cf-ray", "just a moment"]
            return any(m.lower() in html.lower() for m in cf_429)
        return True
    cf_markers = [
        "cf-browser-verification",
        "Checking if the site connection is secure",
        "cf_chl_opt",
        "challenge-platform",
        "Just a moment",
        "Enable JavaScript and cookies to continue",
        "Ray ID",
        "__cf_chl_opt",
        "cf-spinner",
    ]
    for marker in cf_markers:
        if marker.lower() in html.lower():
            return True
    return False


def _curl_fetch(url: str, timeout: int = 20) -> Optional[str]:
    """
    Pass 0: Use system curl with randomized headers.
    Returns HTML string or None if curl is unavailable / fails.
    Curl uses the OS TLS stack — better fingerprint than Python's.
    """
    try:
        ua = random.choice(_UA_POOL)
        result = subprocess.run(
            [
                "curl",
                "--silent",
                "--location",
                "--max-time", str(timeout),
                "--max-redirs", "5",
                "--compressed",
                "-A", ua,
                "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "-H", f"Accept-Language: {random.choice(_ACCEPT_LANG_POOL)}",
                "-H", "Cache-Control: max-age=0",
                "-H", "Upgrade-Insecure-Requests: 1",
                "--connect-timeout", "10",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 5,
        )
        # Accept any non-empty response. robots.txt, sitemap.xml and other raw
        # files are often legitimately under 200 bytes; a size floor here falsely
        # classifies them as "network block" and pushes the whole domain onto the
        # Jina fallback (which can't return raw XML/plain-text anyway).
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        return None


# ── Jina Reader fallback ────────────────────────────────────────────
# Sites hosted on Lovable / behind Cloudflare often drop or refuse connections
# from datacenter IPs (timeout / ECONNREFUSED), so curl/requests/playwright all
# fail at the network layer. Jina Reader (r.jina.ai) fetches from a
# residential-grade network and returns the rendered HTML, bypassing the block.
_JINA_READER = "https://r.jina.ai/"


def _jina_fetch(url: str, timeout: int = 25) -> Optional[str]:
    """Fetch raw HTML via Jina Reader. Returns HTML string or None on failure."""
    try:
        import requests
        r = requests.get(
            _JINA_READER + url,
            headers={
                # Jina blocks browser-style UAs (datacenter fingerprint) with 403;
                # a minimal UA is required for it to serve the rendered HTML.
                "X-Return-Format": "html",
                "User-Agent": "Mozilla/5.0",
            },
            timeout=timeout,
            allow_redirects=True,
        )
        if r.status_code == 200 and len(r.text) > 50:
            return r.text
        return None
    except Exception:
        return None


# ── Datacenter-block cache ──────────────────────────────────────────
# Domain -> expiry timestamp. Once the primary fetch detects a domain drops
# datacenter connections (curl got nothing, Jina worked), remember it so
# sub-fetches (sitemap/robots/llms.txt) skip the ~20s curl timeout and go
# straight to Jina during the same analysis run.
_jina_domains: dict[str, float] = {}
_JINA_DOMAIN_TTL = 600  # seconds


def _domain_of(url: str) -> str:
    from urllib.parse import urlparse
    return (urlparse(url).netloc or '').lower()


def _requests_meta(url: str, timeout: int = 15) -> Optional[dict]:
    """
    Lightweight companion request to capture HTTP response metadata that the
    curl body-fetch cannot expose cleanly: response headers, redirect chain,
    final URL and real status code. Uses stream=True so the body is NOT
    downloaded (headers only). Best-effort — returns None on any failure.
    """
    try:
        import requests
        r = requests.get(
            url, headers=_build_headers(), timeout=timeout,
            allow_redirects=True, stream=True,
        )
        chain = [h.url for h in r.history] + [r.url]
        headers = {k: v for k, v in r.headers.items()}
        status = r.status_code
        final = r.url
        try:
            r.close()
        except Exception:
            pass
        return {"status": status, "headers": headers,
                "redirect_chain": chain, "final_url": final}
    except Exception:
        return None


def fetch_html(url: str, timeout: int = 25, log_fn=None, capture_meta: bool = False, allow_jina: bool = True) -> dict:
    """
    Fetch URL with progressive fallback for Cloudflare-protected sites.

    allow_jina=False disables the Jina Reader fallback (and the datacenter-block
    fast-path). Use it for raw files (robots.txt, sitemap.xml, llms.txt): Jina
    renders/rewraps content and cannot return raw XML or plain text, so it must
    never be used for those.

    Returns:
        {
            "ok": bool,
            "html": str,
            "status": int,
            "method": "curl" | "requests" | "cloudscraper" | "playwright" | "jina",
            "error": str (if not ok)
        }
    """
    def log(msg):
        if log_fn:
            log_fn(msg)

    domain = _domain_of(url)

    # ── Fast-path: domain already known to block this datacenter IP ─────────
    # Skip the ~20s curl/requests timeouts and fetch straight from Jina. Only
    # the primary page goes through metadata capture; sub-fetches get {} here.
    if allow_jina and domain and _jina_domains.get(domain, 0) > time.time():
        log("[stealth_fetch] domain cached as datacenter-blocked — going straight to Jina Reader")
        jina_html = _jina_fetch(url, timeout=min(timeout, 25))
        if jina_html and not _is_cloudflare_block(200, jina_html):
            log("[stealth_fetch] Jina Reader OK (cached block)")
            return {"ok": True, "html": jina_html, "status": 200, "method": "jina", "headers": {}, "redirect_chain": [], "final_url": url}
        # Domain is network-blocked; direct passes would only time out (~95s).
        # Bail fast instead of burning time on curl/requests/cloudscraper/playwright.
        log("[stealth_fetch] Jina also failed for blocked domain — bailing fast")
        return {"ok": False, "html": "", "status": 0, "method": "jina", "error": "Datacenter-blocked domain — Jina could not retrieve this resource"}

    # ── Response metadata (headers, redirect chain, real status) ────────────
    # Captured once for the primary page fetch so downstream analyzers can
    # audit security headers, caching, HTTP status and redirect chains.
    # Skipped for auxiliary fetches (robots.txt, sitemaps, llms.txt) for speed.
    _meta = {"headers": {}, "redirect_chain": [], "final_url": url}
    if capture_meta:
        _m = _requests_meta(url, timeout=min(timeout, 15))
        if _m:
            _meta = _m

    # ─── Pass 0: curl (best TLS fingerprint, no Python overhead) ─────────────
    html0 = _curl_fetch(url, timeout=min(timeout, 20))
    if html0 and not _is_cloudflare_block(200, html0):
        log(f"[stealth_fetch] Pass 0 (curl) OK")
        return {"ok": True, "html": html0, "status": _meta.get("status", 200) or 200, "method": "curl", "headers": _meta["headers"], "redirect_chain": _meta["redirect_chain"], "final_url": _meta["final_url"]}
    if html0:
        log("[stealth_fetch] Pass 0 (curl) blocked by CF — trying requests…")

    # ─── Fallback: Jina Reader (residential proxy) ──────────────────────────
    # curl returned nothing → likely a network-level block on this datacenter IP,
    # not a Cloudflare challenge page. Jina fetches from a residential network.
    if allow_jina and not html0:
        log("[stealth_fetch] curl returned nothing (datacenter block?) — trying Jina Reader…")
        jina_html = _jina_fetch(url, timeout=min(timeout, 25))
        if jina_html and not _is_cloudflare_block(200, jina_html):
            log("[stealth_fetch] Jina Reader OK")
            _jina_domains[domain] = time.time() + _JINA_DOMAIN_TTL
            return {"ok": True, "html": jina_html, "status": 200, "method": "jina", "headers": _meta["headers"], "redirect_chain": _meta["redirect_chain"], "final_url": _meta["final_url"]}
        log("[stealth_fetch] Jina Reader failed — falling through to requests/cloudscraper/playwright…")

    # ─── Pass 1: requests with randomized headers ────────────────────────
    try:
        import requests
        ua = random.choice(_UA_POOL)
        headers = _build_headers(ua)
        r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        html = r.text
        if not _is_cloudflare_block(r.status_code, html):
            log(f"[stealth_fetch] Pass 1 (requests) OK — {r.status_code}")
            return {"ok": True, "html": html, "status": r.status_code, "method": "requests", "headers": (_meta["headers"] or {k: v for k, v in r.headers.items()}), "redirect_chain": (_meta["redirect_chain"] or ([h.url for h in r.history] + [r.url])), "final_url": (_meta["final_url"] or r.url)}
        log(f"[stealth_fetch] Pass 1 blocked ({r.status_code}) — trying cloudscraper…")
    except Exception as e:
        log(f"[stealth_fetch] Pass 1 error: {e} — trying cloudscraper…")

    # ─── Pass 2: cloudscraper ────────────────────────────────────────
    try:
        import cloudscraper
        ua2 = random.choice(_UA_POOL)
        is_mobile = False
        platform = "windows"
        if "Macintosh" in ua2:
            platform = "darwin"
        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": platform, "mobile": is_mobile}
        )
        # Add randomized headers to cloudscraper session
        for k, v in _build_headers(ua2).items():
            scraper.headers.update({k: v})
        r2 = scraper.get(url, timeout=timeout, allow_redirects=True)
        html2 = r2.text
        if not _is_cloudflare_block(r2.status_code, html2):
            log(f"[stealth_fetch] Pass 2 (cloudscraper) OK — {r2.status_code}")
            return {"ok": True, "html": html2, "status": r2.status_code, "method": "cloudscraper", "headers": (_meta["headers"] or {k: v for k, v in r2.headers.items()}), "redirect_chain": (_meta["redirect_chain"] or ([h.url for h in r2.history] + [r2.url])), "final_url": (_meta["final_url"] or r2.url)}
        log(f"[stealth_fetch] Pass 2 blocked ({r2.status_code}) — trying Playwright…")
    except Exception as e:
        log(f"[stealth_fetch] Pass 2 error: {e} — trying Playwright…")

    # ─── Pass 3: Playwright stealth (full browser, bypasses Turnstile) ────────
    try:
        from playwright.sync_api import sync_playwright
        try:
            from playwright_stealth import stealth_sync
            has_stealth = True
        except ImportError:
            has_stealth = False
            log("[stealth_fetch] playwright-stealth not installed — using plain Playwright")

        ua3 = random.choice([ua for ua in _UA_POOL if "Chrome" in ua and "Windows" in ua])

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--disable-setuid-sandbox",
                    "--disable-infobars",
                    "--window-size=1366,768",
                    "--disable-extensions",
                    "--disable-plugins-discovery",
                    "--disable-default-apps",
                    "--no-first-run",
                    "--disable-background-networking",
                    "--disable-sync",
                    "--disable-translate",
                    "--metrics-recording-only",
                    "--safebrowsing-disable-auto-update",
                    "--disable-features=IsolateOrigins,site-per-process",
                ]
            )
            ctx = browser.new_context(
                user_agent=ua3,
                locale="en-US",
                timezone_id="America/Los_Angeles",
                viewport={"width": 1366, "height": 768},
                java_script_enabled=True,
                bypass_csp=True,
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                    "Upgrade-Insecure-Requests": "1",
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Sec-Fetch-User": "?1",
                },
                permissions=["geolocation"],
                geolocation={"longitude": -118.2437, "latitude": 34.0522},  # LA
            )
            page = ctx.new_page()

            if has_stealth:
                stealth_sync(page)

            # Hide all automation indicators
            page.add_init_script("""
                // Remove webdriver flag
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                // Fake plugins
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [
                        {name:'Chrome PDF Plugin',filename:'internal-pdf-viewer'},
                        {name:'Chrome PDF Viewer',filename:'mhjfbmdgcfjbbpaeojofohoefgiehjai'},
                        {name:'Native Client',filename:'internal-nacl-plugin'}
                    ]
                });
                // Fake languages
                Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
                // Chrome runtime
                window.chrome = {runtime: {}, loadTimes: function(){}, csi: function(){}, app: {}};
                // Permissions
                const origQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                    Promise.resolve({state: Notification.permission}) :
                    origQuery(parameters)
                );
            """)

            resp = page.goto(url, wait_until="networkidle", timeout=timeout * 1000)

            # Wait for Cloudflare challenge to resolve
            try:
                page.wait_for_function(
                    "() => !document.title.toLowerCase().includes('just a moment') && "
                    "!document.title.toLowerCase().includes('checking')",
                    timeout=15000
                )
            except Exception:
                pass

            # Small random human-like delay
            time.sleep(random.uniform(0.5, 1.5))

            html3 = page.content()
            status3 = resp.status if resp else 200
            browser.close()

            if not _is_cloudflare_block(status3, html3):
                log(f"[stealth_fetch] Pass 3 (Playwright) OK — {status3}")
                return {"ok": True, "html": html3, "status": status3, "method": "playwright", "headers": (_meta["headers"] or (dict(resp.headers) if resp else {})), "redirect_chain": _meta["redirect_chain"] or [url], "final_url": (_meta["final_url"] or (resp.url if resp else url))}
            else:
                log("[stealth_fetch] Pass 3 still blocked — site is hardened CF Enterprise")
                return {
                    "ok": False, "html": html3, "status": status3,
                    "method": "playwright",
                    "error": "Cloudflare Enterprise protection — cannot bypass automatically."
                }

    except Exception as e:
        log(f"[stealth_fetch] Pass 3 error: {e}")
        return {
            "ok": False, "html": "", "status": 0,
            "method": "playwright",
            "error": f"All fetch methods failed: {e}"
        }


def parse_html(html: str) -> dict:
    """
    Extract structured data from raw HTML.
    Returns dict with: title, meta_desc, h1, h2s, h3s, text, links, images, word_count
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")

    # Remove noise
    for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()

    title = soup.find("title")
    title = title.get_text(strip=True) if title else ""

    meta_desc = ""
    md = soup.find("meta", attrs={"name": "description"})
    if md:
        meta_desc = md.get("content", "")

    h1s = [h.get_text(strip=True) for h in soup.find_all("h1")]
    h2s = [h.get_text(strip=True) for h in soup.find_all("h2")]
    h3s = [h.get_text(strip=True) for h in soup.find_all("h3")]

    all_text = soup.get_text(separator=" ", strip=True)
    all_text = re.sub(r"\s{3,}", "  ", all_text)

    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        if href and not href.startswith(("#", "javascript:", "mailto:", "tel:")):
            links.append({"href": href, "text": text[:80]})

    # Extract images for visual analysis
    images = []
    for img in soup.find_all("img"):
        images.append({
            "src": img.get("src", ""),
            "alt": img.get("alt", ""),
            "width": img.get("width", ""),
            "height": img.get("height", ""),
            "loading": img.get("loading", ""),
        })

    return {
        "title": title,
        "meta_desc": meta_desc,
        "h1": h1s[0] if h1s else "",
        "h2s": h2s[:10],
        "h3s": h3s[:10],
        "all_text": all_text[:8000],
        "links": links[:50],
        "images": images[:30],
        "word_count": len(all_text.split()),
    }


def fetch_and_parse(url: str, timeout: int = 25, log_fn=None) -> dict:
    """Convenience: fetch + parse in one call."""
    result = fetch_html(url, timeout=timeout, log_fn=log_fn)
    if result["ok"]:
        parsed = parse_html(result["html"])
        parsed["ok"] = True
        parsed["method"] = result["method"]
        parsed["status"] = result["status"]
        return parsed
    return result
