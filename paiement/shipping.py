"""Server delivery quotes, with legacy attempts preserved until their expiry."""
from decimal import Decimal, InvalidOperation
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

COUNTRY_SESSION_KEY = 'checkout_shipping_country'
DISPLAYED_SESSION_KEY = 'checkout_displayed_shipping'


def _threshold(value, allow_zero=False):
    try:
        value = Decimal(str(value))
        if not value.is_finite() or value < 0 or (not allow_zero and value == 0) or value != value.quantize(Decimal('0.01')):
            raise InvalidOperation
    except (InvalidOperation, ValueError, TypeError):
        raise ImproperlyConfigured('Le seuil de livraison offerte doit être un montant positif en euros, avec deux décimales au maximum.')
    return value.quantize(Decimal('0.01'))


def free_shipping_threshold():
    from boutique.shipping import configuration
    config = configuration()
    return _threshold(config['free_shipping_threshold'], allow_zero=True) if config['managed'] else _threshold(settings.FREE_SHIPPING_THRESHOLD_EUR)


def shipping_summary(subtotal):
    # Kept deliberately stable for legacy carts and payment attempts.
    threshold = _threshold(settings.FREE_SHIPPING_THRESHOLD_EUR)
    return {'threshold': threshold, 'remaining': max(Decimal('0.00'), threshold - subtotal),
            'is_free': subtotal >= threshold}


def shipping_option(subtotal_cents, country=None, snapshot=None):
    if snapshot is None and country is not None:
        from boutique.shipping import quote
        snapshot = quote(subtotal_cents, country)
    if snapshot and snapshot.get('managed'):
        option = {'type': 'fixed_amount', 'display_name': snapshot['service_name'],
                  'fixed_amount': {'amount': snapshot['amount'], 'currency': 'eur'},
                  'delivery_estimate': {
                      'minimum': {'unit': 'business_day', 'value': snapshot['delivery_min_days']},
                      'maximum': {'unit': 'business_day', 'value': snapshot['delivery_max_days']},
                  }}
        return {'shipping_rate_data': option}
    if subtotal_cents >= int(_threshold(settings.FREE_SHIPPING_THRESHOLD_EUR) * 100):
        return {'shipping_rate_data': {'type': 'fixed_amount', 'display_name': 'Livraison standard offerte',
                'fixed_amount': {'amount': 0, 'currency': 'eur'}}}
    return {'shipping_rate': settings.STRIPE_SHIPPING_RATE}


def shipping_ready():
    from boutique.shipping import configuration, ready
    config = configuration()
    return ready(config) if config['managed'] else bool(settings.STRIPE_SHIPPING_RATE)


def request_context(request, subtotal, discount=Decimal('0'), order=None):
    from boutique.shipping import configuration, quote, ShippingUnavailable
    config = configuration()
    if not config['managed']:
        return {'shipping': shipping_summary(subtotal), 'shipping_quote': None, 'shipping_error': ''}
    countries = list(config['countries'])
    wanted = request.session.get(COUNTRY_SESSION_KEY, '')
    frozen = getattr(order, 'shipping_snapshot', {}) if order is not None else {}
    # A payment already being prepared retains its exact offer. An explicit
    # destination change is cancelled/reconciled before choosing a new quote.
    if (frozen and order.status == 'pending' and order.subtotal == int(subtotal * 100)
            and wanted in ('', frozen.get('country'))):
        quoted, error = frozen, ''
        if frozen['country'] not in {code for code, label in countries}:
            countries.append((frozen['country'], frozen['country_label']))
    else:
        if not wanted:
            wanted = 'FR' if any(code == 'FR' for code, label in countries) else countries[0][0] if len(countries) == 1 else ''
        try:
            quoted, error = quote(int(subtotal * 100), wanted, config=config), ''
        except ShippingUnavailable as exc:
            quoted, error = None, str(exc)
    shipping = {
        'managed': True, 'countries': countries,
        'country': quoted['country'] if quoted else wanted,
        'error': error, 'notice': quoted.get('public_notice', '') if quoted else config.get('public_notice', ''),
        'threshold': None, 'remaining': None, 'is_free': False,
    }
    if quoted:
        threshold = Decimal(quoted['threshold']) / 100 if quoted.get('threshold') is not None else None
        shipping.update(quoted)
        shipping.update(threshold=threshold, amount_eur=Decimal(quoted['amount']) / 100,
                        total=subtotal - discount + Decimal(quoted['amount']) / 100,
                        remaining=max(Decimal('0'), threshold - subtotal) if threshold is not None else None)
    return {'shipping': shipping, 'shipping_quote': quoted, 'shipping_error': error}


def request_shipping_cost(request, subtotal_cents, order=None):
    data = request_context(request, Decimal(subtotal_cents) / 100, order=order)
    value = data['shipping_quote']
    return value['amount'] if value else None


def snapshot_delivery_text(snapshot):
    amount = f"{snapshot['amount'] / 100:.2f}".replace('.', ',')
    text = (f"{snapshot['service_name']} vers {snapshot['country_label']}, par {snapshot['carrier_name']}. "
            f"Frais de livraison pour cette commande : {amount} €. "
            f"Délai annoncé, préparation comprise : {snapshot['delivery_min_days']} à {snapshot['delivery_max_days']} jours ouvrés après confirmation du paiement.")
    if snapshot.get('threshold') is not None:
        threshold = f"{snapshot['threshold'] / 100:.2f}".replace('.', ',')
        text += f' Livraison offerte dès {threshold} € d’articles, avant code client.'
    if snapshot.get('public_notice'):
        text += ' ' + snapshot['public_notice']
    return text


def validate_snapshot(snapshot, subtotal_cents):
    from boutique.shipping import quote, ShippingUnavailable
    if not isinstance(snapshot, dict) or snapshot.get('managed') is not True:
        raise ShippingUnavailable('Choisissez une destination de livraison et vérifiez les frais avant de payer.')
    canonical = quote(subtotal_cents, snapshot.get('country'))
    if snapshot != canonical:
        raise ShippingUnavailable('Les conditions de livraison ont changé. Vérifiez le nouveau récapitulatif avant de payer.')
    return canonical


def payment_matches_shipping(order, session):
    snapshot = order.shipping_snapshot
    if not snapshot:
        return True
    amount = (session.get('total_details') or {}).get('amount_shipping')
    address = ((session.get('collected_information') or {}).get('shipping_details')
               or session.get('shipping_details') or {}).get('address') or {}
    return (type(amount) is int and amount == snapshot['amount']
            and address.get('country') == snapshot['country'])
