"""Refunds are immutable attempts, serialized per order and reconciled with Stripe."""
from datetime import timedelta
import stripe
from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from paiement.models import Order
from .models import ReturnRequest, ReturnEvent
from .services import ReturnError, validate_refund_allocation
from .mail import return_notice


def apply_refund(record, refund):
    if (refund.get('currency') != 'eur' or refund.get('amount') != record.amount
        or refund.get('payment_intent') != record.payment_intent
        or refund.get('metadata',{}).get('return_id') != str(record.pk)
        or (record.stripe_refund_id and record.stripe_refund_id != refund['id'])):
        raise ReturnError('Le remboursement Stripe ne correspond pas à ce dossier. Vérification nécessaire.')
    new = refund.get('status')
    if new not in ('pending','succeeded','failed','canceled','requires_action'):raise ReturnError('État Stripe inconnu.')
    state = {'canceled':'failed','requires_action':'review'}.get(new,new)
    if record.refund_state=='succeeded' and state!='succeeded':raise ReturnError('État de remboursement incohérent.')
    changed = record.refund_state != state
    record.stripe_refund_id=refund['id'];record.refund_state=state
    if state=='succeeded' and not record.refunded_at:record.refunded_at=timezone.now()
    record.save(update_fields=['stripe_refund_id','refund_state','refunded_at'])
    if changed:
        ReturnEvent.objects.create(request=record,label='Stripe : '+record.get_refund_state_display()+'.')
        if state=='succeeded':return_notice(record,'refund','Remboursement confirmé auprès de Stripe. L’apparition sur votre compte dépend des délais bancaires.')
    return record


def initiate(pk, actor, verified_problem=False):
    if ReturnRequest.objects.filter(pk=pk,order__status='demo').exists():
        from .demo import simulate_refund
        return simulate_refund(pk,actor)
    if not settings.STRIPE_SECRET_KEY or not settings.PAYMENTS_ENABLED:raise ReturnError('Stripe n’est pas encore activé. Le dossier reste enregistré, sans opération financière.')
    order_id=ReturnRequest.objects.values_list('order_id',flat=True).get(pk=pk)
    # Commit the immutable attempt before contacting Stripe, including on process crash.
    with transaction.atomic():
        order=Order.objects.select_for_update().get(pk=order_id)
        record=ReturnRequest.objects.select_for_update().get(pk=pk)
        if record.refund_state in ('succeeded','pending'):return record
        if record.refund_state in ('failed','review'):raise ReturnError('Vérifiez ce remboursement auprès de Stripe avant toute nouvelle opération. Aucun second remboursement automatique.')
        if order.return_shipping_amount is None:raise ReturnError('Les frais de livraison de cette ancienne commande doivent être vérifiés avant remboursement.')
        if record.kind=='problem' and not verified_problem:raise ReturnError('Vérifiez et confirmez le montant de ce dossier SAV avant remboursement.')
        if not order.stripe_session_id:raise ReturnError('Cette commande n’a pas de paiement Stripe lié.')
        committed = order.returns.exclude(pk=pk).exclude(refund_state='none').aggregate(total=Sum('amount'))['total'] or 0
        if order.status != 'paid' or order.total_paid is None or committed+record.amount>order.total_paid:raise ReturnError('Le montant dépasse le solde remboursable de la commande.')
        validate_refund_allocation(record, order)
        if record.refund_state=='none':
            record.refund_state='submitting';record.refund_started_at=timezone.now()
            record.save(update_fields=['refund_state','refund_started_at'])
            ReturnEvent.objects.create(request=record,actor=actor,label='Remboursement demandé par la boutique ; confirmation Stripe attendue.')
    return reconcile(pk)


