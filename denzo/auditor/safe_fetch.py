"""Bounded public-web reader for untrusted audit URLs; DNS is pinned per hop.

No browser/proxy fallbacks: a blocked or unreadable page is an incomplete audit.
"""
import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import urlsplit, urlunsplit, urljoin

MAX_BYTES = 3 * 1024 * 1024


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


def fetch_bytes(url, max_bytes=MAX_BYTES, timeout=15, **_kwargs):
    from denzo.runtime_limits import check_cancelled
    deadline=time.monotonic()+max(1,min(120,float(timeout)))
    def remaining():
        check_cancelled()
        value=deadline-time.monotonic()
        if value<=0:
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
            chunks=[]
            size=0
            while True:
                if response.isclosed():
                    break
                request_socket.settimeout(remaining())
                chunk=response.read1(min(65536,max_bytes+1-size))
                if not chunk:
                    break
                chunks.append(chunk);size+=len(chunk)
                if size>max_bytes:
                    raise ValueError('Website response exceeded audit size limit')
            body=b''.join(chunks)
            if len(body) > max_bytes:
                raise ValueError('Website response exceeded audit size limit')
            if response.status >= 500 or response.status in (401, 403, 408, 429):
                raise ValueError('Website temporarily unavailable or blocking automated analysis')
            return {'ok': 200 <= response.status < 300,
                    'body': body, 'status': response.status, 'headers': headers, 'final_url': url,
                    'redirect_chain': chain, 'method': 'public_http'}
        finally:
            conn.close()
    raise ValueError('Website has too many redirects')


def fetch_html(url, **kwargs):
    result = fetch_bytes(url, **kwargs)
    result['html']=result.pop('body',b'').decode('utf-8',errors='replace')
    return result
