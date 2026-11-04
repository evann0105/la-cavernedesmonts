"""Read-only pause indicator for the daily seller workspace."""
from zoneinfo import ZoneInfo

from django.http import JsonResponse
from django.utils import timezone
from django.utils.cache import patch_cache_control
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET

from tableau.access import seller_required
from .models import PurchasePause
from .services import public_state

PARIS = ZoneInfo('Europe/Paris')


def _date(value):
    return timezone.localtime(value, PARIS).strftime('%d/%m/%Y à %H h %M')


def dashboard_state(at=None):
    """Describe pauses only; payment and delivery readiness are independent."""
    at = at or timezone.now()
    state = public_state(at=at)
    data = {
        'schema': 1,
        'state': 'paused' if state['paused'] else 'open',
        'title': 'Commandes en pause' if state['paused'] else 'Commandes ouvertes',
        'badge': 'En pause' if state['paused'] else 'Aucune pause',
        'schedule': '',
        'message': state['message'],
        'checked_at': state['checked_at'],
        'next_change_at': state['next_change_at'],
    }
    if state['paused']:
        # public_state includes all adjacent periods in the final reopening.
        end = parse_datetime(state['resumes_at'])
        data['schedule'] = f'Reprise automatique le {_date(end)} (heure de Paris).'
    else:
        upcoming = PurchasePause.objects.filter(
            stopped_at__isnull=True, starts_at__gt=at,
        ).order_by('starts_at', 'pk').first()
        if upcoming:
            future_state = public_state(at=upcoming.starts_at)
            end = parse_datetime(future_state['resumes_at'] or '') or upcoming.ends_at
            data.update({
                'state': 'scheduled', 'badge': 'Pause programmée',
                'schedule': (f'Prochaine pause le {_date(upcoming.starts_at)}. '
                             f'Reprise le {_date(end)} (heure de Paris).'),
            })
        else:
            data['schedule'] = 'Aucune pause programmée pour le moment.'
    return data


@seller_required
@require_GET
def status(request):
    response = JsonResponse(dashboard_state())
    patch_cache_control(response, private=True, no_store=True, max_age=0)
    response['X-Robots-Tag'] = 'noindex, nofollow'
    response['Referrer-Policy'] = 'same-origin'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