def reconcile(pk):
    order_id=ReturnRequest.objects.values_list('order_id',flat=True).get(pk=pk)
    with transaction.atomic():
        order=Order.objects.select_for_update().get(pk=order_id)
        record=ReturnRequest.objects.select_for_update().get(pk=pk)
        if order.is_demo:raise ReturnError('Ce dossier est une simulation, sans opération Stripe.')
        if record.refund_state=='none':return record
        if record.stripe_refund_id:
            refund=stripe.Refund.retrieve(record.stripe_refund_id,api_key=settings.STRIPE_SECRET_KEY)
            return apply_refund(record,refund)
        if record.refund_state!='submitting':return record
        validate_refund_allocation(record, order)
        if record.amount==0:
            record.refund_state='succeeded';record.refunded_at=timezone.now();record.save(update_fields=['refund_state','refunded_at'])
            ReturnEvent.objects.create(request=record,label='Aucun montant à rembourser (article gratuit).')
            return_notice(record,'refund','Cet article était gratuit après remise : aucun montant ne doit être reversé. Le dossier reste suivi pour la réception des articles.')
            return record
        session=stripe.checkout.Session.retrieve(order.stripe_session_id,api_key=settings.STRIPE_SECRET_KEY)
        if session.get('payment_status')!='paid' or session.get('amount_total')!=order.total_paid or session.get('client_reference_id')!=str(order.pk):raise ReturnError('Le paiement Stripe de cette commande doit être vérifié.')
        intent=session.get('payment_intent')
        if not intent:raise ReturnError('Paiement Stripe introuvable.')
        if record.payment_intent and record.payment_intent!=intent:raise ReturnError('Le paiement lié a changé.')
        record.payment_intent=intent;record.save(update_fields=['payment_intent'])
        # A delayed retry must never recreate an old attempt after Stripe's key retention.
        if timezone.now()-record.refund_started_at>timedelta(hours=23):
            matches=stripe.Refund.list(payment_intent=intent,limit=100,api_key=settings.STRIPE_SECRET_KEY)
            found=next((r for r in matches['data'] if r.get('metadata',{}).get('return_id')==str(record.pk)),None)
            if found:return apply_refund(record,found)
            record.refund_state='review';record.save(update_fields=['refund_state'])
            ReturnEvent.objects.create(request=record,label='Tentative ancienne : rapprochement manuel requis, aucun nouvel envoi.')
            return record
        existing=stripe.Refund.list(payment_intent=intent,limit=100,api_key=settings.STRIPE_SECRET_KEY)
        own=next((r for r in existing['data'] if r.get('metadata',{}).get('return_id')==str(record.pk)),None)
        if own:return apply_refund(record,own)
        known=set(order.returns.exclude(stripe_refund_id__isnull=True).values_list('stripe_refund_id',flat=True))
        if existing.get('has_more') or any(r['id'] not in known for r in existing['data']):
            record.refund_state='review';record.save(update_fields=['refund_state'])
            ReturnEvent.objects.create(request=record,label='Remboursement externe détecté : contrôle requis avant nouvel envoi.')
            return record
        refund=stripe.Refund.create(payment_intent=intent,amount=record.amount,reason='requested_by_customer',metadata={'return_id':str(record.pk),'order_id':str(order.pk)},idempotency_key=f'return-{record.pk}',api_key=settings.STRIPE_SECRET_KEY)
        return apply_refund(record,refund)


def reconcile_all():
    if not settings.STRIPE_SECRET_KEY:return
    for pk in ReturnRequest.objects.exclude(order__status='demo').filter(refund_state__in=['submitting','pending']).values_list('pk',flat=True)[:50]:
        try:reconcile(pk)
        except (stripe.StripeError,ReturnError):continue


def apply_verified_event(refund):
    pk=refund.get('metadata',{}).get('return_id')
    if not pk:return False
    try:record=ReturnRequest.objects.get(pk=pk)
    except (ReturnRequest.DoesNotExist,ValueError):return False
    with transaction.atomic():
        order=Order.objects.select_for_update().get(pk=record.order_id)
        record=ReturnRequest.objects.select_for_update().get(pk=pk)
        if order.is_demo or record.refund_state=='none':return False
        if not record.payment_intent:
            session=stripe.checkout.Session.retrieve(order.stripe_session_id,api_key=settings.STRIPE_SECRET_KEY)
            if session.get('payment_status')!='paid' or session.get('amount_total')!=order.total_paid or session.get('client_reference_id')!=str(order.pk):raise ReturnError('Paiement non vérifié.')
            record.payment_intent=session.get('payment_intent') or ''
            record.save(update_fields=['payment_intent'])
        apply_refund(record,refund)
        return True
