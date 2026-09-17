from django.utils.translation import gettext as _
import logging
import stripe
from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST
from core.cart import lines, total
from .models import Order

logger = logging.getLogger(__name__)


def ready():
    return all((settings.PAYMENTS_ENABLED, settings.SHOP_READY, settings.STRIPE_SECRET_KEY,
                settings.STRIPE_WEBHOOK_SECRET, settings.STRIPE_SHIPPING_RATE,
                settings.DEBUG or settings.SITE_URL.startswith('https://')))


@require_http_methods(['GET', 'POST'])
def checkout(request):
    items = lines(request)
    if not items:
        return redirect('core:cart')
    sizes_valid = all(i['size'] in i['product'].size_options for i in items)
    enabled = ready() and sizes_valid
    if request.method == 'POST':
        if not enabled:
            messages.info(request, _('Le paiement en ligne n’est pas encore disponible pour ce panier. Contactez-nous pour préparer votre commande.'))
            return redirect('paiement:checkout')
        if not request.session.session_key:
            request.session.save()
        snapshot = [{'product': i['product'].pk, 'name': i['product'].name, 'size': i['size'], 'quantity': i['quantity'], 'unit_amount': int(i['product'].price * 100)} for i in items]
        # Reuse a pending order to give retries and double-clicks the same Stripe idempotency key.
        order = Order.objects.filter(id=request.session.get('checkout_order'), session_key=request.session.session_key, status='pending').first() if request.session.get('checkout_order') else None
        if not order or order.items != snapshot:
            order = Order.objects.create(session_key=request.session.session_key, items=snapshot, subtotal=int(total(items)*100))
            request.session['checkout_order'] = str(order.id)
        try:
            if order.stripe_session_id:
                session = stripe.checkout.Session.retrieve(order.stripe_session_id, api_key=settings.STRIPE_SECRET_KEY)
                if session.status == 'complete':
                    return redirect('paiement:confirmation', order_id=order.id)
                if session.status == 'expired':
                    order = Order.objects.create(session_key=request.session.session_key, items=snapshot, subtotal=int(total(items)*100))
                    request.session['checkout_order'] = str(order.id)
                    session = None
            else:
                session = None
            if session is None:
                session = stripe.checkout.Session.create(
                    api_key=settings.STRIPE_SECRET_KEY,
                    idempotency_key=f'order-{order.id}',
                    mode='payment', payment_method_types=['card'], locale=getattr(request, 'LANGUAGE_CODE', 'fr'),
                    line_items=[{'price_data': {'currency': 'eur', 'unit_amount': i['unit_amount'], 'product_data': {'name': f"{i['name']} — {i['size']}"}}, 'quantity': i['quantity']} for i in snapshot],
                    shipping_address_collection={'allowed_countries': ['FR']},
                    shipping_options=[{'shipping_rate': settings.STRIPE_SHIPPING_RATE}],
                    billing_address_collection='required',
                    success_url=settings.SITE_URL + reverse('paiement:confirmation', args=[order.id]),
                    cancel_url=settings.SITE_URL + reverse('core:cart'),
                    client_reference_id=str(order.id), metadata={'order_id': str(order.id)},
                )
                order.stripe_session_id = session.id
                order.save(update_fields=['stripe_session_id'])
            return redirect(session.url, permanent=False)
        except stripe.StripeError:
            logger.warning('Stripe Checkout unavailable for order %s', order.id)
            messages.error(request, _('Le paiement est momentanément indisponible. Votre panier est conservé ; vous pouvez réessayer.'))
            return redirect('paiement:checkout')
    return render(request, 'paiement/checkout.html', {'enabled': enabled, 'total': total(items), 'cart_items': items})


def confirmation(request, order_id):
    order = get_object_or_404(Order, id=order_id, session_key=request.session.session_key or '')
    if order.status == 'paid' and request.session.get('checkout_order') == str(order.id):
        # Remove purchased quantities only; keep anything added after checkout started.
        cart = request.session.get('cart', {})
        for item in order.items:
            key = f"{item['product']}:{item['size']}"
            if key in cart:
                remaining = cart[key]['quantity'] - item['quantity']
                if remaining > 0:
                    cart[key]['quantity'] = remaining
                else:
                    del cart[key]
        request.session['cart'] = cart
        request.session.pop('checkout_order', None)
    return render(request, 'paiement/confirmation.html', {'order': order})


@csrf_exempt
@require_POST
def webhook(request):
    if not settings.STRIPE_WEBHOOK_SECRET or not settings.STRIPE_SECRET_KEY:
        return HttpResponse(status=503)
    try:
        event = stripe.Webhook.construct_event(request.body, request.headers.get('Stripe-Signature', ''), settings.STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.SignatureVerificationError):
        return HttpResponse(status=400)
    if event['type'] not in ('checkout.session.completed', 'checkout.session.async_payment_succeeded'):
        return HttpResponse(status=200)
    event_session = event['data']['object']
    # Retrieve authoritative payment state, never trust the customer's return URL.
    try:
        session = stripe.checkout.Session.retrieve(event_session['id'], api_key=settings.STRIPE_SECRET_KEY)
    except stripe.StripeError:
        return HttpResponse(status=503)
    if session.get('payment_status') != 'paid':
        return HttpResponse(status=200)
    order_id = session.get('metadata', {}).get('order_id')
    if not order_id:
        return HttpResponse(status=400)
    from django.core.exceptions import ValidationError
    try:
        with transaction.atomic():
            order = Order.objects.select_for_update().get(id=order_id)
            if order.stripe_session_id and order.stripe_session_id != session['id']:
                return HttpResponse(status=400)
            if session.get('currency') != 'eur' or session.get('amount_subtotal') != order.subtotal or session.get('client_reference_id') != str(order.id):
                return HttpResponse(status=400)
            if order.status != 'paid':
                order.status = 'paid'
                order.paid_at = timezone.now()
                order.stripe_session_id = session['id']
                order.customer_email = (session.get('customer_details') or {}).get('email') or ''
                order.shipping_details = (session.get('collected_information') or {}).get('shipping_details') or session.get('shipping_details') or {}
                order.total_paid = session['amount_total']
                order.save()
    except (Order.DoesNotExist, ValidationError):
        return HttpResponse(status=400)
    return HttpResponse(status=200)
