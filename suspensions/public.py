"""Public pause information; reads scheduling state without touching orders."""
from zoneinfo import ZoneInfo

from django.http import JsonResponse
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .services import public_state

PARIS = ZoneInfo('Europe/Paris')


def public_payload(at=None):
    state = public_state(at=at)
    # Publish only the customer-facing contract, never administrative metadata.
    payload = {key: state[key] for key in (
        'paused', 'message', 'starts_at', 'resumes_at', 'next_change_at', 'checked_at', 'revision')}
    resumes = parse_datetime(state['resumes_at']) if state['resumes_at'] else None
    if resumes:
        local = resumes.astimezone(PARIS)
        payload['resume_label'] = f'Reprise des commandes le {local:%d/%m/%Y} à {local:%H h %M} (heure de Paris).'
    else:
        payload['resume_label'] = ''
    return payload


def public_information(request):
    match = getattr(request, 'resolver_match', None)
    name = match.view_name if match else ''
    if (request.path.startswith(('/gestion/', '/admin/'))
            or name in {'core:catalog_preview', 'editorial:page_preview_frame', 'editorial:page_version_frame', 'historique:frame'}
            or getattr(request, '_history_preview', False)):
        return {}
    return {'order_pause': {**public_payload(), 'show_public': True,
                           'status_url': reverse('suspensions_public:status')}}


@require_GET
@never_cache
def status(request):
    response = JsonResponse(public_payload())
    response['Cache-Control'] = 'no-store, max-age=0'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
