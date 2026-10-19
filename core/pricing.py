"""One pricing clock for catalog, basket and payment; no scheduled job is needed.

A confirmed reference covers price history outside this installation. Recorded
prices may only lower that reference. Observations are recorded by writes, never
by reads, and the migration records the present rather than inventing 30 days.
"""
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.db.models import Case, DecimalField, F, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Coalesce
from django.utils import timezone

CENT = Decimal('0.01')


def money(value):
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def regular_price(product, variant=None):
    price = getattr(variant, 'price', None) if variant is not None else None
    return money(product.price if price is None else price)


@dataclass(frozen=True)
class PriceQuote:
    current: Decimal
    regular: Decimal
    reference: Decimal | None = None
    has_discount: bool = False
    promotion_id: int | None = None
    starts_at: object = None
    ends_at: object = None
    next_boundary: object = None


@dataclass(frozen=True)
class PriceSummary:
    minimum: Decimal
    maximum: Decimal

    @property
    def has_range(self):
        return self.minimum != self.maximum


def discounted_price(reference, kind, value):
    """Return no price for malformed/unusable rows, even if inserted outside forms."""
    try:
        reference, value = money(reference), Decimal(str(value))
        if not reference.is_finite() or not value.is_finite() or reference <= 0 or value <= 0:
            return None
        if kind == 'percent' and value < 100:
            result = reference * (Decimal('100') - value) / Decimal('100')
        elif kind == 'amount':
            result = reference - value
        elif kind == 'fixed':
            result = value
        else:
            return None
        result = money(result)
        return result if CENT <= result < reference else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _variant_key(variant=None):
    return str(variant.pk) if variant is not None else 'product'


