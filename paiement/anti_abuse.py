"""Bound unpaid checkouts across browsers, without collecting raw IP addresses.

Budgets are enforced in the same transaction as stock reservation. A new browser
does not reset an IP budget. Existing checkout fingerprints are resumed before
this admission step, including after a Stripe timeout. Only the payment provider
can release an engaged hold: reaching its local expiry never resets this guard.
"""
from datetime import timedelta
import ipaddress
import math

from django.db import connection
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .models import CheckoutAdmission, Order


WINDOW = timedelta(minutes=30)
RETENTION = timedelta(hours=24)
MIN_IP_INTERVAL = timedelta(seconds=30)
IP_CREATION_LIMIT = 10
IDENTITY_CREATION_LIMIT = 5
MAX_BASKET_VARIANTS = 20
MAX_BASKET_UNITS = 30
# Three different unpaid baskets can coexist on shared Wi-Fi. Holding a variant
# in another browser is refused: extra units belong in the first basket instead.
IP_PENDING_LIMIT = 3
IDENTITY_PENDING_LIMIT = 1
IP_VARIANT_PENDING_LIMIT = 1


class CheckoutLimited(ValueError):
    def __init__(self, retry_after):
        self.retry_after = max(1, int(math.ceil(retry_after)))
        super().__init__('Les tentatives de paiement sont temporairement limitées. '
                         'Votre panier est conservé. Patientez avant de réessayer, '
                         'ou reprenez un paiement déjà ouvert.')


def _digest(kind, value):
    return salted_hmac('checkout-admission:v1', f'{kind}:{value}', algorithm='sha256').hexdigest()


def _ip(value):
    try:
        address = ipaddress.ip_address(value)
        if isinstance(address, ipaddress.IPv6Address):
            if address.ipv4_mapped:
                return str(address.ipv4_mapped)
            # A visitor can cheaply rotate IPv6 addresses inside their /64.
            # Group that local network, as IPv4 visitors behind NAT already are.
            return str(ipaddress.ip_network(f'{address}/64', strict=False))
        return str(address)
    except ValueError:
        # Missing/invalid REMOTE_ADDR shares a strict bucket, never an exemption.
        return 'unknown'


def _lock(keys):
    if not connection.in_atomic_block:
        raise RuntimeError('Une admission de paiement exige une transaction.')
    if connection.vendor == 'postgresql':
        # Stable, ordered transaction locks work across workers and restarts.
        # Hash collisions only serialize unrelated buyers; they cannot bypass a
        # quota. No existing order/stock row is locked by these quota queries.
        numbers = sorted({int(key[:8], 16) - 2**31 for key in keys if key})
        with connection.cursor() as cursor:
            for number in numbers:
                cursor.execute('SELECT pg_advisory_xact_lock(%s, %s)', [604003, number])
    # SQLite already owns its writer lock: reserve() has inserted the new order.


def _wait_for_pending(orders, now):
    expiry = orders.order_by('reservation_expires_at').values_list('reservation_expires_at', flat=True).first()
    # An elapsed/unknown expiry still needs provider reconciliation. Retry in a
    # minute, never assume stock is free or open another payment meanwhile.
    return max(60, (expiry - now).total_seconds()) if expiry else 60


def oversized_basket(snapshot):
    return len(snapshot) > MAX_BASKET_VARIANTS or sum(item['quantity'] for item in snapshot) > MAX_BASKET_UNITS


def validate_basket(snapshot):
    if oversized_basket(snapshot):
        from stocks.reservations import StockUnavailable
        raise StockUnavailable('Une commande en ligne est limitée à 20 tailles et couleurs différentes '
                               'et 30 articles au total. Retirez des articles du panier ou contactez '
                               'la boutique pour une commande plus importante. Votre panier est conservé.')


def admit(order, *, client_ip):
    """Called only for a newly created order, before any reservation is written."""
    if not order.session_key:
        raise ValueError('Une session est nécessaire pour réserver des articles.')
    keys = {'ip_hash': _digest('ip', _ip(client_ip)),
            'session_hash': _digest('session', order.session_key),
            'account_hash': _digest('account', order.user_id) if order.user_id else ''}
    _lock(keys.values())
    now = timezone.now()
    recent = CheckoutAdmission.objects.filter(created_at__gt=now - WINDOW)
    ip_recent = recent.filter(ip_hash=keys['ip_hash'])
    latest = ip_recent.order_by('-created_at').values('created_at', 'session_hash', 'account_hash').first()
    same_buyer = latest and (latest['session_hash'], latest['account_hash']) == (keys['session_hash'], keys['account_hash'])
    # A safely expired/cancelled checkout may be replaced immediately by its own
    # browser (e.g. changing a coupon). Pending/frequency budgets below still
    # apply. A new browser on that IP never gains this convenience.
    if latest and not same_buyer and now < latest['created_at'] + MIN_IP_INTERVAL:
        raise CheckoutLimited((latest['created_at'] + MIN_IP_INTERVAL - now).total_seconds())
    for field, value in keys.items():
        if not value:
            continue
        events = recent.filter(**{field: value})
        limit = IP_CREATION_LIMIT if field == 'ip_hash' else IDENTITY_CREATION_LIMIT
        if events.count() >= limit:
            oldest = events.order_by('created_at').values_list('created_at', flat=True).first()
            raise CheckoutLimited((oldest + WINDOW - now).total_seconds())

    active = Order.objects.filter(status='pending', reservations__state='held').exclude(pk=order.pk).distinct()
    # Legacy holds also count for the account/session, even before this feature
    # was installed. No IP address is invented for historical orders.
    scopes = [(active.filter(checkout_admission__ip_hash=keys['ip_hash']), IP_PENDING_LIMIT),
              (active.filter(session_key=order.session_key), IDENTITY_PENDING_LIMIT)]
    if order.user_id:
        scopes.append((active.filter(user_id=order.user_id), IDENTITY_PENDING_LIMIT))
    for orders, limit in scopes:
        if orders.count() >= limit:
            raise CheckoutLimited(_wait_for_pending(orders, now))

    variant_ids = [item['variant'] for item in order.items]
    overlapping = Order.objects.filter(
        status='pending', checkout_admission__ip_hash=keys['ip_hash'],
        reservations__state='held', reservations__variant_id__in=variant_ids,
    ).exclude(pk=order.pk).distinct()
    if overlapping.count() >= IP_VARIANT_PENDING_LIMIT:
        raise CheckoutLimited(_wait_for_pending(overlapping, now))
    CheckoutAdmission.objects.create(order=order, created_at=now, **keys)


def purge_old_admissions(*, batch_size=500):
    """Bound worker cleanup; preserve every unresolved payment, however old."""
    cutoff = timezone.now() - RETENTION
    expired = CheckoutAdmission.objects.filter(created_at__lt=cutoff).exclude(order__status='pending')
    limit = max(1, min(int(batch_size), 500))
    identifiers = list(expired.order_by('created_at', 'pk').values_list('pk', flat=True)[:limit])
    if not identifiers:
        return 0
    deleted, _ = expired.filter(pk__in=identifiers).delete()
    return deleted
