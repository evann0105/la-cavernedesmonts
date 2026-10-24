"""Shared catalogue filters for the list and its cross-page selection."""

import re

from django.db.models import BooleanField, Case, Exists, F, OuterRef, Q, Value, When
from django.db.models.functions import Lower

from stocks.models import Variant

from .models import Product
from .pricing import with_catalog_prices


FILTER_KEYS = ('q', 'categorie', 'statut', 'attention', 'tri')
_STATUSES = {'', 'published', 'draft', 'duplicate'}
_ATTENTION = {'', 'missing_photo', 'missing_brand', 'missing_stock', 'out_of_stock'}
_SORTS = {'', 'name', '-name', 'price', '-price'}


def normalize_catalog_filters(params):
    """Accept only the public filter values, never arbitrary ORM ordering."""
    values = {}
    for key in FILTER_KEYS:
        value = params.get(key, '')
        values[key] = value.strip() if isinstance(value, str) else ''
    values['q'] = values['q'][:100]
    category = values['categorie']
    if len(category) > 140 or (category and not re.fullmatch(r'[-a-zA-Z0-9_]+', category)):
        values['categorie'] = ''
    for key, allowed in (('statut', _STATUSES), ('attention', _ATTENTION), ('tri', _SORTS)):
        if values[key] not in allowed:
            values[key] = ''
    return values


def catalog_queryset(filters):
    """Return complete rows with prices and stock flags, without variant joins.

    A stock that has not been entered is unknown, not sold out. A known stock
    is sold out only when every unit of every variant is gone or reserved.
    """
    filters = normalize_catalog_filters(filters)
    variants = Variant.objects.filter(product_id=OuterRef('pk'))
    no_main_photo = Q(main_image__isnull=True) | Q(main_image='')
    no_static_photo = Q(static_image__isnull=True) | Q(static_image='')
    products = Product.objects.select_related('category', 'duplicate_of').annotate(
        _catalog_has_variants=Exists(variants),
        _catalog_unknown_stock=Exists(variants.filter(quantity__isnull=True)),
        _catalog_available_stock=Exists(variants.filter(quantity__gt=F('reserved'))),
        _needs_photo=Case(
            When(no_main_photo & no_static_photo, then=Value(True)),
            default=Value(False), output_field=BooleanField(),
        ),
        _needs_brand=Case(
            When(Q(brand__isnull=True) | Q(brand__regex=r'^\s*$'), then=Value(True)),
            default=Value(False), output_field=BooleanField(),
        ),
    ).annotate(
        _needs_stock=Case(
            When(_catalog_has_variants=False, then=Value(True)),
            When(_catalog_unknown_stock=True, then=Value(True)),
            default=Value(False), output_field=BooleanField(),
        ),
        _out_of_stock=Case(
            When(_catalog_has_variants=True, _catalog_unknown_stock=False,
                 _catalog_available_stock=False, then=Value(True)),
            default=Value(False), output_field=BooleanField(),
        ),
    )
    if filters['q']:
        query = filters['q']
        products = products.filter(
            Q(name__icontains=query) | Q(description__icontains=query) | Q(brand__icontains=query)
        )
    if filters['categorie']:
        category = filters['categorie']
        products = products.filter(Q(category__slug=category) | Q(collections__slug=category)).distinct()
    if filters['statut'] == 'duplicate':
        products = products.filter(duplicate_of__isnull=False)
    elif filters['statut'] in ('published', 'draft'):
        products = products.filter(is_published=filters['statut'] == 'published')
    flag = {
        'missing_photo': '_needs_photo',
        'missing_brand': '_needs_brand',
        'missing_stock': '_needs_stock',
        'out_of_stock': '_out_of_stock',
    }.get(filters['attention'])
    if flag:
        products = products.filter(**{flag: True})
    products = with_catalog_prices(products)
    sort = filters['tri']
    if sort in ('name', '-name'):
        products = products.alias(_catalog_sort_name=Lower('name'))
        return products.order_by('-_catalog_sort_name' if sort.startswith('-') else '_catalog_sort_name', 'pk')
    if sort in ('price', '-price'):
        products = products.alias(_catalog_sort_name=Lower('name'))
        return products.order_by('-_catalog_min_price' if sort.startswith('-') else '_catalog_min_price',
                                 '_catalog_sort_name', 'pk')
    return products.order_by('-created_at', '-pk')