class PricingContext:
    def __init__(self, products, variants, promotions, observations, at):
        self.at = at
        self.products = {product.pk: product for product in products}
        self.variants = {}
        self.promotions = {}
        self.observations = {}
        self._period_cache = {}
        for variant in variants:
            self.variants.setdefault(variant.product_id, []).append(variant)
        for promotion in promotions:
            promotion._pricing_target_ids = {str(variant.pk) for variant in promotion.targets.all()}
            self.promotions.setdefault(promotion.product_id, []).append(promotion)
        for observation in observations:
            key = str(observation.variant_id) if observation.variant_id is not None else 'product'
            self.observations.setdefault((observation.product_id, key), []).append(observation)
        for product in products:
            product._pricing_context = self
            product._pricing_at = at
        for variant in variants:
            variant._pricing_context = self
            # A caller may have loaded a distinct Product instance through this FK.
            if variant.product_id in self.products:
                variant._state.fields_cache['product'] = self.products[variant.product_id]

    @staticmethod
    def targets(promotion, key):
        return promotion.all_variants or key in promotion._pricing_target_ids

    @staticmethod
    def period_end(promotion):
        if promotion.cancelled_at is not None:
            return min(promotion.ends_at, promotion.cancelled_at)
        return promotion.ends_at if promotion.enabled else promotion.starts_at

    def observed_minimum(self, product_id, key, start, end):
        """Include the known price still in force at the beginning of the window."""
        observations = self.observations.get((product_id, key), [])
        prices = []
        prior = None
        for observation in observations:
            if observation.observed_at >= end:
                break
            if observation.observed_at < start:
                prior = observation.price
            else:
                prices.append(observation.price)
        if prior is not None:
            prices.append(prior)
        return min(prices) if prices else None

    def periods(self, product_id, key):
        """Resolve previous promotions chronologically, including cancellations.

        A future period is not treated as a price already charged. Its reference
        is resolved when the requested pricing clock reaches its real start.
        """
        cache_key = (product_id, key)
        if cache_key in self._period_cache:
            return self._period_cache[cache_key]
        periods = []
        for promotion in self.promotions.get(product_id, []):
            if promotion.starts_at > self.at or not promotion.reference_confirmed or not self.targets(promotion, key):
                continue
            end = self.period_end(promotion)
            if end <= promotion.starts_at:
                continue
            raw = promotion.reference_prices.get(key) if isinstance(promotion.reference_prices, dict) else None
            try:
                reference = money(raw)
                if not reference.is_finite() or reference <= 0:
                    continue
            except (InvalidOperation, ValueError, TypeError):
                continue
            intended_final = discounted_price(reference, promotion.kind, promotion.value)
            if intended_final is None:
                # A malformed row was never a valid scheduled price.
                continue
            window = promotion.starts_at - timedelta(days=30)
            known = self.observed_minimum(product_id, key, window, promotion.starts_at)
            if known is not None:
                reference = min(reference, known)
            for previous in periods:
                if previous['start'] < promotion.starts_at and previous['end'] > window:
                    reference = min(reference, previous['price'])
            final = discounted_price(reference, promotion.kind, promotion.value)
            if final is None and promotion.kind in ('fixed', 'amount'):
                # Earlier real reductions may lower the legal reference after
                # scheduling. Keep the promised final price, but do not call it
                # a reduction or advertise savings against an invalid reference.
                final, reference = intended_final, None
            if final is not None:
                periods.append({'promotion': promotion, 'start': promotion.starts_at, 'end': end,
                                'reference': reference, 'price': final})
        self._period_cache[cache_key] = periods
        return periods

    def reference_limit(self, product_id, key, starts_at):
        end = min(starts_at, self.at)
        start = starts_at - timedelta(days=30)
        known = self.observed_minimum(product_id, key, start, end)
        for period in self.periods(product_id, key):
            if period['start'] < end and period['end'] > start:
                known = period['price'] if known is None else min(known, period['price'])
        return money(known) if known is not None else None

    def quote(self, product, variant=None, at=None):
        at = at or self.at
        # Usually shared at exactly one request clock. An explicit other clock
        # gets its own period cache but reuses the already loaded database rows.
        if at != self.at:
            context = object.__new__(PricingContext)
            context.__dict__ = {**self.__dict__, 'at': at, '_period_cache': {}}
            return context.quote(product, variant, at)
        regular = regular_price(product, variant)
        key = _variant_key(variant)
        boundaries = []
        for promotion in self.promotions.get(product.pk, []):
            if not promotion.enabled or not promotion.reference_confirmed or not self.targets(promotion, key):
                continue
            for boundary in (promotion.starts_at, self.period_end(promotion)):
                if boundary > at:
                    boundaries.append(boundary)
        next_boundary = min(boundaries, default=None)
        active = [period for period in self.periods(product.pk, key) if period['start'] <= at < period['end']]
        if not active:
            return PriceQuote(regular, regular, next_boundary=next_boundary)
        # Forms reject overlap. If outside code violates it, take one best price;
        # reductions are never multiplied or added together.
        period = min(active, key=lambda item: (min(regular, item['price']), item['promotion'].pk))
        current = min(regular, period['price'])
        has_discount = period['reference'] is not None and current < period['reference']
        return PriceQuote(current, regular, period['reference'], has_discount,
                          period['promotion'].pk, period['start'], period['end'], next_boundary)


def load_pricing_context(products, at=None):
    """Attach one constant-query price context to products and their variants.

    Returns the product list so callers can use it directly. Pass the same `at`
    throughout a checkout to keep date boundaries deterministic.
    """
    from .models import PriceObservation, ProductPromotion
    from stocks.models import Variant

    products = list(products)
    at = at or timezone.now()
    ids = {product.pk for product in products if product.pk is not None}
    if not ids:
        return products
    variants = []
    missing = []
    for product in products:
        cached = getattr(product, '_prefetched_objects_cache', {}).get('stock_variants')
        if cached is None:
            missing.append(product.pk)
        else:
            variants.extend(cached)
    if missing:
        variants.extend(Variant.objects.filter(product_id__in=set(missing)))
    promotions = list(ProductPromotion.objects.filter(product_id__in=ids).prefetch_related('targets').order_by('starts_at', 'pk'))
    observations = list(PriceObservation.objects.filter(product_id__in=ids).order_by('observed_at', 'pk'))
    PricingContext(products, variants, promotions, observations, at)
    return products


# Explicit alias for callers using a queryset/list of products.
prefetch_pricing = load_pricing_context


def price_quote(product, variant=None, at=None):
    at = at or timezone.now()
    context = getattr(product, '_pricing_context', None) or getattr(variant, '_pricing_context', None)
    if context is None and product.pk is not None:
        load_pricing_context([product], at=at)
        context = product._pricing_context
    if context is None:
        regular = regular_price(product, variant)
        return PriceQuote(regular, regular)
    return context.quote(product, variant, at=at)


