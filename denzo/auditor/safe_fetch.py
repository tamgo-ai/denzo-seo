"""Bounded public-web reader for untrusted audit URLs; DNS is pinned per hop.

The direct fetch is strict (http.client + ssl, no browser). Some sites' WAF/CDN
(WPEngine/wpcloud, Cloudflare) reject the non-browser TLS fingerprint (JA3) with
429/403, or drop datacenter connections outright. For those we (a) retry briefly
for transient statuses, then (b) fall back to Jina Reader (r.jina.ai), which
fetches from a residential network and returns the rendered HTML.

`fetch_method` records which path was used ("public_http" | "jina") so reports
stay honest about how the page was obtained.
"""
import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import urlsplit, urlunsplit, urljoin

MAX_BYTES = 3 * 1024 * 1024

_JINA_READER = "https://r.jina.ai/"
# Statuses that mean "blocked / temporarily unavailable" and are worth a retry
# and a Jina fallback (as opposed to a genuine 404/410 which we keep as-is).
_TRANSIENT_STATUSES = (401, 403, 408, 429, 500, 502, 503, 504)


def validate_url(url):
    if not isinstance(url, str) or len(url) > 2048:
        raise ValueError('Invalid website URL')
    url = url.strip()
    if '://' not in url:
        url = 'https://' + url
    p = urlsplit(url)
    if (p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password
            or p.port not in (None, 80, 443) or any(ord(c) < 33 for c in url)):
        raise ValueError('Use a public HTTP or HTTPS website')
    host = p.hostname.encode('idna').decode('ascii')
    addresses = list(dict.fromkeys(x[4][0] for x in socket.getaddrinfo(host, p.port or (443 if p.scheme == 'https' else 80), type=socket.SOCK_STREAM)))
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError('Website must resolve only to public addresses')
    return urlunsplit((p.scheme, p.netloc, p.path or '/', p.query, '')), addresses[0]


def _curl_fetch(url, max_bytes=MAX_BYTES, timeout=15):
    """Fetch via system curl — its TLS fingerprint (JA3) is not blocked by most
    WAFs, unlike Python's http.client/ssl. Redirects are followed manually so
    each hop is re-validated for SSRF. Returns a result dict or None."""
    import subprocess
    try:
        chain = []
        current = url
        for _ in range(6):
            normalized, address = validate_url(current)
            p = urlsplit(normalized)
            port = p.port or (443 if p.scheme == 'https' else 80)
            chain.append(normalized)
            result = subprocess.run(
                ['curl', '--silent', '--show-error', '--max-time', str(int(timeout)),
                 '--connect-timeout', '10', '-A', 'Droppin-Auditor/2.0',
                 '--resolve', f'{p.hostname}:{port}:{address}',
                 '--max-filesize', str(max_bytes),
                 '-D', '-',
                 '-w', '\n__CURL_STATUS__%{http_code}\n',
                 normalized],
                capture_output=True, timeout=int(timeout) + 5,
            )
            if result.returncode == 63:
                raise ValueError('Website response exceeded audit size limit')
            if result.returncode != 0:
                return None
            out = result.stdout.decode('utf-8', errors='replace')
            if '__CURL_STATUS__' in out:
                body_part, status_part = out.rsplit('__CURL_STATUS__', 1)
            else:
                body_part, status_part = out, ''
            tokens = status_part.strip().split()
            status = int(tokens[0]) if tokens and tokens[0].isdigit() else 0
            if '\r\n\r\n' in body_part:
                header_block, body_str = body_part.split('\r\n\r\n', 1)
            elif '\n\n' in body_part:
                header_block, body_str = body_part.split('\n\n', 1)
            else:
                header_block, body_str = '', body_part
            headers = {}
            for line in header_block.splitlines():
                if ':' in line:
                    k, v = line.split(':', 1)
                    headers[k.strip()] = v.strip()
            location = next((v for k, v in headers.items() if k.lower() == 'location'), '')
            if status in (301, 302, 303, 307, 308) and location:
                current = urljoin(normalized, location)
                continue
            if status in _TRANSIENT_STATUSES or status >= 500:
                raise ValueError('Website temporarily unavailable or blocking automated analysis')
            body = body_str.encode('utf-8', errors='replace')
            if len(body) > max_bytes:
                raise ValueError('Website response exceeded audit size limit')
            return {'ok': 200 <= status < 300, 'body': body, 'status': status,
                    'headers': headers, 'final_url': normalized,
                    'redirect_chain': chain, 'method': 'curl'}
        raise ValueError('Website has too many redirects')
    except ValueError:
        raise
    except Exception:
        return None


