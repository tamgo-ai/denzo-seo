"""Regression guards for Cloudflare challenge/block detection in the agents' fetcher."""
from denzo.agents.utils.stealth_fetch import _is_cloudflare_block


def test_cloudflare_js_detection_script_is_not_a_block():
    # Cloudflare injects this passive JS-detection script into every page (even
    # for real browsers). It must NOT be treated as a challenge/block.
    html = ('<html><head><title>Real Site</title></head><body>'
            '<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>'
            '<p>real content</p></body></html>')
    assert _is_cloudflare_block(200, html) is False


def test_real_cloudflare_challenge_is_still_detected():
    html = ('<html><head><title>Just a moment...</title></head><body>'
            '<form id="challenge-form" action="/cdn-cgi/challenge-platform/"></form>'
            '<noscript>Enable JavaScript and cookies to continue</noscript></body></html>')
    assert _is_cloudflare_block(200, html) is True


def test_cloudflare_403_is_a_block():
    assert _is_cloudflare_block(403, '<html>blocked</html>') is True