def reference_price_limit(product, variant=None, starts_at=None):
    """Lowest known price before this start; None never means a fabricated past."""
    if product.pk is None:
        return None
    if not hasattr(product, '_pricing_context'):
        load_pricing_context([product])
    return product._pricing_context.reference_limit(product.pk, _variant_key(variant), starts_at or timezone.now())


def product_price_summary(product, variants=None, at=None):
    if variants is None and hasattr(product, '_catalog_min_price'):
        return PriceSummary(product._catalog_min_price, product._catalog_max_price)
    if variants is None:
        variants = getattr(product, '_prefetched_objects_cache', {}).get('stock_variants')
        if variants is None:
            variants = product.stock_variants.all() if product.pk else []
    variants = list(variants)
    if product.pk is not None:
        if not hasattr(product, '_pricing_context'):
            # Reuse the exact variants already read by a detail page.
            product._prefetched_objects_cache = {**getattr(product, '_prefetched_objects_cache', {}), 'stock_variants': variants}
            load_pricing_context([product], at=at)
        prices = [price_quote(product, variant, at).current for variant in variants]
        fallback = price_quote(product, at=at).current
    else:
        prices = [regular_price(product, variant) for variant in variants]
        fallback = regular_price(product)
    return PriceSummary(min(prices, default=fallback), max(prices, default=fallback))


def _regular_catalog_prices(products):
    from stocks.models import Variant
    decimal = DecimalField(max_digits=10, decimal_places=2)
    prices = Variant.objects.filter(product_id=OuterRef('pk')).annotate(
        catalog_effective_price=Coalesce('price', OuterRef('price'), output_field=decimal),
    )
    return products.annotate(
        _catalog_min_price=Coalesce(Subquery(prices.order_by('catalog_effective_price', 'pk').values('catalog_effective_price')[:1], output_field=decimal), F('price'), output_field=decimal),
        _catalog_max_price=Coalesce(Subquery(prices.order_by('-catalog_effective_price', 'pk').values('catalog_effective_price')[:1], output_field=decimal), F('price'), output_field=decimal),
    )


def with_catalog_prices(products, at=None):
    """Composable min/max/sorting with one bulk price calculation, never N+1.

    The usual non-promotional catalog keeps its existing SQL subqueries. For
    active periods, a bounded set of bulk queries resolves legal references and
    emits CASE annotations so database filtering, ordering and paging still work.
    """
    from .models import Product, ProductPromotion
    at = at or timezone.now()
    active_ids = list(ProductPromotion.objects.filter(
        Q(enabled=True, cancelled_at__isnull=True) | Q(cancelled_at__gt=at),
        product_id__in=products.values('pk'), reference_confirmed=True,
        starts_at__lte=at, ends_at__gt=at,
    ).values_list('product_id', flat=True).distinct())
    regular = _regular_catalog_prices(products)
    if not active_ids:
        return regular
    promoted = list(Product.objects.filter(pk__in=active_ids))
    load_pricing_context(promoted, at=at)
    decimal = DecimalField(max_digits=10, decimal_places=2)
    minima, maxima = [], []
    for product in promoted:
        summary = product_price_summary(product, product._pricing_context.variants.get(product.pk, []), at)
        minima.append(When(pk=product.pk, then=Value(summary.minimum, output_field=decimal)))
        maxima.append(When(pk=product.pk, then=Value(summary.maximum, output_field=decimal)))
    return regular.annotate(
        _catalog_min_price=Case(*minima, default=F('_catalog_min_price'), output_field=decimal),
        _catalog_max_price=Case(*maxima, default=F('_catalog_max_price'), output_field=decimal),
    )


def record_product_price(product):
    from .models import PriceObservation
    from stocks.models import Variant
    PriceObservation.objects.create(product=product, price=money(product.price))
    inherited = Variant.objects.filter(product=product, price__isnull=True).values_list('pk', flat=True)
    PriceObservation.objects.bulk_create([
        PriceObservation(product=product, variant_id=variant_id, price=money(product.price))
        for variant_id in inherited
    ])


def record_variant_price(variant):
    from .models import PriceObservation, Product
    # Avoid a stale select_related Product when another write changed its price.
    price = variant.price
    if price is None:
        price = Product.objects.values_list('price', flat=True).get(pk=variant.product_id)
    PriceObservation.objects.create(product_id=variant.product_id, variant=variant, price=money(price))
