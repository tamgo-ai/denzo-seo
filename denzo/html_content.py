"""Sanitise generated fragments before rendering them on public websites."""

from urllib.parse import urlsplit
from bs4 import BeautifulSoup


def sanitize_fragment(value):
    soup = BeautifulSoup(value or "", "html.parser")
    for tag in soup.find_all(
        [
            "script",
            "style",
            "object",
            "embed",
            "base",
            "form",
            "input",
            "button",
            "textarea",
            "link",
            "meta",
        ]
    ):
        tag.decompose()
    for tag in soup.find_all(True):
        for name in list(tag.attrs):
            if name.lower().startswith("on") or name.lower() in (
                "srcdoc",
                "formaction",
            ):
                del tag[name]
        for name in ("href", "src", "xlink:href"):
            url = "".join(str(tag.get(name, "")).split())
            if urlsplit(url).scheme.lower() not in (
                "",
                "https",
                "http",
                "mailto",
                "tel",
            ):
                tag.attrs.pop(name, None)
        if tag.name == "iframe":
            parsed = urlsplit(tag.get("src", ""))
            if parsed.scheme != "https" or parsed.hostname not in (
                "www.youtube.com",
                "www.youtube-nocookie.com",
                "player.vimeo.com",
            ):
                tag.decompose()
            else:
                tag["sandbox"] = "allow-scripts allow-same-origin allow-presentation"
        elif tag.name == "h1":
            tag.name = "h2"
    return soup.body.decode_contents() if soup.body else str(soup)
