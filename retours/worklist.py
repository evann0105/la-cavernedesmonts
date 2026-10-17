"""Operational return queues, independent of the selected sales period."""
from datetime import timedelta
from django.conf import settings
from django.db.models import Q, Count, Case, When, Value, IntegerField
from django.utils import timezone
from django.urls import reverse
from .models import ReturnRequest


def action_definitions():
    days = max(1, getattr(settings, 'RETURN_ACTION_AGE_DAYS', 7))
    cutoff = timezone.now() - timedelta(days=days)
    return [
        ('new', 'Nouveaux retours', Q(return_state='declared')),
        ('check', 'Colis reçus à contrôler', Q(return_state='received')),
        ('refund_due', 'Remboursements à effectuer', Q(refund_state='none')),
        ('refund_pending', 'Remboursements en attente', Q(refund_state__in=['pending','submitting'])),
        ('refund_failed', 'Échecs ou vérifications', Q(refund_state__in=['failed','review'])),
        ('shipments', 'Nouveaux suivis ou justificatifs', Q(new_shipments__gt=0)),
        ('close', 'Prêts à clôturer', Q(return_state='checked', refund_state='succeeded')),
        ('older', f'Ouverts depuis {days} jours ou plus', Q(created_at__lte=cutoff)),
    ]


def open_returns():
    return ReturnRequest.objects.filter(order__status='paid').exclude(state='completed').select_related('order').annotate(
        new_shipments=Count('shipments', filter=Q(shipments__seen_at__isnull=True)))


def dashboard_attention():
    records = open_returns()
    definitions = action_definitions()
    cards = [{'key':key, 'label':label, 'count':records.filter(condition).count(),
              'url':reverse('retours:manage') + '?action=' + key} for key, label, condition in definitions]
    relevant = Q()
    for _, _, condition in definitions:
        relevant |= condition
    queue = records.filter(relevant).annotate(priority=Case(
        When(refund_state__in=['failed','review'], then=Value(0)),
        When(created_at__lte=timezone.now()-timedelta(days=max(1,getattr(settings,'RETURN_ACTION_AGE_DAYS',7))), then=Value(1)),
        When(return_state='received', then=Value(2)), default=Value(3), output_field=IntegerField()
    )).order_by('priority','created_at','pk')[:8]
    items = []
    for record in queue:
        items.append(record)
    return {'cards':cards, 'records':items, 'total':records.count()}
