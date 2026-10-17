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


def reserve(session_key, user_id, snapshot):
    fingerprint = hashlib.sha256(json.dumps([session_key,user_id,snapshot],sort_keys=True).encode()).hexdigest()
    with transaction.atomic():
        order, created = Order.objects.get_or_create(checkout_fingerprint=fingerprint, defaults={'session_key':session_key,'user_id':user_id,'items':snapshot,'subtotal':sum(i['unit_amount']*i['quantity'] for i in snapshot),'reservation_expires_at':timezone.now()+timedelta(minutes=35)})
        order = Order.objects.select_for_update().get(pk=order.pk)
        if not created:
            return order
        products = {p.pk:p for p in Product.objects.select_for_update().filter(pk__in=[i['product'] for i in snapshot]).order_by('pk')}
        variants = {v.pk:v for v in Variant.objects.select_for_update().filter(pk__in=[i.get('variant') for i in snapshot]).order_by('pk')}
        if len(variants) != len(snapshot):
            raise StockUnavailable('Choisissez une variante valide pour chaque article.')
        for item in snapshot:
            variant = variants.get(item.get('variant'))
            product = products.get(item['product'])
            if not variant or variant.product_id != item['product'] or not product or not product.is_published or product.availability == 'unavailable' or int(product.price*100) != item['unit_amount'] or (variant.size,variant.color)!=(item['size'],item['color']) or not 1 <= item['quantity'] <= 10 or variant.available < item['quantity']:
                raise StockUnavailable('Une taille, une couleur, un prix ou un stock a changé. Vérifiez votre panier.')
            variant.reserved += item['quantity'];variant.revision += 1
            variant.save(update_fields=['reserved','revision','updated_at'])
            Reservation.objects.create(order=order,variant=variant,quantity=item['quantity'])
        return order


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
