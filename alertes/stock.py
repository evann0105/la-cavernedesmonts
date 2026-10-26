"""Low-stock worklist and durable threshold alerts, per size and colour.

Public-page reads never queue mail. The existing background worker observes the
available quantity (physical stock minus reservations), including bulk imports.
"""
import unicodedata

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from connexion.email_verification import email_access_allowed
from core.catalog_access import can_manage_catalog
from core.models import Product
from stocks.models import Variant
from .models import AlertEvent, AlertNotice, AlertSettings, StockWatch
from .services import get_settings


def _fold(value):
    return ''.join(char for char in unicodedata.normalize('NFKD', str(value or '').casefold())
                   if not unicodedata.combining(char))


def _watch(variant):
    try:
        return variant.alert_watch
    except StockWatch.DoesNotExist:
        return None


def _published(variant):
    # An archived category may still contain products visible in the global
    # catalogue. Publication of the product itself is the relevant boundary.
    return variant.product.is_published and variant.product.duplicate_of_id is None


def _threshold(watch, preferences):
    return watch.threshold if watch and watch.threshold is not None else preferences.stock_threshold


def _low(variant, watch, preferences):
    return (_published(variant) and variant.quantity is not None
            and (watch is None or watch.enabled)
            and variant.available <= _threshold(watch, preferences))


def stock_snapshot(query=''):
    """Return live, read-only rows and unfiltered worklist counters.

Only published canonical products are shown. Unknown quantities are shown
separately instead of being treated as zero stock. Optional search accepts
product, brand, size, colour and internal reference, ignoring accents.
"""
    preferences = get_settings()
    variants = Variant.objects.filter(product__is_published=True, product__duplicate_of__isnull=True).select_related(
        'product', 'alert_watch').order_by('product__name', 'size', 'color', 'pk')
    counts = {key: 0 for key in ('low', 'out', 'unknown', 'disabled', 'normal', 'total')}
    rows = []
    words = _fold(str(query or '')[:150]).split()
    missing_products = list(Product.objects.filter(is_published=True, duplicate_of__isnull=True,
        stock_variants__isnull=True).order_by('name', 'pk'))
    missing_products_count = len(missing_products)
    if words:
        missing_products = [product for product in missing_products
            if all(word in _fold(' '.join((product.name, product.brand))) for word in words)]
    for variant in variants:
        watch = _watch(variant)
        enabled = watch is None or watch.enabled
        available = variant.available if variant.quantity is not None else None
        threshold = _threshold(watch, preferences)
        is_low = _low(variant, watch, preferences)
        state = ('disabled' if not enabled else 'unknown' if available is None
                 else 'out' if available == 0 else 'low' if is_low else 'normal')
        labels = {'disabled': 'Alerte désactivée', 'unknown': 'Stock à renseigner',
                  'out': 'Rupture de stock', 'low': 'Stock faible', 'normal': 'Stock suffisant'}
        counts[state] += 1
        counts['total'] += 1
        text = _fold(' '.join((variant.product.name, variant.product.brand,
                              variant.size, variant.color, variant.reference)))
        if words and not all(word in text for word in words):
            continue
        rows.append({'variant': variant, 'variant_id': variant.pk, 'product': variant.product,
                     'title': variant.product.name, 'size': variant.size, 'color': variant.color,
                     'quantity': variant.quantity, 'reserved': variant.reserved, 'available': available,
                     'threshold': threshold, 'override_threshold': watch.threshold if watch else None,
                     'enabled': enabled, 'is_low': is_low, 'state': state, 'status': labels[state],
                     'revision': watch.revision if watch else 1,
                     'url': reverse('stocks:product', args=[variant.product_id]),
                     'adjust_url': reverse('stocks:adjust', args=[variant.product_id, variant.pk])})
    rank = {'out': 0, 'low': 1, 'unknown': 2, 'normal': 3, 'disabled': 4}
    rows.sort(key=lambda row: (rank[row['state']], _fold(row['title']), row['size'], row['color'], row['variant_id']))
    return {'rows': rows, 'low_rows': [row for row in rows if row['is_low']], 'counts': counts,
            'unknown_count': counts['unknown'], 'low_count': counts['low'] + counts['out'],
            'missing_products': missing_products, 'missing_products_count': missing_products_count,
            'settings': preferences}


