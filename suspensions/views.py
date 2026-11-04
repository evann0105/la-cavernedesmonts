"""Organized, private merchant controls for dated pauses in new purchases."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_http_methods

from core.catalog_access import catalog_required
from . import services
from .forms import PauseActionForm, PauseForm, DEFAULT_MESSAGE
from .history import history_context
from .models import PurchasePause


STATES = (('active', 'En cours'), ('upcoming', 'À venir'), ('past', 'Terminées'), ('cancelled', 'Annulées'))


def _private(response):
    response['Cache-Control'] = 'private, no-store, max-age=0'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    response['Referrer-Policy'] = 'same-origin'
    return response


def _pause(pk):
    if not 0 < pk <= 9223372036854775807:
        raise Http404
    return get_object_or_404(PurchasePause, pk=pk)


def _filters(at):
    return {
        'active': Q(stopped_at__isnull=True, starts_at__lte=at, ends_at__gt=at),
        'upcoming': Q(stopped_at__isnull=True, starts_at__gt=at),
        'past': Q(stop_kind='resume') | Q(stopped_at__isnull=True, ends_at__lte=at),
        'cancelled': Q(stop_kind='cancel'),
    }


def _mutable(pause):
    return pause is None or pause.status in ('active', 'upcoming')


def _form_error(form, error):
    if isinstance(error, ValidationError):
        if hasattr(error, 'message_dict'):
            for field, errors in error.message_dict.items():
                for message in errors:
                    form.add_error(field if field in form.fields else None, message)
        else:
            for message in error.messages:
                form.add_error(None, message)
    else:
        form.add_error(None, str(error))


@catalog_required
@require_GET
def overview(request):
    at = timezone.now()
    filters = _filters(at)
    state = request.GET.get('state', 'active')
    if state not in filters:
        state = 'active'
    queryset = PurchasePause.objects.all()
    counts = queryset.aggregate(**{key: Count('pk', filter=condition) for key, condition in filters.items()})
    active = queryset.filter(filters['active']).order_by('starts_at', 'pk').first()
    next_pause = queryset.filter(filters['upcoming']).order_by('starts_at', 'pk').first()
    state_public = services.public_state(at=at)
    resumes_at = parse_datetime(state_public['resumes_at']) if state_public.get('resumes_at') else None
    rows = queryset.filter(filters[state]).order_by('starts_at', 'pk') if state == 'upcoming' else queryset.filter(filters[state]).order_by('-starts_at', '-pk')
    page = Paginator(rows, 12).get_page(request.GET.get('page'))
    return _private(render(request, 'suspensions/overview.html', {
        'page_title': 'Pause des commandes', 'current_page': 'overview',
        'active': active, 'next_pause': next_pause, 'resumes_at': resumes_at, 'page_obj': page, 'state': state,
        'tabs': [{'key': key, 'label': label, 'count': counts[key]} for key, label in STATES],
    }))


def _editor(request, pause=None, *, source=None):
    revision = services.configuration_revision()
    form = PauseForm(request.POST if request.method == 'POST' else None,
        pause=pause, source=source, revision=revision, immediate=request.GET.get('mode') == 'now')
    status, conflict, fresh = 200, False, None
    editable = _mutable(pause)
    if request.method == 'POST':
        if not editable:
            form.add_error(None, 'Cette pause est terminée ou annulée. Utilisez « Dupliquer » pour reprendre ses textes avec de nouvelles dates.')
            status = 409
        elif form.is_valid():
            try:
                saved = services.save_pause(form.cleaned_data, request.user, pk=pause.pk if pause else None)
            except services.PauseConflict as error:
                status, conflict = 409, True
                fresh = _pause(pause.pk) if pause else None
                if fresh is not None:
                    pause = fresh
                editable = _mutable(fresh)
                data = request.POST.copy()
                data['revision'] = str(services.configuration_revision())
                data.pop('confirmed', None)
                form = PauseForm(data, pause=fresh, source=source, revision=int(data['revision']))
                form.is_valid()
                _form_error(form, error)
            except (services.PauseError, ValidationError) as error:
                _form_error(form, error)
                status = 400
            else:
                messages.success(request, ('La nouvelle pause a été enregistrée. La pause d’origine est conservée.' if source else
                    'La pause des commandes a été enregistrée. La reprise sera automatique à la date choisie.'))
                return _private(redirect('suspensions:edit', pk=saved.pk))
        else:
            status = 400
    if not editable and request.method == 'GET':
        for field in form.fields.values():
            field.widget.attrs['readonly'] = True
    preview_ends_at = (getattr(form, 'cleaned_data', {}).get('ends_at') if form.is_bound
                       else pause.ends_at if pause else None)
    history = history_context(pause.events.all(), request.GET.get('history_page')) if pause else {}
    return _private(render(request, 'suspensions/form.html', {
        'page_title': 'Dupliquer une pause' if source else ('Modifier la pause' if editable else 'Consulter la pause') if pause else 'Nouvelle pause',
        'current_page': 'edit' if pause else 'create', 'form': form, 'pause': pause, 'source': source,
        'conflict': conflict, 'fresh': fresh, 'editable': editable,
        'conflict_schedule': PurchasePause.objects.filter(stopped_at__isnull=True, ends_at__gt=timezone.now()).order_by('starts_at')[:8] if conflict else [],
        **history, 'default_message': DEFAULT_MESSAGE, 'preview_ends_at': preview_ends_at,
    }, status=status))


@catalog_required
@require_http_methods(['GET', 'POST'])
def create(request):
    return _editor(request)


@catalog_required
@require_http_methods(['GET', 'POST'])
def edit(request, pk):
    return _editor(request, _pause(pk))


@catalog_required
@require_http_methods(['GET', 'POST'])
def duplicate(request, pk):
    # Resolve only the URL source on both GET and POST. Posted identifiers can
    # neither select a different source nor turn this creation into an edit.
    return _editor(request, source=_pause(pk))


@catalog_required
@require_http_methods(['GET', 'POST'])
def action(request, pk, action):
    if action not in ('cancel', 'resume'):
        raise Http404
    pause = _pause(pk)
    allowed = pause.status == ('upcoming' if action == 'cancel' else 'active')
    form = PauseActionForm(request.POST if request.method == 'POST' else None,
        initial={'revision': services.configuration_revision()})
    status, conflict = 200, False
    if request.method == 'POST':
        if not allowed:
            form.add_error(None, 'La situation de cette pause a changé. Cette action ne peut plus être appliquée.')
            status = 409
        elif form.is_valid():
            service = services.cancel_pause if action == 'cancel' else services.resume_pause
            try:
                service(pause.pk, form.cleaned_data['revision'], request.user)
            except services.PauseConflict as error:
                status, conflict = 409, True
                pause = _pause(pk)
                allowed = pause.status == ('upcoming' if action == 'cancel' else 'active')
                form = PauseActionForm({'revision': services.configuration_revision()})
                form.is_valid(); _form_error(form, error)
            except (services.PauseError, ValidationError) as error:
                _form_error(form, error); status = 400
            else:
                messages.success(request, 'La pause programmée a été annulée.' if action == 'cancel' else 'La pause est terminée. Les nouvelles commandes peuvent reprendre.')
                return _private(redirect('suspensions:edit', pk=pause.pk))
        else:
            status = 400
    return _private(render(request, 'suspensions/action.html', {
        'page_title': 'Annuler la pause programmée' if action == 'cancel' else 'Reprendre les commandes maintenant',
        'current_page': 'action', 'pause': pause, 'action': action, 'form': form,
        'allowed': allowed, 'conflict': conflict,
    }, status=status))
