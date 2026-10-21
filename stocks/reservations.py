"""Stock transitions are serialized by PostgreSQL locks, never by browser state."""
import hashlib
import json
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from core.models import Product
from paiement.models import Order
from .models import Variant, Reservation


class StockUnavailable(ValueError):
    pass


def reserve(session_key, user_id, snapshot, benefit=None, benefit_quote=None, shipping_quote=None):
    identity = [session_key, user_id, snapshot]
    if shipping_quote is not None:
        identity.append({'shipping': shipping_quote})
    if benefit is not None:
        # An existing hold keeps its original conditions even if the seller
        # resaves the offer without changing it during the payment attempt.
        revision = benefit_quote['revision'] if benefit_quote is not None else benefit.updated_at.isoformat()
        identity.append([benefit.pk, revision])
    fingerprint = hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    with transaction.atomic():
        order, created = Order.objects.get_or_create(checkout_fingerprint=fingerprint, defaults={'session_key':session_key,'user_id':user_id,'items':snapshot,'subtotal':sum(i['unit_amount']*i['quantity'] for i in snapshot),'shipping_snapshot':shipping_quote or {},'reservation_expires_at':timezone.now()+timedelta(minutes=35)})
        order = Order.objects.select_for_update().get(pk=order.pk)
        if not created:
            if benefit is not None:
                from avoirs.services import reserve_locked
                from django.contrib.auth import get_user_model
                redemption = reserve_locked(order, benefit, get_user_model().objects.get(pk=user_id))
                _check_benefit_quote(redemption, benefit_quote, order)
            return order
        from boutique.shipping import configuration as delivery_configuration, ShippingUnavailable
        if shipping_quote is not None or delivery_configuration()['managed']:
            from paiement.shipping import validate_snapshot
            try:
                validate_snapshot(shipping_quote, order.subtotal)
            except ShippingUnavailable as error:
                raise StockUnavailable(str(error)) from error
            from paiement.benefits import validate_discounted_total
            from avoirs.services import BenefitError
            if benefit is None:
                try:
                    validate_discounted_total(order.subtotal, 0, order.shipping_snapshot['amount'])
                except BenefitError as error:
                    raise StockUnavailable(str(error)) from error
        products = {p.pk:p for p in Product.objects.select_for_update().filter(pk__in=[i['product'] for i in snapshot]).order_by('pk')}
        variants = {v.pk:v for v in Variant.objects.select_for_update().filter(pk__in=[i.get('variant') for i in snapshot]).order_by('pk')}
        from core.pricing import load_pricing_context, price_quote
        pricing_at = timezone.now()
        load_pricing_context(products.values(), at=pricing_at)
        if len(variants) != len(snapshot):
            raise StockUnavailable('Choisissez une variante valide pour chaque article.')
        for item in snapshot:
            variant = variants.get(item.get('variant'))
            product = products.get(item['product'])
            if variant and product and variant.product_id == product.pk:
                # Use the locked Product for a current, race-safe fallback price.
                variant.product = product
            if not variant or variant.product_id != item['product'] or not product or not product.is_published or product.availability == 'unavailable' or int(price_quote(product, variant, at=pricing_at).current*100) != item['unit_amount'] or (variant.size,variant.color)!=(item['size'],item['color']) or not 1 <= item['quantity'] <= 10 or variant.available < item['quantity']:
                raise StockUnavailable('Une taille, une couleur, un prix ou un stock a changé. Vérifiez votre panier.')
            variant.reserved += item['quantity'];variant.revision += 1
            variant.save(update_fields=['reserved','revision','updated_at'])
            Reservation.objects.create(order=order,variant=variant,quantity=item['quantity'])
        if benefit is not None:
            from avoirs.services import reserve_locked
            from django.contrib.auth import get_user_model
            redemption = reserve_locked(order, benefit, get_user_model().objects.get(pk=user_id))
            _check_benefit_quote(redemption, benefit_quote, order)
        return order


def _check_benefit_quote(redemption, expected, order):
    from paiement.benefits import validate_discounted_total
    validate_discounted_total(redemption.snapshot['subtotal'], redemption.discount_amount,
                              shipping_cents=order.shipping_snapshot.get('amount'))
    if expected is not None and (redemption.discount_amount != expected['discount_amount'] or
            redemption.snapshot['benefit_revision'] != expected['revision']):
        from avoirs.services import BenefitError
        raise BenefitError('Les conditions du code ont changé. Vérifiez le récapitulatif avant de payer.')


def finish_locked(order, sold):
    """Caller holds the order lock. An elapsed clock alone never releases a hold."""
    holds=list(order.reservations.filter(state='held').order_by('variant_id'))
    variants={v.pk:v for v in Variant.objects.select_for_update().filter(pk__in=[h.variant_id for h in holds]).order_by('pk')}
    for hold in holds:
        variant=variants[hold.variant_id]
        if variant.reserved < hold.quantity or variant.quantity is None or variant.quantity < hold.quantity:
            raise StockUnavailable('Réservation incohérente : contrôle du stock nécessaire.')
        variant.reserved -= hold.quantity
        if sold:variant.quantity -= hold.quantity
        variant.revision += 1
        variant.save(update_fields=['quantity','reserved','revision','updated_at'])
        hold.state='sold' if sold else 'released';hold.save(update_fields=['state'])
    order.checkout_fingerprint=None
    if not sold:
        from avoirs.services import release_locked
        release_locked(order)
