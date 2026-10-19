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
    from espace.legal import sales_ready
    return all((sales_ready(), settings.EMAIL_DELIVERY_ENABLED, not settings.EMAIL_TEST_MODE, settings.PAYMENTS_ENABLED, settings.SHOP_READY, settings.STRIPE_SECRET_KEY,
                settings.STRIPE_WEBHOOK_SECRET, settings.STRIPE_SHIPPING_RATE,
                settings.DEBUG or settings.SITE_URL.startswith('https://')))


@require_http_methods(['GET', 'POST'])
def checkout(request):
    from .cart_checkout import sync_paid_cart, current_order, release_for_cart_change
    paid_order = sync_paid_cart(request)
    if paid_order:
        return redirect('paiement:confirmation', order_id=paid_order.pk)
    items = lines(request)
    if not items:
        return redirect('core:cart')
    from stocks.reservations import reserve, StockUnavailable
    from .stock_checkout import get_session, settle
    enabled = ready() and all(i['variant'] and i['product'].availability != 'unavailable' for i in items)
    displayed_prices = [
        {'product': item['product'].pk, 'variant': item['variant'].pk if item['variant'] else None,
         'quantity': item['quantity'], 'unit_amount': int(item['unit_price'] * 100)}
        for item in items
    ]
    if request.method == 'GET':
        request.session['checkout_displayed_prices'] = displayed_prices
    if request.method == 'POST':
        if not enabled:
            messages.info(request, _('Le paiement en ligne n’est pas encore disponible pour ce panier. Contactez-nous pour préparer votre commande.'))
            return redirect('paiement:checkout')
        previous_prices = request.session.get('checkout_displayed_prices')
        if previous_prices is not None and previous_prices != displayed_prices:
            messages.info(request, 'Le prix ou le contenu de votre panier a changé. Vérifiez le nouveau récapitulatif avant de payer.')
            return redirect('paiement:checkout')
        if not request.session.session_key:request.session.save()
        snapshot = [{'product':i['product'].pk, 'variant':i['variant'].pk, 'name':i['product'].name, 'size':i['size'], 'color':i['color'], 'quantity':i['quantity'], 'unit_amount':int(i['unit_price']*100)} for i in items]
        try:
            previous = current_order(request)
            if previous and previous.status == 'pending' and (previous.items != snapshot or previous.user_id != (request.user.pk if request.user.is_authenticated else None)):
                paid_order = release_for_cart_change(request)
                if paid_order:
                    return redirect('paiement:confirmation', order_id=paid_order.pk)
            order=reserve(request.session.session_key,request.user.pk if request.user.is_authenticated else None,snapshot)
            request.session['checkout_order']=str(order.pk)
            session=get_session(order.pk,getattr(request,'LANGUAGE_CODE','fr'))
            if session is None:
                messages.info(request, _('Cette réservation est terminée. Vous pouvez réessayer.'))
                return redirect('paiement:checkout')
            if session.get('status') in ('complete','expired'):
                if not settle(session):raise StockUnavailable('Le paiement doit être vérifié par la boutique.')
                order.refresh_from_db()
                if order.status=='expired':
                    messages.info(request, _('La réservation a expiré. Vous pouvez relancer le paiement.'))
                    return redirect('paiement:checkout')
                return redirect('paiement:confirmation',order_id=order.pk)
            return redirect(session.url,permanent=False)
        except StockUnavailable as error:
            messages.error(request,str(error))
        except stripe.StripeError:
            logger.warning('Stripe Checkout unavailable; pending stock reservation preserved.')
            messages.error(request, _('Le paiement est momentanément indisponible. Votre panier est conservé ; réessayez pour reprendre la même tentative.'))
        return redirect('paiement:checkout')
    from .shipping import shipping_summary
    subtotal = total(items)
    return render(request,'paiement/checkout.html',{'enabled':enabled,'total':subtotal,'cart_items':items,'shipping':shipping_summary(subtotal)})


def confirmation(request, order_id):
    order = get_object_or_404(Order, id=order_id, session_key=request.session.session_key or '')
    from .cart_checkout import sync_paid_cart
    sync_paid_cart(request)
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
    if event['type'] in ('refund.created','refund.updated','refund.failed'):
        from retours.refunds import apply_verified_event
        from retours.services import ReturnError
        from django.core.exceptions import ValidationError
        try:
            refund=stripe.Refund.retrieve(event['data']['object']['id'],api_key=settings.STRIPE_SECRET_KEY)
            apply_verified_event(refund)
        except stripe.StripeError:return HttpResponse(status=503)
        except (ReturnError,ValidationError,ValueError,KeyError):return HttpResponse(status=400)
        return HttpResponse(status=200)
    if event['type'] not in ('checkout.session.completed', 'checkout.session.async_payment_succeeded', 'checkout.session.expired'):
        return HttpResponse(status=200)
    event_session = event['data']['object']
    # Retrieve authoritative payment state, never trust the customer's return URL.
    try:
        session = stripe.checkout.Session.retrieve(event_session['id'], api_key=settings.STRIPE_SECRET_KEY)
    except stripe.StripeError:
        return HttpResponse(status=503)
    from django.core.exceptions import ValidationError
    from stocks.reservations import StockUnavailable
    from .stock_checkout import settle
    try:
        if not settle(session):return HttpResponse(status=400)
    except StockUnavailable:
        return HttpResponse(status=409)
    except (Order.DoesNotExist, ValidationError, ValueError, KeyError):
        return HttpResponse(status=400)
    return HttpResponse(status=200)
