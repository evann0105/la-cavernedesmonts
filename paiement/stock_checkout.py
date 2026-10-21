"""Durable Stripe attempts: ambiguous network errors keep inventory reserved."""
import stripe
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from .models import Order
from .shipping import shipping_option
from stocks.reservations import finish_locked, StockUnavailable


def benefit_coupon_id(order):
    return f'customer-benefit-{order.pk.hex}'


def get_session(order_id, language='fr'):
    with transaction.atomic():
        order=Order.objects.select_for_update().get(pk=order_id)
        if not order.checkout_payload:
            if order.reservation_expires_at <= timezone.now():
                # No payload means no request was ever sent to Stripe.
                finish_locked(order,False);order.status='expired';order.save()
                return None
            from espace.legal import contract_snapshot, profile as legal_profile
            from facturation.profile import capture_profile
            # Read the live identity once: both purchase records must describe
            # the same seller even if the shop is edited in another session.
            merchant = legal_profile()
            if order.shipping_snapshot:
                from .shipping import snapshot_delivery_text
                merchant = {**merchant, 'delivery': snapshot_delivery_text(order.shipping_snapshot),
                            'delivery_revision': order.shipping_snapshot['config_revision'],
                            'delivery_managed': True, 'delivery_blockers': []}
            order.terms_snapshot = contract_snapshot(merchant)
            order.invoice_profile = capture_profile(merchant)
            order.checkout_payload={
                'consent_collection':{'terms_of_service':'required'},
                'custom_text':{
                    'terms_of_service_acceptance':{'message':f"J’accepte les [conditions générales de vente]({settings.SITE_URL}{reverse('espace:sales_terms')}) et j’ai pris connaissance du droit de rétractation."},
                    'submit':{'message':'Commande avec obligation de paiement. Achat ponctuel, sans abonnement.'},
                },
                'allow_promotion_codes':True,'mode':'payment','payment_method_types':['card'],'locale':language,
                'expires_at':int(order.reservation_expires_at.timestamp()),
                'line_items':[{'price_data':{'currency':'eur','unit_amount':i['unit_amount'],'product_data':{'name':f"{i['name']} — {i['size']} · {i['color']}",'metadata':{'order_id':str(order.pk),'order_item':str(n)}}},'quantity':i['quantity']} for n,i in enumerate(order.items)],
                'shipping_address_collection':{'allowed_countries':[order.shipping_snapshot['country']] if order.shipping_snapshot else ['FR']},
                'shipping_options':[shipping_option(order.subtotal, snapshot=order.shipping_snapshot)],
                'billing_address_collection':'required',
                'success_url':settings.SITE_URL+reverse('paiement:confirmation',args=[order.pk]),
                'cancel_url':settings.SITE_URL+reverse('core:cart'),
                'client_reference_id':str(order.pk),'metadata':{'order_id':str(order.pk)},
            }
            from avoirs.models import BenefitRedemption
            redemption = BenefitRedemption.objects.filter(order=order, state='held').first()
            if redemption:
                order.checkout_payload.pop('allow_promotion_codes', None)
                order.checkout_payload['discounts'] = [{'coupon': benefit_coupon_id(order)}]
                # The beneficiary is authenticated and verified before reservation.
                # This is a payment contact, never a public promotion code.
                order.customer_email = redemption.benefit.recipient_email
                order.checkout_payload['metadata']['customer_benefit'] = str(redemption.benefit_id)
            order.save(update_fields=['checkout_payload','terms_snapshot','invoice_profile','customer_email'])
    # The payload and reservations are committed before any external request.
    with transaction.atomic():
        order=Order.objects.select_for_update().get(pk=order_id)
        if order.status != 'pending':return None
        if order.stripe_session_id:
            session=stripe.checkout.Session.retrieve(order.stripe_session_id,api_key=settings.STRIPE_SECRET_KEY)
        else:
            # Stripe may prune idempotency keys after 24 h. Never risk recreating an old ambiguous attempt.
            if timezone.now()-order.created_at > timedelta(hours=23):
                raise StockUnavailable('Cette tentative de paiement nécessite une vérification par la boutique.')
            from avoirs.models import BenefitRedemption
            redemption = BenefitRedemption.objects.filter(order=order, state='held').first()
            if redemption and not redemption.stripe_coupon_id:
                # Frozen amount and stable idempotency key: a timeout cannot
                # generate a second code or silently alter an existing attempt.
                coupon = stripe.Coupon.create(
                    api_key=settings.STRIPE_SECRET_KEY,
                    idempotency_key=f'benefit-coupon-{order.pk}',
                    id=benefit_coupon_id(order), amount_off=redemption.discount_amount,
                    currency='eur', duration='once', max_redemptions=1,
                    name='Avantage client La Caverne des Monts',
                    metadata={'order_id': str(order.pk)},
                )
                redemption.stripe_coupon_id = coupon.id
                redemption.save(update_fields=['stripe_coupon_id'])
            payload = dict(order.checkout_payload)
            if redemption:
                # Contact stays encrypted at rest rather than leaking into the
                # otherwise non-personal JSON used for payment idempotency.
                payload['customer_email'] = order.customer_email
            session=stripe.checkout.Session.create(api_key=settings.STRIPE_SECRET_KEY,idempotency_key=f'order-{order.pk}',**payload)
            order.stripe_session_id=session.id
            order.save(update_fields=['stripe_session_id'])
        return session


def settle(session):
    """Only server-retrieved Stripe state may consume or release a reservation."""
    order_id=session.get('metadata',{}).get('order_id')
    if not order_id:return False
    with transaction.atomic():
        order=Order.objects.select_for_update().get(pk=order_id)
        if order.is_demo:return False
        if order.stripe_session_id and order.stripe_session_id != session['id']:return False
        if session.get('client_reference_id') != str(order.pk):return False
        from .line_totals import payment_confirmed
        if payment_confirmed(session):
            if session.get('currency') != 'eur' or session.get('amount_subtotal') != order.subtotal:return False
            from .shipping import payment_matches_shipping
            if not payment_matches_shipping(order, session):return False
            if order.status == 'paid':return True
            if order.status == 'expired':return False
            from .line_totals import capture_line_totals
            order.paid_line_totals = capture_line_totals(order, session)
            finish_locked(order,True)
            # Keep the same global lock order as reservation (stock, then
            # benefit). A mismatch rolls back stock and benefit together.
            from avoirs.services import consume_locked
            consume_locked(order, session)
            order.status='paid';order.paid_at=timezone.now();order.stripe_session_id=session['id']
            order.customer_email=(session.get('customer_details') or {}).get('email') or ''
            customer = session.get('customer_details') or {}
            order.billing_details = {key:customer.get(key) for key in ('name','address') if customer.get(key) is not None}
            order.shipping_details=(session.get('collected_information') or {}).get('shipping_details') or session.get('shipping_details') or {}
            # Record acceptance only from the server-retrieved Stripe object.
            if (session.get('consent') or {}).get('terms_of_service') == 'accepted':
                order.terms_accepted_at = timezone.now()
            order.total_paid=session['amount_total']
            order.return_shipping_amount=(session.get('total_details') or {}).get('amount_shipping',0)
            order.save()
            from retours.mail import order_confirmation
            order_confirmation(order)
            from retours.mail import deliver_pending
            transaction.on_commit(deliver_pending, robust=True)
        elif session.get('status') == 'expired' and session.get('payment_status') == 'unpaid':
            if order.status == 'pending':
                finish_locked(order,False);order.status='expired';order.save()
        return True
