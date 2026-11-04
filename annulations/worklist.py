"""Read-only cancellation follow-up, separate from physical return queues."""
import re
from urllib.parse import urlencode

from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_cache_control
from django.views.decorators.http import require_GET

from expeditions.priorities import waiting_time
from retours.access import returns_required
from .models import Cancellation


def filters():
    completed = Q(refund__refund_state='succeeded', stock_completed_at__isnull=False)
    pending = Q(refund__refund_state='pending', refund_error='')
    stock = Q(refund__refund_state='succeeded', stock_completed_at__isnull=True)
    refund = ~Q(refund__refund_state='succeeded') & ~pending
    return [
        ('attention', 'À traiter', ~completed),
        ('pending', 'En attente', pending),
        ('refund', 'Remboursement à vérifier', refund),
        ('stock', 'Stock à remettre', stock),
        ('completed', 'Terminées', completed),
        ('all', 'Toutes', Q()),
    ]


def records_for(scope='paid'):
    # Both callers default to actual paid purchases. A forged demo parameter
    # must never reveal development orders outside DEBUG.
    scope = 'demo' if settings.DEBUG and scope == 'demo' else 'paid'
    return Cancellation.objects.filter(order__status=scope).select_related('order', 'refund')


def prioritized(records):
    conditions = {key: condition for key, _, condition in filters()}
    return records.annotate(work_priority=Case(
        When(conditions['refund'] | conditions['stock'], then=Value(0)),
        When(conditions['pending'], then=Value(1)),
        default=Value(2), output_field=IntegerField(),
    )).order_by('work_priority', 'created_at', 'pk')


def followup(record, now=None):
    """Explain the next action without retrying refunds or touching stock."""
    state = record.refund.refund_state
    if state == 'succeeded' and record.stock_completed_at is not None:
        key, label, action = 'completed', 'Annulation terminée', 'Voir le récapitulatif'
        note = 'Remboursement confirmé et articles remis en stock.'
        if record.order.is_demo:
            note = 'Remboursement et remise en stock simulés.'
    elif state == 'succeeded':
        key, label, action = 'stock', 'Stock à remettre', 'Vérifier la remise en stock'
        note = 'Le remboursement est confirmé. La remise en stock reste à terminer.'
        if record.order.is_demo:
            note = 'Le remboursement est simulé. La vérification du stock reste à terminer.'
    elif state == 'pending' and not record.refund_error:
        key, label, action = 'pending', 'Remboursement en attente', 'Suivre le remboursement'
        note = 'Le remboursement a été demandé ; sa confirmation est attendue.'
    else:
        key, action = 'refund', 'Vérifier le remboursement'
        if state == 'none':
            label, action = 'Remboursement à effectuer', 'Reprendre le remboursement'
            note = 'La commande est annulée. Le remboursement reste à effectuer.'
        elif state == 'failed':
            label = 'Remboursement échoué'
            note = 'Le remboursement n’a pas abouti. Ouvrez le dossier pour vérifier la suite.'
        else:
            label = 'Remboursement à vérifier'
            note = 'La confirmation du remboursement nécessite une vérification.'
    shipping = record.order.shipping_details
    customer_name = shipping.get('name', '') if isinstance(shipping, dict) else ''
    return {
        'record': record, 'order': record.order, 'key': key,
        'label': label, 'action': action, 'note': note,
        'needs_action': key in ('refund', 'stock'),
        'customer_name': str(customer_name or ''),
        'amount': record.refund.display_amount,
        'since': record.created_at,
        'waiting_label': waiting_time(record.created_at, now or timezone.now()),
        'url': reverse('annulations:detail', args=[record.order_id]),
    }


def dashboard_attention():
    condition = dict((key, condition) for key, _, condition in filters())['attention']
    records = records_for().filter(condition)
    now = timezone.now()
    return {
        'total': records.count(),
        'rows': [followup(record, now) for record in prioritized(records)[:5]],
        'url': reverse('annulations:overview'),
    }


@returns_required
@require_GET
def overview(request):
    scope = 'demo' if settings.DEBUG and request.GET.get('scope') == 'demo' else 'paid'
    definitions = filters()
    conditions = {key: condition for key, _, condition in definitions}
    state = request.GET.get('state', 'attention')
    if state not in conditions:
        state = 'attention'
    query = request.GET.get('q', '').strip()[:100]
    records = records_for(scope)
    if query:
        records = records.filter(order__pk__icontains=query) if re.fullmatch(r'[0-9a-fA-F-]+', query) else records.none()
    counts = records.aggregate(**{f'count_{key}': Count('pk', filter=condition) for key, _, condition in definitions})
    selected = records.filter(conditions[state])
    selected = selected.order_by('-created_at', '-pk') if state == 'completed' else prioritized(selected)
    page = Paginator(selected, 25).get_page(request.GET.get('page'))
    now = timezone.now()
    destination = reverse('annulations:overview')
    tabs = [{
        'key': key, 'label': label, 'count': counts[f'count_{key}'],
        'url': destination + '?' + urlencode({'scope': scope, 'state': key, 'q': query}),
    } for key, label, _ in definitions]
    response = render(request, 'annulations/overview.html', {
        'scope': scope, 'allow_demo': bool(settings.DEBUG), 'state': state,
        'query': query, 'tabs': tabs, 'page_obj': page,
        'rows': [followup(record, now) for record in page],
        'filter_query': urlencode({'scope': scope, 'state': state, 'q': query}),
        'back_url': reverse('tableau:home'), 'back_use_history': False,
    })
    patch_cache_control(response, private=True, no_store=True, max_age=0)
    response['X-Robots-Tag'] = 'noindex, nofollow'
    response['Referrer-Policy'] = 'same-origin'
    return response
