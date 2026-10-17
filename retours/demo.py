"""Explicit local demonstrations: no bank API and no real stock movements."""
from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from paiement.models import Order
from .models import ReturnRequest, ReturnEvent
from .services import ReturnError
from .mail import return_notice


@transaction.atomic
def simulate_refund(pk, actor):
    if not settings.DEBUG:raise ReturnError('La simulation est réservée au développement local.')
    order_id=ReturnRequest.objects.values_list('order_id',flat=True).get(pk=pk)
    order=Order.objects.select_for_update().get(pk=order_id)
    if not order.is_demo:raise ReturnError('Cette commande n’est pas une simulation.')
    record=ReturnRequest.objects.select_for_update().get(pk=pk)
    if record.refund_state=='succeeded':return record
    committed=order.returns.exclude(pk=record.pk).exclude(refund_state='none').aggregate(total=Sum('amount'))['total'] or 0
    if committed+record.amount>order.total_paid:raise ReturnError('Le montant dépasse la commande fictive.')
    record.refund_state='succeeded';record.refunded_at=timezone.now()
    record.save(update_fields=['refund_state','refunded_at'])
    ReturnEvent.objects.create(request=record,actor=actor,label='SIMULATION : remboursement confirmé, sans appel Stripe ni transfert d’argent.')
    return_notice(record,'refund','SIMULATION : remboursement fictif confirmé. Aucun paiement, appel Stripe ou transfert d’argent n’a eu lieu.')
    return record
