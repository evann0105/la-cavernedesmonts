"""Factual product structured data, using the storefront's pricing clock.

Google's single-page ProductGroup format needs stable variant identifiers and
URLs that select those exact variants. Merchant offers are deliberately omitted
while online selling is disabled or a variant's stock has not been counted.
"""
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

from django.utils import timezone

from .pricing import price_quote
from .seo_products import build_product_seo, clean_text


SCHEMA = 'https://schema.org/'


def _absolute_url(value, base):
    value = str(value or '').strip()
    if not value:
        return ''
    result = urljoin(base, value)
    parsed = urlsplit(result)
    return result if parsed.scheme in ('http', 'https') and parsed.netloc else ''


def _canonical_url(url):
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', ''))


def _key(value):
    return ' '.join(str(value or '').split()).casefold()


def _known_attribute(value):
    value = clean_text(value or '')
    unresolved = ('à confirmer', 'a confirmer', 'non renseign', 'non communiqu',
                  'non précisé', 'non precise', 'à vérifier', 'a verifier')
    return '' if any(text in value.casefold() for text in unresolved) else value


def _image_data(product, images, base):
    """Keep only real pictures; never substitute a known different color."""
    all_images, by_color = [], {}
    for entry in images or []:
        raw = entry.get('url', '') if isinstance(entry, dict) else entry
        absolute = _absolute_url(raw, base)
        if not absolute:
            continue
        if absolute not in all_images:
            all_images.append(absolute)
        if isinstance(entry, dict):
            color = _key(entry.get('color', ''))
            if color:
                by_color.setdefault(color, []).append(absolute)

    prefetched = getattr(product, '_prefetched_objects_cache', {})
    photos = prefetched.get('color_photos')
    if photos is None:
        photos = product.color_photos.all() if product.pk else []
    for photo in photos:
        if not photo.image:
            continue
        absolute = _absolute_url(photo.image.url, base)
        if not absolute:
            continue
        if absolute not in all_images:
            all_images.append(absolute)
        by_color.setdefault(_key(photo.name), []).append(absolute)

    main_image = _absolute_url(product.image_url, base)
    main_color = _key(product.main_color)
    if main_image:
        if main_image not in all_images:
            all_images.insert(0, main_image)
        if main_color:
            by_color.setdefault(main_color, []).insert(0, main_image)

    return all_images, by_color


def _offer(product, variant, url, at, can_sell):
    if not can_sell or not product.is_published:
        return None
    # Checkout requires a counted Variant, including for legacy products whose
    # broad availability flag was manually set to "available".
    if variant is None:
        return None
    if product.availability == 'unavailable':
        state = 'OutOfStock'
    elif variant.quantity is None:
        return None
    else:
        state = 'InStock' if variant.available > 0 else 'OutOfStock'

    quote = price_quote(product, variant, at=at)
    try:
        price = Decimal(str(quote.current))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not price.is_finite() or price <= 0:
        return None
    offer = {
        '@type': 'Offer',
        'url': url,
        'priceCurrency': 'EUR',
        'price': format(price, '.2f'),
        'availability': SCHEMA + state,
    }
    # Use the exact next boundary, including an earlier campaign, rather than
    # inventing an end date for an otherwise unbounded regular price.
    if quote.next_boundary is not None:
        offer['validThrough'] = quote.next_boundary.isoformat()
    return offer


def build_product_schema(product, *, url, images, variants=None, at=None, can_sell=False):
    """Return JSON-serializable Product/ProductGroup data for one detail page.

    ``url`` is the absolute product URL; tracking/variant parameters are removed
    for its canonical identity. ``images`` accepts absolute/relative URLs or
    gallery dictionaries with ``url`` and optional exact ``color``. Pass the
    already prefetched variants and the same aware ``at`` used for visible
    prices. The caller must escape ``<`` when embedding the JSON in HTML.
    """
    at = at or timezone.now()
    canonical = _canonical_url(url)
    if variants is None:
        variants = getattr(product, '_prefetched_objects_cache', {}).get('stock_variants')
        if variants is None:
            variants = product.stock_variants.all() if product.pk else []
    variants = list(variants)
    seo = build_product_seo(product, variants=variants)
    pictures, color_pictures = _image_data(product, images, canonical)
    name = clean_text(product.name)
    identifier = f'LCDM-P{product.pk}'
    common = {'name': name, 'description': seo.description}
    if pictures:
        common['image'] = pictures
    brand = _known_attribute(product.brand)
    if brand:
        common['brand'] = {'@type': 'Brand', 'name': brand}
    composition = _known_attribute(product.composition)
    if composition:
        common['material'] = composition
    if product.category_id:
        common['category'] = clean_text(product.category.name)

    def variant_product(variant, *, grouped):
        variant_url = canonical + '?' + urlencode({'variante': variant.pk})
        variant_id = f'{identifier}-V{variant.pk}'
        color, size = clean_text(variant.color), clean_text(variant.size)
        item = {
            '@type': 'Product',
            '@id': variant_url + '#product',
            'url': variant_url,
            'sku': variant_id,
            'name': ' — '.join(part for part in (name, color, size) if part),
            'description': seo.description,
        }
        if not grouped:
            item.update({key: value for key, value in common.items() if key not in ('name', 'image')})
        if color:
            item['color'] = color
        if size:
            item['size'] = size
        variant_images = color_pictures.get(_key(color), [])
        # Without any color-specific photography a shared product image is
        # truthful for its sole recorded color. With multiple colors, leave an
        # unpictured color unillustrated rather than showing the wrong one.
        if not variant_images and len({_key(v.color) for v in variants}) <= 1 and not color_pictures:
            variant_images = pictures
        if variant_images:
            item['image'] = list(dict.fromkeys(variant_images))
        offer = _offer(product, variant, variant_url, at, can_sell)
        if offer:
            item['offers'] = offer
        return item

    if len(variants) > 1:
        group = {
            '@context': SCHEMA,
            '@type': 'ProductGroup',
            '@id': canonical + '#product-group',
            'url': canonical,
            **common,
            'productGroupID': identifier,
            'variesBy': [SCHEMA + field for field in ('color', 'size')
                         if len({_key(getattr(v, field)) for v in variants}) > 1],
            'hasVariant': [variant_product(variant, grouped=True) for variant in variants],
        }
        return group
    if variants:
        return {'@context': SCHEMA, **variant_product(variants[0], grouped=False)}
    result = {
        '@context': SCHEMA, '@type': 'Product', '@id': canonical + '#product',
        'url': canonical, 'sku': identifier, **common,
    }
    if _known_attribute(product.main_color):
        result['color'] = clean_text(product.main_color)
    offer = _offer(product, None, canonical, at, can_sell)
    if offer:
        result['offers'] = offer
    return result