def _attempt_direct(url, max_bytes=MAX_BYTES, timeout=15):
    """Single strict direct fetch. Returns result dict or raises."""
    from denzo.runtime_limits import check_cancelled
    deadline = time.monotonic() + max(1, min(120, float(timeout)))

    def remaining():
        check_cancelled()
        value = deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError('Website request time limit reached')
        return value

    chain = []
    for _ in range(6):
        remaining()
        url, address = validate_url(url)
        p = urlsplit(url)
        chain.append(url)
        port = p.port or (443 if p.scheme == 'https' else 80)
        conn = http.client.HTTPConnection(address, port, timeout=remaining())
        try:
            conn.sock = socket.create_connection((address, port), timeout=remaining())
            if p.scheme == 'https':
                conn.sock = ssl.create_default_context().wrap_socket(conn.sock, server_hostname=p.hostname)
            conn.request('GET', urlunsplit(('', '', p.path, p.query, '')), headers={
                'Host': p.netloc, 'User-Agent': 'Droppin-Auditor/2.0',
                'Accept': 'text/html,application/xhtml+xml,application/xml,text/plain',
                'Accept-Encoding': 'identity', 'Connection': 'close',
            })
            request_socket = conn.sock
            response = conn.getresponse()
            headers = dict(response.getheaders())
            if response.status in (301, 302, 303, 307, 308) and response.getheader('Location'):
                url = urljoin(url, response.getheader('Location'))
                continue
            chunks = []
            size = 0
            while True:
                if response.isclosed():
                    break
                request_socket.settimeout(remaining())
                chunk = response.read1(min(65536, max_bytes + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError('Website response exceeded audit size limit')
            body = b''.join(chunks)
            if len(body) > max_bytes:
                raise ValueError('Website response exceeded audit size limit')
            if response.status >= 500 or response.status in _TRANSIENT_STATUSES:
                raise ValueError('Website temporarily unavailable or blocking automated analysis')
            return {'ok': 200 <= response.status < 300,
                    'body': body, 'status': response.status, 'headers': headers, 'final_url': url,
                    'redirect_chain': chain, 'method': 'public_http'}
        finally:
            conn.close()
    raise ValueError('Website has too many redirects')


def _jina_fetch(url, max_bytes=MAX_BYTES, timeout=25):
    """Fallback via Jina Reader (residential network). Returns result dict or None."""
    try:
        import requests
        with requests.get(
            _JINA_READER + url,
            headers={
                # Jina blocks browser-style UAs (datacenter fingerprint) with 403;
                # a minimal UA is required for it to serve the rendered HTML.
                'X-Return-Format': 'html',
                'User-Agent': 'Mozilla/5.0',
            },
            timeout=timeout,
            allow_redirects=True,
            stream=True,
        ) as r:
            if r.status_code != 200:
                return None
            ct = (r.headers.get('content-type') or '').lower()
            # Jina reports failures (e.g. NXDOMAIN) as JSON error bodies — not HTML.
            if 'application/json' in ct:
                return None
            body = b''
            for chunk in r.iter_content(65536):
                if not chunk:
                    continue
                body += chunk
                if len(body) > max_bytes:
                    body = body[:max_bytes]
                    break
            if len(body) <= 50:
                return None
            # Jina returns the rendered HTML but labels it text/plain; force a
            # text/html content-type so downstream HTML checks pass. Remove any
            # existing content-type key (case-insensitive) so it can't win a
            # case-insensitive lookup downstream.
            headers = {k: v for k, v in r.headers.items() if k.lower() != 'content-type'}
            headers['Content-Type'] = 'text/html; charset=utf-8'
            return {'ok': True, 'body': body, 'status': 200,
                    'headers': headers, 'final_url': url,
                    'redirect_chain': [], 'method': 'jina'}
    except Exception:
        return None
    return None


def fetch_bytes(url, max_bytes=MAX_BYTES, timeout=15, attempts=2, allow_jina=True, **_kwargs):
    last_err = None
    for i in range(max(1, attempts)):
        try:
            return _attempt_direct(url, max_bytes=max_bytes, timeout=timeout)
        except ValueError as e:
            # Only the explicit "blocked/unavailable" signal is retryable. SSRF,
            # bad-URL, oversized and redirect-limit errors must propagate at once.
            if 'temporarily unavailable or blocking' not in str(e):
                raise
            # http.client was blocked (WAF / non-browser TLS fingerprint). curl's
            # TLS fingerprint is accepted by most WAFs — try it before backing off.
            curl = None
            try:
                curl = _curl_fetch(url, max_bytes=max_bytes, timeout=timeout)
            except ValueError as ce:
                if 'temporarily unavailable or blocking' not in str(ce):
                    raise
                curl = None  # curl was also blocked → fall through
            if curl is not None:
                return curl
            last_err = e
            time.sleep(0.4 * (i + 1))
        except socket.gaierror:
            # DNS resolution failed (NXDOMAIN) — permanent. No retry and no Jina
            # fallback can turn a non-existent host into content.
            raise
        except (socket.timeout, TimeoutError, ConnectionError, ssl.SSLError, http.client.HTTPException, OSError) as e:
            last_err = e
            time.sleep(0.4 * (i + 1))

    # Direct path exhausted (blocked / transient) → Jina Reader fallback.
    # Disabled for raw files (robots.txt / sitemap.xml / llms.txt): Jina rewraps
    # them as HTML and corrupts their parsing.
    if allow_jina:
        jina = _jina_fetch(url, max_bytes=max_bytes)
        if jina is not None:
            return jina

    raise last_err or ValueError('Website temporarily unavailable or blocking automated analysis')


def fetch_html(url, **kwargs):
    result = fetch_bytes(url, **kwargs)
    result['html'] = result.pop('body', b'').decode('utf-8', errors='replace')
    return result
