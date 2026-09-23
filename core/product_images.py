import json
from functools import lru_cache
from pathlib import Path
from django.templatetags.static import static


@lru_cache(maxsize=1)
def image_manifest():
    path = Path(__file__).parent/'static/core/product-images.json'
    return json.loads(path.read_text()) if path.exists() else {}


def image_variants(path):
    return [{**item, 'url':static(item['path'])} for item in image_manifest().get(path, [])]


def thumbnail_url(url):
    prefix = static('')
    if url.startswith(prefix):
        variants = image_variants(url[len(prefix):])
        if variants:
            return variants[0]['url']
    return url
