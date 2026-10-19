"""Catalog prices come from real size/color variants, including stockouts."""
from dataclasses import dataclass
from decimal import Decimal

from django.db.models import DecimalField, F, OuterRef, Subquery
from django.db.models.functions import Coalesce


@dataclass(frozen=True)
class PriceSummary:
    minimum: Decimal
    maximum: Decimal

    @property
    def has_range(self):
        return self.minimum != self.maximum


def product_price_summary(product, variants=None):
    """Use annotations or preloaded variants without issuing per-card queries."""
    if variants is None and hasattr(product, '_catalog_min_price'):
        return PriceSummary(product._catalog_min_price, product._catalog_max_price)
    if variants is None:
        variants = getattr(product, '_prefetched_objects_cache', {}).get('stock_variants')
        if variants is None:
            variants = product.stock_variants.all() if product.pk else []
    default_price = Decimal(str(product.price))
    prices = [Decimal(str(variant.price)) if variant.price is not None else default_price for variant in variants]
    return PriceSummary(min(prices, default=default_price), max(prices, default=default_price))


def with_catalog_prices(products):
    """Keep min/max independent of joins used by category and size filters."""
    from stocks.models import Variant

    decimal = DecimalField(max_digits=10, decimal_places=2)
    prices = Variant.objects.filter(product_id=OuterRef('pk')).annotate(
        catalog_effective_price=Coalesce('price', OuterRef('price'), output_field=decimal),
    )
    return products.annotate(
        _catalog_min_price=Coalesce(
            Subquery(prices.order_by('catalog_effective_price', 'pk').values('catalog_effective_price')[:1], output_field=decimal),
            F('price'), output_field=decimal,
        ),
        _catalog_max_price=Coalesce(
            Subquery(prices.order_by('-catalog_effective_price', 'pk').values('catalog_effective_price')[:1], output_field=decimal),
            F('price'), output_field=decimal,
        ),
    )
