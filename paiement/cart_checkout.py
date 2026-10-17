"""Reconcile a cart with its payment before changing or charging it again."""
from django.contrib import messages
from django.db import transaction
from .models import Order
from stocks.reservations import finish_locked, StockUnavailable


def current_order(request):
    from django.core.exceptions import ValidationError
    identifier = request.session.get('checkout_order')
    if not identifier:
        return None
    try:
        return Order.objects.filter(pk=identifier,
                                    session_key=request.session.session_key or '').first()
    except (ValidationError, ValueError):
        return None


def sync_paid_cart(request):
    order = current_order(request)
    if order is None or order.status != 'paid':
        return None
    cart = request.session.get('cart', {}).copy()
    for item in order.items:
        key = f"variant:{item['variant']}" if item.get('variant') else f"{item['product']}:{item['size']}"
        if key in cart:
            remaining = cart[key]['quantity'] - item['quantity']
            if remaining > 0:
                cart[key] = {**cart[key], 'quantity':remaining}
            else:
                del cart[key]
    request.session['cart'] = cart
    request.session.pop('checkout_order', None)
    return order


def release_for_cart_change(request):
    """Never release stock while a Stripe session could still collect payment.

    If payment won the race with cancellation, consume its cart instead and tell
    the caller not to apply the now-obsolete cart change.
    """
    import stripe
    from django.conf import settings
    from .stock_checkout import get_session, settle
    from .line_totals import payment_confirmed
    order = current_order(request)
    if order is None:
        return None
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order.pk)
        if order.status == 'pending':
            if not order.checkout_payload and not order.stripe_session_id:
                # No request has ever been sent to the payment provider.
                finish_locked(order, False)
                order.status = 'expired'
                order.save()
            else:
                session = get_session(order.pk)
                if session is not None and session.get('status') == 'open':
                    try:
                        session = stripe.checkout.Session.expire(session['id'], api_key=settings.STRIPE_SECRET_KEY)
                    except stripe.StripeError:
                        # It may have completed during cancellation. Retrieve,
                        # never infer failure or release on a network timeout.
                        session = stripe.checkout.Session.retrieve(session['id'], api_key=settings.STRIPE_SECRET_KEY)
                if session is not None:
                    if not (payment_confirmed(session) or
                            (session.get('status') == 'expired' and session.get('payment_status') == 'unpaid')):
                        raise StockUnavailable('Le paiement est encore en cours de vérification. Réessayez dans quelques instants ; votre panier est conservé.')
                    if not settle(session):
                        raise StockUnavailable('Le paiement doit être vérifié avant de modifier le panier.')
                order.refresh_from_db()
        if order.status not in ('paid', 'expired'):
            raise StockUnavailable('Le paiement doit être vérifié avant de modifier le panier.')
    if order.status == 'paid':
        sync_paid_cart(request)
        messages.info(request, 'Votre paiement est confirmé. Les articles achetés ont été retirés du panier.')
        return order
    request.session.pop('checkout_order', None)
    return None