@transaction.atomic
def update_rule(actor, variant_id, threshold, enabled, revision):
    """Change a variant's alert threshold, never its quantity or price."""
    if not can_manage_catalog(actor) or not email_access_allowed(actor):
        raise PermissionDenied
    if threshold is not None and (type(threshold) is not int or not 0 <= threshold <= 99999):
        raise ValidationError({'threshold': 'Choisissez un seuil entre 0 et 99 999, ou laissez vide pour le seuil général.'})
    if type(enabled) is not bool or type(revision) is not int or revision < 1:
        raise ValidationError('Rechargez les réglages de cette taille et couleur avant d’enregistrer.')
    # Settings first is the shared lock order with the sender and scan worker.
    AlertSettings.objects.get_or_create(pk=1)
    preferences = AlertSettings.objects.select_for_update().get(pk=1)
    try:
        variant = Variant.objects.select_for_update().get(pk=variant_id)
    except (Variant.DoesNotExist, TypeError, ValueError):
        raise ValidationError('Cette taille et couleur n’existe plus. Revenez à la liste des stocks.')
    watch, _ = StockWatch.objects.select_for_update().get_or_create(variant=variant)
    if revision != watch.revision:
        raise ValidationError('Ce seuil a été modifié dans un autre onglet. Rechargez la page avant d’enregistrer.')
    before = {'threshold': watch.threshold, 'enabled': watch.enabled}
    after = {'threshold': threshold, 'enabled': enabled}
    if before == after:
        return watch
    watch.threshold, watch.enabled = threshold, enabled
    watch.revision += 1
    if not _low(variant, watch, preferences):
        watch.was_low = False
    watch.save(update_fields=['threshold', 'enabled', 'revision', 'was_low', 'updated_at'])
    AlertNotice.objects.filter(kind='low_stock', variant=variant, sent_at__isnull=True,
        cancelled_at__isnull=True).update(cancelled_at=timezone.now(), failure_code='obsolete')
    AlertEvent.objects.create(actor=actor, kind='stock_rule', details={
        'variant_id': variant.pk, 'product_id': variant.product_id,
        'label': str(variant), 'before': before, 'after': after})
    return watch


def _payload(variant, watch, preferences):
    threshold = _threshold(watch, preferences)
    quantity = variant.available
    title = 'Une taille et couleur est en rupture.' if quantity == 0 else 'Un stock approche de la rupture.'
    details = (f'{variant.product.name}\nTaille : {variant.size}\nCouleur : {variant.color}\n\n'
               f'Au moment de l’alerte : {quantity} unité(s) disponible(s) à la vente.\n'
               f'Stock physique : {variant.quantity} · Réservé : {variant.reserved}.\n'
               f'Seuil choisi : {threshold} unité(s).\n\n'
               'Consultez le stock actuel dans votre espace de gestion avant de réapprovisionner.')
    return {'recipient': preferences.recipient.strip().lower(), 'settings_revision': preferences.revision,
            'stock_generation': preferences.stock_generation,
            'watch_revision': watch.revision, 'variant_id': variant.pk, 'episode': watch.episode,
            'available': quantity, 'threshold': threshold,
            'name': variant.product.name, 'size': variant.size, 'color': variant.color,
            'subject': 'Stock à surveiller · ' + variant.product.name, 'title': title,
            'intro': 'Le stock disponible a atteint le seuil choisi pour cette taille et cette couleur.',
            'details': details, 'action': 'Voir le stock',
            'url': reverse('stocks:product', args=[variant.product_id])}


