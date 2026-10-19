"""Frozen multi-product selections; every price is checked again before writing."""
from decimal import Decimal

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from stocks.models import Variant
from .catalog_filters import with_article_type
from .models import Product, ProductPromotion, SaleCampaign
from .pricing import load_pricing_context, reference_price_limit, regular_price
from .product_options import color_key
from .promotion_forms import ProductPromotionForm

PREVIEW_SALT = 'core.sales.selection.v1'
CANCEL_SALT = 'core.sales.cancel.v1'


def matching_rows(criteria, products=None):
    """AND criteria on one concrete variant, never across unrelated variants."""
    queryset = with_article_type(Product.objects.all())
    if criteria.get('publication', 'published') == 'published':
        queryset = queryset.filter(is_published=True)
    if criteria.get('categories'):
        queryset = queryset.filter(Q(category_id__in=criteria['categories']) | Q(collections__pk__in=criteria['categories']))
    if criteria.get('article_type'):
        queryset = queryset.filter(resolved_type=criteria['article_type'])
    if criteria.get('q'):
        query = criteria['q']
        queryset = queryset.filter(Q(name__icontains=query) | Q(brand__icontains=query) | Q(model_reference__icontains=query))
    if products is None:
        products = list(queryset.distinct().order_by('name', 'pk').prefetch_related('stock_variants', 'collections', 'category'))
    else:
        eligible_ids = set(queryset.filter(pk__in=[p.pk for p in products]).values_list('pk', flat=True))
        products = [product for product in products if product.pk in eligible_ids]
    minimum = Decimal(criteria['price_min']) if criteria.get('price_min') else None
    maximum = Decimal(criteria['price_max']) if criteria.get('price_max') else None
    rows = []
    for product in products:
        variants = list(product.stock_variants.all())
        targets = []
        for variant in variants or [None]:
            if criteria.get('color') and (variant is None or color_key(variant.color) != color_key(criteria['color'])):
                continue
            if criteria.get('size') and (variant is None or color_key(variant.size) != color_key(criteria['size'])):
                continue
            stock = criteria.get('stock', 'all')
            if stock == 'available' and (variant is None or variant.available <= 0):
                continue
            if stock == 'zero' and (variant is None or variant.quantity is None or variant.available > 0):
                continue
            price = regular_price(product, variant)
            if minimum is not None and price < minimum or maximum is not None and price > maximum:
                continue
            targets.append({'key': f'v{variant.pk}' if variant else f'p{product.pk}',
                'product': product, 'variant': variant,
                'label': f'{variant.size} · {variant.color}' if variant else 'Prix du produit', 'regular_price': price})
        if targets:
            prices = [target['regular_price'] for target in targets]
            rows.append({'product': product, 'targets': targets, 'target_count': len(targets),
                'min_price': min(prices), 'max_price': max(prices)})
    return rows


def snapshot_rows(rows):
    return [{'product': row['product'].pk, 'targets': [
        {'key': target['key'], 'price': str(target['regular_price']),
         'size': target['variant'].size if target['variant'] else '',
         'color': target['variant'].color if target['variant'] else ''}
        for target in sorted(row['targets'], key=lambda target: target['key'])]} for row in sorted(rows, key=lambda row: row['product'].pk)]


def sign_preview(rows, criteria, user):
    return signing.dumps({'version': 1, 'user': user.pk, 'criteria': criteria, 'rows': snapshot_rows(rows)}, salt=PREVIEW_SALT, compress=True)


def read_preview(token, user):
    try:
        data = signing.loads(token, salt=PREVIEW_SALT, max_age=7200)
        if data.get('version') != 1 or data.get('user') != user.pk or not data.get('rows') or len(data['rows']) > 1000:
            raise ValueError
        if len({row['product'] for row in data['rows']}) != len(data['rows']):
            raise ValueError
        return data
    except (signing.BadSignature, TypeError, ValueError, KeyError, AttributeError):
        raise ValidationError('Cet aperçu a expiré ou a changé. Sélectionnez à nouveau les articles avant de valider.')


def selection_from_snapshot(snapshot, lock=False):
    ids = sorted(row['product'] for row in snapshot['rows'])
    queryset = Product.objects.filter(pk__in=ids).order_by('pk')
    if lock:
        queryset = queryset.select_for_update()
    products = list(queryset)
    if len(products) != len(ids):
        raise ValidationError('Un article de la sélection a été retiré. Recommencez l’aperçu.')
    variant_qs = Variant.objects.filter(product_id__in=ids).order_by('product_id', 'pk')
    if lock:
        variant_qs = variant_qs.select_for_update()
    variants = list(variant_qs)
    grouped = {}
    for variant in variants:
        grouped.setdefault(variant.product_id, []).append(variant)
    for product in products:
        product._prefetched_objects_cache = {'stock_variants': grouped.get(product.pk, [])}
    rows = matching_rows(snapshot['criteria'], products=products)
    if snapshot_rows(rows) != snapshot['rows']:
        raise ValidationError('Le catalogue, les prix ou les déclinaisons ont changé depuis l’aperçu. Recommencez la sélection pour vérifier les bons articles.')
    return rows


