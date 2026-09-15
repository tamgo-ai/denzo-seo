"""Bounded, cached reads of real image dimensions."""
from functools import lru_cache
from io import BytesIO


@lru_cache(maxsize=256)
def image_dimensions(url):
    from PIL import Image
    from denzo.auditor.safe_fetch import fetch_bytes
    data = fetch_bytes(url, max_bytes=8*1024*1024)
    if not data.get('ok'):
        raise ValueError('Image unavailable')
    try:
        with Image.open(BytesIO(data['body'])) as img:
            w,h = img.size
            if not (0<w<=20000 and 0<h<=20000 and w*h<=40000000):
                raise ValueError('Image exceeds dimension budget')
            return w,h
    except Exception as exc:
        raise ValueError('Cannot read image dimensions') from exc