def scan_low_stock(limit=500):
    """Observe all variants, queue at most ``limit`` new mail in this run.

    A low episode is rearmed only after observing stock above its threshold.
    While already low, repeated scans retain the same unique notification key.
    Stock-specific generations and rule revisions allow a deliberate new
    destination/threshold to be reflected. Changing paid-order notifications
    alone neither cancels a pending stock notice nor repeats a successful one.
    """
    if type(limit) is not int or limit < 1:
        return 0
    queued = 0
    ids = list(Variant.objects.values_list('pk', flat=True).order_by('pk'))
    for variant_id in ids:
        with transaction.atomic():
            preferences = AlertSettings.objects.select_for_update().filter(pk=1).first() or AlertSettings(pk=1)
            # Quantity editors lock product then variant. Lock only the variant
            # here; the joined product is presentation data and is rechecked by
            # the sender, so this avoids reversing the stock editors' locks.
            variant = Variant.objects.select_for_update(of=('self',)).select_related('product').filter(pk=variant_id).first()
            if variant is None:
                continue
            watch = StockWatch.objects.select_for_update().filter(variant=variant).first()
            low = _low(variant, watch, preferences)
            if not low:
                if watch and watch.was_low:
                    watch.was_low = False
                    watch.save(update_fields=['was_low', 'updated_at'])
                AlertNotice.objects.filter(kind='low_stock', variant=variant, sent_at__isnull=True,
                    cancelled_at__isnull=True).update(cancelled_at=timezone.now(), failure_code='obsolete')
                continue
            if watch is None:
                watch = StockWatch.objects.create(variant=variant)
            if not watch.was_low:
                watch.was_low = True
                watch.episode += 1
                watch.save(update_fields=['was_low', 'episode', 'updated_at'])
            if (not preferences.stock_notifications_enabled or not preferences.recipient or queued >= limit):
                continue
            payload = _payload(variant, watch, preferences)
            key = f'stock-{variant.pk}-e{watch.episode}-g{preferences.stock_generation}-w{watch.revision}'
            notice, created = AlertNotice.objects.get_or_create(key=key,
                defaults={'kind': 'low_stock', 'variant': variant, 'payload': payload})
            if not created and notice.cancelled_at and notice.failure_code == 'obsolete':
                # The sender may have observed recovery/unpublication between
                # worker scans. If stock is low again, that cancelled episode
                # must not suppress a fresh alert indefinitely.
                watch.episode += 1
                watch.save(update_fields=['episode', 'updated_at'])
                payload = _payload(variant, watch, preferences)
                key = f'stock-{variant.pk}-e{watch.episode}-g{preferences.stock_generation}-w{watch.revision}'
                _, created = AlertNotice.objects.get_or_create(key=key,
                    defaults={'kind': 'low_stock', 'variant': variant, 'payload': payload})
            queued += int(created)
    return queued


def stock_notice_is_current(notice, settings=None):
    """Recheck live stock immediately before a queued e-mail is attempted."""
    if notice.kind != 'low_stock' or notice.variant_id is None:
        return False
    preferences = settings or get_settings()
    if (not preferences.stock_notifications_enabled or not preferences.recipient
            or notice.payload.get('recipient') != preferences.recipient.strip().lower()):
        return False
    variant = Variant.objects.select_related('product', 'alert_watch').filter(pk=notice.variant_id).first()
    if variant is None:
        return False
    watch = _watch(variant)
    payload = notice.payload
    return bool(watch and watch.was_low and _low(variant, watch, preferences)
        and payload.get('variant_id') == variant.pk and payload.get('episode') == watch.episode
        and payload.get('stock_generation') == preferences.stock_generation
        and payload.get('watch_revision') == watch.revision
        and payload.get('threshold') == _threshold(watch, preferences))


validate_notice = stock_notice_is_current
