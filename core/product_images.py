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


def imported_gallery_urls(product, initial_images):
    """Retain reviewed aliases' original pictures without duplicate image bytes."""
    from hashlib import sha256
    from urllib.parse import unquote
    from django.conf import settings
    images = list(initial_images)
    if not product.include_imported_gallery:
        return images
    sources = [product]
    if product.pk:
        sources.extend(product.duplicate_records.filter(include_imported_gallery=True).only('slug'))
    base = (Path(settings.BASE_DIR) / 'core/static').resolve()
    prefix = static('')
    seen = set()
    for url in images:
        if not url.startswith(prefix):
            continue
        path = (base / unquote(url[len(prefix):])).resolve()
        if path.is_relative_to(base.resolve()) and path.is_file():
            seen.add(sha256(path.read_bytes()).digest())
    for source in sources:
        for folder in ('products', 'best_product', 'femmes', 'hommes', 'enfants'):
            directory = (base / 'core/img' / folder / source.slug).resolve()
            if not directory.is_relative_to(base.resolve()) or not directory.is_dir():
                continue
            for path in sorted(directory.iterdir()):
                if path.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp') or not path.is_file():
                    continue
                digest = sha256(path.read_bytes()).digest()
                if digest in seen:
                    continue
                seen.add(digest)
                url = static(path.relative_to(base).as_posix())
                if url not in images:
                    images.append(url)
    return images
