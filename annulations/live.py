"""Read-only, private snapshots for the seller's cancellation follow-up."""
import hashlib
import json

from django.conf import settings
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.utils.cache import patch_vary_headers
from django.views.decorators.http import require_GET

from retours.access import returns_required

from .models import Cancellation
from .notifications import notification_cards


SETTLED_NOTIFICATIONS = frozenset({'sent_local', 'sent_provider', 'unknown', 'cancelled'})
PARTIALS = {
    'progress': 'annulations/_progress.html',
    'stock': 'annulations/_stock_history.html',
    'notifications': 'annulations/_notifications.html',
}


def _version(facts):
    # Rendering includes a newly masked CSRF token. Hash only displayed facts,
    # never rendered HTML, private notes or raw payment / e-mail payloads.
    value = json.dumps(facts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def tracking_state(cancellation, cards, stock_movements):
    refund = cancellation.refund
    demo = cancellation.order.is_demo
    progress = {
        'demo': demo, 'refund_state': refund.refund_state,
        'refund_error': cancellation.refund_error,
        'stock_completed_at': cancellation.stock_completed_at,
        'stock_error': cancellation.stock_error,
    }
    stock = {
        'demo': demo, 'stock_completed_at': cancellation.stock_completed_at,
        'movements': [
            {
                'id': movement.pk, 'name': movement.product_name,
                'size': movement.size, 'color': movement.color,
                'before': movement.quantity_before, 'added': movement.quantity_added,
                'after': movement.quantity_after, 'reserved': movement.reserved,
                'created_at': movement.created_at, 'source': movement.source,
                'actor': (movement.actor.get_full_name() or movement.actor.get_username())
                         if movement.actor else '',
            }
            for movement in stock_movements
        ],
    }
    # Explicit allowlist: future internal card fields must not change or leak
    # through this endpoint without a deliberate review.
    card_fields = (
        'pk', 'kind', 'title', 'exists', 'recipient', 'date', 'date_label',
        'attempts', 'status', 'status_label', 'reason', 'can_retry', 'detail_url',
    )
    notices = [{key: card.get(key) for key in card_fields} for card in cards]
    versions = {key: _version(value) for key, value in (
        ('progress', progress), ('stock', stock), ('notifications', notices),
    )}
    completed = refund.refund_state == 'succeeded'
    return {
        'version': _version({'cancellation': cancellation.pk, 'regions': versions}),
        'region_versions': versions,
        'active': not (completed and cancellation.stock_completed_at and len(cards) == 2
                       and all(card['status'] in SETTLED_NOTIFICATIONS for card in cards)),
        'total_label': ('Total simulé' if demo else 'Total remboursé') if completed else 'Total à rembourser',
    }


def _private(response):
    response['Cache-Control'] = 'private, no-store, max-age=0'
    response['Referrer-Policy'] = 'same-origin'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    response['X-Content-Type-Options'] = 'nosniff'
    patch_vary_headers(response, ('Cookie',))
    return response


@returns_required
@require_GET
def status(request, pk):
    statuses = ('paid', 'demo') if settings.DEBUG else ('paid',)
    cancellation = get_object_or_404(
        Cancellation.objects.select_related('order', 'refund', 'refund__order'),
        order_id=pk, order__status__in=statuses,
    )
    cards = notification_cards(cancellation)
    movements = list(cancellation.stock_movements.select_related('actor'))
    state = tracking_state(cancellation, cards, movements)
    payload = {key: state[key] for key in ('version', 'active', 'total_label')}
    payload['changed'] = request.GET.get('version') != state['version']
    if payload['changed']:
        context = {
            'order': cancellation.order, 'cancellation': cancellation,
            'notification_cards': cards, 'stock_movements': movements,
            'csrf_token': get_token(request),
        }
        payload['fragments'] = {
            key: {
                'html': render_to_string(template, context),
                'version': state['region_versions'][key],
            }
            for key, template in PARTIALS.items()
        }
    return _private(JsonResponse(payload))
