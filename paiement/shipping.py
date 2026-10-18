"""One delivery threshold for the cart, contract and persisted Checkout payload."""
from decimal import Decimal, InvalidOperation
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def free_shipping_threshold():
    try:
        value = Decimal(str(settings.FREE_SHIPPING_THRESHOLD_EUR))
        if not value.is_finite() or value <= 0 or value != value.quantize(Decimal('0.01')):
            raise InvalidOperation
    except (InvalidOperation, ValueError, TypeError):
        raise ImproperlyConfigured('FREE_SHIPPING_THRESHOLD_EUR doit être un montant positif en euros, avec deux décimales au maximum.')
    return value.quantize(Decimal('0.01'))


def shipping_summary(subtotal):
    threshold = free_shipping_threshold()
    return {'threshold': threshold, 'remaining': max(Decimal('0.00'), threshold - subtotal),
            'is_free': subtotal >= threshold}


def shipping_option(subtotal_cents):
    # Use the immutable merchandise subtotal, before codes entered at Stripe.
    if subtotal_cents >= int(free_shipping_threshold() * 100):
        return {'shipping_rate_data': {
            'type': 'fixed_amount', 'display_name': 'Livraison standard offerte',
            'fixed_amount': {'amount': 0, 'currency': 'eur'},
        }}
    return {'shipping_rate': settings.STRIPE_SHIPPING_RATE}
