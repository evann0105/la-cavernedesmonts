"""Read-only daily workspace, independent of the sales statistics period."""
from django.db.models import Count, Q
from django.db.models.functions import Coalesce
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_cache_control
from django.views.decorators.http import require_GET

from alertes.dashboard import attention as mail_stock_attention
from annulations.worklist import dashboard_attention as cancellation_attention
from clients.worklist import dashboard_attention as sav_attention
from expeditions.priorities import waiting_time
from expeditions.selectors import summary
from paiement.models import Order
from retours.access import manager
from retours.worklist import dashboard_attention as return_attention
from suspensions.dashboard import dashboard_state as purchase_pause_state

from .access import seller_required


def _private(response):
    patch_cache_control(response, private=True, no_store=True, max_age=0)
    response['X-Robots-Tag'] = 'noindex, nofollow'
    response['Referrer-Policy'] = 'same-origin'
    return response


def shipment_attention():
    """Match the dispatch queues, without treating blockers as ready to ship."""
    preparing = Q(fulfillment__isnull=True) | Q(fulfillment__state='preparing')
    orders = Order.objects.filter(status='paid', cancellation__isnull=True)
    counts = orders.aggregate(
        preparing=Count('pk', filter=preparing),
        ready=Count('pk', filter=Q(fulfillment__state='ready')),
    )
    oldest = orders.filter(preparing | Q(fulfillment__state='ready')).select_related(
        'fulfillment', 'cancellation__refund',
    ).prefetch_related('returns').annotate(
        priority_at=Coalesce('paid_at', 'created_at'),
    ).order_by('priority_at', 'created_at', 'pk')[:5]
    now = timezone.now()
    rows = []
    for order in oldest:
        shipment = summary(order)
        since = order.paid_at or order.created_at
        rows.append({
            'order': order,
            'shipment': shipment,
            'since': since,
            'waiting_label': waiting_time(since, now),
            'url': reverse('expeditions:detail', args=[order.pk]),
            'action_label': 'Vérifier la commande' if shipment['blocked_reason'] else 'Ouvrir la commande',
        })
    destination = reverse('expeditions:overview')
    return {
        **counts, 'total': counts['preparing'] + counts['ready'], 'rows': rows,
        'preparing_url': destination + '?state=preparing',
        'ready_url': destination + '?state=ready',
    }


def _action_cards(shipments, returns, sav, alerts, cancellations):
    """Keep independent counts; the same case may require several actions."""
    cards = []
    if shipments is not None:
        cards.extend([
            {'key': 'preparing', 'label': 'Commandes à préparer', 'count': shipments['preparing'],
             'url': shipments['preparing_url'], 'description': 'Commandes payées en attente de préparation.'},
            {'key': 'ready', 'label': 'Commandes prêtes', 'count': shipments['ready'],
             'url': shipments['ready_url'], 'description': 'Colis préparés, en attente de remise au transporteur.'},
        ])
    if cancellations is not None:
        cards.append({'key': 'cancellations', 'label': 'Annulations à vérifier', 'count': cancellations['total'],
                      'url': cancellations['url'],
                      'description': 'Remboursements et remises en stock restant à terminer.'})
    if sav is not None:
        cards.append({'key': 'sav', 'label': 'SAV à suivre', 'count': sav['total'],
                      'url': reverse('clients:case_list') + '?attention=1',
                      'description': 'Rappels du jour, en retard et dossiers urgents.'})
    if returns is not None:
        cards.append({'key': 'returns', 'label': 'Retours en cours', 'count': returns['total'],
                      'url': reverse('retours:manage') + '?action=open', 'description': 'Dossiers de retour à suivre jusqu’à leur clôture.'})
    if alerts['mail_count'] is not None:
        cards.append({'key': 'emails', 'label': 'E-mails à vérifier', 'count': alerts['mail_count'],
                      'url': reverse('alertes:emails'), 'description': 'Messages en échec ou dont l’envoi est bloqué.'})
    if alerts['stock_count'] is not None:
        cards.append({'key': 'stock', 'label': 'Stocks à surveiller', 'count': alerts['stock_count'],
                      'url': reverse('alertes:stocks'), 'description': 'Tailles et couleurs au seuil d’alerte ou en dessous.'})
    return cards


@seller_required
@require_GET
def home(request):
    can_manage_service = manager(request.user)
    shipments = shipment_attention() if can_manage_service else None
    returns = return_attention() if can_manage_service else None
    sav = sav_attention() if can_manage_service else None
    cancellations = cancellation_attention() if can_manage_service else None
    alerts = mail_stock_attention(request.user)
    cards = _action_cards(shipments, returns, sav, alerts, cancellations)
    return _private(render(request, 'tableau/home.html', {
        'today': timezone.localdate(),
        'purchase_pause': purchase_pause_state(),
        'seller_name': request.user.first_name or request.user.get_username(),
        'shipment_attention': shipments,
        'return_attention': returns,
        'cancellation_attention': cancellations,
        'sav_attention': sav,
        'mail_stock_attention': alerts,
        'action_cards': cards,
        'has_attention': any(card['count'] for card in cards),
    }))


@seller_required
@require_GET
def tools(request):
    return _private(render(request, 'tableau/tools.html'))
