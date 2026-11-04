"""Render retained content with the actual storefront template, without writes."""
from decimal import Decimal
from types import SimpleNamespace

from django.shortcuts import render
from django.utils import timezone

from core.models import Category, Product
from core.pricing import PriceQuote, PriceSummary
from core.product_options import build_product_options
from stocks.models import Variant

from .media import asset_name, image_url
from .exceptions import HistoryError
from .services import authorized


def response(request, version):
    from .products import _state
    authorized(request.user)
    state = _state(version)
    values = dict(state['fields'])
    if values['benefits'] is None and 'resolved_benefits' in state:
        values['benefits'] = '\n'.join(state['resolved_benefits'])
    live = Product.objects.filter(pk=version.object_id).first()
    values['price'] = Decimal(values['price'])
    product = Product(pk=version.object_id, slug=live.slug if live else f'version-{version.object_id}', **values)
    # Keep old category wording visible even when that category was renamed.
    if values['category_id']:
        category = Category.objects.filter(pk=values['category_id']).first()
        product.category = Category(pk=values['category_id'], slug=category.slug if category else '',
            name=state.get('category_labels', {}).get(str(values['category_id']), category.name if category else 'Ancienne rubrique'))
    try:
        if product.main_image:
            _, product.main_image = asset_name(version, 'media', product.main_image.name)
        elif product.static_image:
            kind, name = asset_name(version, 'static', product.static_image)
            if kind == 'media':
                product.main_image, product.static_image = name, ''
            else:
                product.static_image = name
    except HistoryError:
        # Text and the remaining photographs stay reviewable. The outer screen
        # explains the missing file and blocks an incomplete restoration.
        product.main_image, product.static_image = '', ''
    gallery = []

    def photo(name, kind='media', alt=''):
        url = image_url(name, kind, version=version)
        if url and not any(row['url'] == url for row in gallery):
            gallery.append({'url': url, 'thumbnail': url, 'alt': alt or product.localized_name})

    if values['main_image']:
        photo(values['main_image'])
    elif values['static_image']:
        photo(values['static_image'], 'static')
    for row in state['gallery']:
        photo(row['image'], alt=row['alt'])
    for row in state['imported_gallery']:
        photo(row['name'], row['kind'], row.get('alt', ''))
    colors = []
    for row in state['colors']:
        url = image_url(row['image'], version=version)
        if url:
            colors.append(SimpleNamespace(name=row['name'], image=SimpleNamespace(url=url)))
            photo(row['image'], alt=f'{product.name} — {row["name"]}')
    live_variants = {row.pk: row for row in Variant.objects.filter(product_id=version.object_id)}
    variants = []
    for row in state['variants']:
        current = live_variants.get(row['id'])
        variant = Variant(pk=row['id'], product=product, size=row['size'], color=row['color'],
            reference=row['reference'], price=Decimal(row['price']) if row['price'] is not None else None,
            quantity=current.quantity if current else None, reserved=current.reserved if current else 0)
        regular = variant.regular_price
        variant.price_quote = PriceQuote(current=regular, regular=regular)
        variants.append(variant)
    options = build_product_options(product, variants, colors)
    prices = [row.regular_price for row in variants] or [product.price]
    return render(request, 'core/product_detail.html', {
        'product': product, 'gallery': gallery, 'images': [row['url'] for row in gallery],
        'related': [], 'catalog_preview': True, 'history_preview': True, 'editorial_preview': True,
        'variants': variants, 'product_sizes': options['sizes'], 'product_colors': options['colors'],
        'price_summary': PriceSummary(min(prices), max(prices)), 'selected_variant': None,
        'seo': {'title': f'Aperçu privé — {product.name}', 'robots': 'noindex,nofollow'},
        'pricing_at': timezone.now(), 'next_price_change': None,
        'has_available_variant': any(row.available > 0 for row in variants),
        'has_unknown_stock': any(row.quantity is None for row in variants),
    })