def reference_targets(rows, now=None):
    now = now or timezone.now()
    load_pricing_context([row['product'] for row in rows], at=now)
    targets = []
    for row in rows:
        for target in row['targets']:
            known = reference_price_limit(target['product'], target['variant'], starts_at=now)
            targets.append({**target, 'known_reference': known,
                'reference': min(target['regular_price'], known) if known is not None else target['regular_price']})
    return targets


def _product_forms(form, rows, now, promotions):
    per_product = []
    data = form.cleaned_data
    for row in rows:
        product = row['product']
        variants = list(product.stock_variants.all())
        submitted = {name: data.get(name) for name in ('start_mode', 'starts_at', 'ends_at', 'kind', 'value', 'reference_confirmed')}
        submitted['scope'] = 'variants' if variants else 'all'
        submitted['targets'] = [str(target['variant'].pk) for target in row['targets'] if target['variant']]
        for target in row['targets']:
            key = str(target['variant'].pk) if target['variant'] else 'product'
            submitted[f'reference_{key}'] = data.get(f"ref_{target['key']}")
        child = ProductPromotionForm(submitted, product=product, variants=variants,
            promotions=[promo for promo in promotions if promo.product_id == product.pk], now=now)
        if not child.is_valid():
            for name, errors in child.errors.items():
                variant_key = name.removeprefix('reference_') if name.startswith('reference_') else None
                parent_name = f'ref_v{variant_key}' if variant_key and variant_key != 'product' else f'ref_p{product.pk}' if variant_key else name
                if parent_name not in form.fields:
                    parent_name = None
                for error in errors:
                    form.add_error(parent_name, f'{product.name} : {error}')
        per_product.append(child)
    return per_product


@transaction.atomic
def create_campaign(form, snapshot, user):
    rows = selection_from_snapshot(snapshot, lock=True)
    # Match the same lock order as checkout and the existing product screen.
    ids = sorted(row['product'].pk for row in rows)
    promotions = list(ProductPromotion.objects.select_for_update().filter(product_id__in=ids).order_by('product_id', 'pk').prefetch_related('targets'))
    now = timezone.now()
    load_pricing_context([row['product'] for row in rows], at=now)
    if not form.is_valid():
        return None
    children = _product_forms(form, rows, now, promotions)
    if form.errors:
        return None
    campaign = SaleCampaign.objects.create(title=form.cleaned_data['title'], criteria=snapshot['criteria'])
    for child in children:
        promotion = child.save()
        promotion.campaign = campaign
        promotion.save(update_fields=['campaign', 'updated_at'])
    return campaign


def cancellation_token(campaign, promotions, user):
    return signing.dumps({'user': user.pk, 'campaign': campaign.pk, 'revision': campaign.revision,
        'promotions': {str(p.pk): p.revision for p in promotions}}, salt=CANCEL_SALT, compress=True)


@transaction.atomic
def cancel_campaign(pk, revision, token, user):
    # Read membership first, then lock products in the common global order.
    product_ids = list(ProductPromotion.objects.filter(campaign_id=pk).order_by('product_id').values_list('product_id', flat=True).distinct())
    list(Product.objects.select_for_update().filter(pk__in=product_ids).order_by('pk'))
    list(Variant.objects.select_for_update().filter(product_id__in=product_ids).order_by('product_id', 'pk'))
    promotions = list(ProductPromotion.objects.select_for_update().filter(campaign_id=pk).order_by('product_id', 'pk'))
    campaign = SaleCampaign.objects.select_for_update().get(pk=pk)
    try:
        expected = signing.loads(token, salt=CANCEL_SALT, max_age=86400)
        if (expected.get('user') != user.pk or expected.get('campaign') != campaign.pk
                or expected.get('revision') != campaign.revision or int(revision) != campaign.revision
                or expected.get('promotions') != {str(p.pk): p.revision for p in promotions}):
            raise ValueError
    except (signing.BadSignature, ValueError, TypeError, AttributeError):
        raise ValidationError('Cette opération a changé depuis l’ouverture de la page. Vérifiez son état avant de recommencer.')
    now = timezone.now()
    changed = []
    for promotion in promotions:
        if promotion.enabled and promotion.ends_at > now:
            promotion.enabled = False
            promotion.cancelled_at = now
            promotion.revision += 1
            promotion.save(update_fields=['enabled', 'cancelled_at', 'revision', 'updated_at'])
            changed.append(promotion)
    if changed:
        campaign.revision += 1
        campaign.save(update_fields=['revision'])
    return campaign, changed
