"""Private, read-only history and explicitly confirmed restoration screens."""
from importlib import import_module
from urllib.parse import urlencode

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q, OuterRef, Subquery
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_GET, require_http_methods

from core.catalog_access import catalog_required
from core.models import Product

from . import services
from .exceptions import HistoryConflict, HistoryError
from .forms import HistoryFilterForm, RestoreForm
from .media import asset_warnings
from .models import HistoryVersion
from .presentation import product_changes


def _private(response):
    response['Cache-Control'] = 'private, no-store, max-age=0'
    response['Referrer-Policy'] = 'same-origin'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def _version(pk):
    if not 0 < pk <= 9223372036854775807:
        raise Http404
    return get_object_or_404(HistoryVersion.objects.select_related('actor', 'restored_from'), pk=pk)


def _backend(version):
    return import_module('historique.products' if version.scope == 'product' else 'historique.homepage')


def _history_url(version):
    return reverse('historique:product', args=[version.object_id]) if version.scope == 'product' else reverse('historique:homepage')


def _differences(version, before_snapshot, *, before_version=None):
    try:
        if version.scope == 'product':
            return product_changes(before_snapshot, version.snapshot,
                before_version=before_version, after_version=version)
        return _backend(version).describe_changes(before_snapshot, version.snapshot,
            before_version=before_version, after_version=version)
    except HistoryError as error:
        return [{'title': 'Comparaison indisponible', 'rows': [{'label': 'Contenu conservé',
            'before': 'Comparaison non disponible', 'after': str(error), 'kind': 'text'}]}]


def _warnings(version):
    warnings = asset_warnings(version)
    try:
        warnings.extend(_backend(version).restore_warnings(version))
    except HistoryError as error:
        warnings.append(str(error))
    return list(dict.fromkeys(warnings))


def _context(version, *, check_current=False):
    exists = version.scope != 'product' or Product.objects.filter(pk=version.object_id).exists()
    is_current = False
    if exists and check_current:
        try:
            is_current = services.fingerprint(services.snapshot_current(version)) == version.fingerprint
        except HistoryError:
            pass
    return {'version': version, 'history_url': _history_url(version), 'is_current': is_current,
            'object_deleted': not exists,
            'object_url': services.object_url(version) if exists else '', 'scope': version.scope}


def _listing(request, *, scope=None, object_id=None, object_label=''):
    entries = HistoryVersion.objects.select_related('restored_from').defer('snapshot', 'assets', 'restored_from__snapshot', 'restored_from__assets')
    if scope:
        entries = entries.filter(scope=scope)
    if object_id is not None:
        entries = entries.filter(object_id=object_id)
    actors = list(entries.exclude(actor_label='').order_by('actor_label').values_list('actor_label', flat=True).distinct())
    data = request.GET.copy()
    if scope:
        data['scope'] = scope
    form = HistoryFilterForm(data, actors=actors, fixed_scope=scope)
    if form.is_valid():
        filters = form.cleaned_data
        if filters.get('scope'):
            entries = entries.filter(scope=filters['scope'])
        if filters.get('q'):
            query = filters['q']
            entries = entries.filter(Q(object_label__icontains=query) | Q(label__icontains=query) | Q(actor_label__icontains=query))
        if filters.get('source'):
            entries = entries.filter(source=filters['source'])
        if filters.get('actor'):
            entries = entries.filter(actor_label=filters['actor'])
        if filters.get('since'):
            entries = entries.filter(created_at__date__gte=filters['since'])
        if filters.get('until'):
            entries = entries.filter(created_at__date__lte=filters['until'])
    else:
        filters = {}
        entries = entries.none()
    params = {key: value.isoformat() if hasattr(value, 'isoformat') else value
              for key, value in filters.items() if value and not (key == 'scope' and scope)}
    latest_sequence = HistoryVersion.objects.filter(scope=OuterRef('scope'), object_id=OuterRef('object_id')).order_by('-sequence').values('sequence')[:1]
    entries = entries.annotate(latest_sequence=Subquery(latest_sequence))
    page = Paginator(entries.order_by('-created_at', '-pk'), 20).get_page(request.GET.get('page'))
    counts = {row['scope']: row['total'] for row in HistoryVersion.objects.values('scope').annotate(total=Count('pk'))}
    object_url = (reverse('core:catalog_edit', args=[object_id]) if scope == 'product' and object_id else
                  reverse('core:catalog_homepage') if scope == 'homepage' else '')
    if scope == 'product' and object_id and not Product.objects.filter(pk=object_id).exists():
        object_url = ''
    return _private(render(request, 'historique/list.html', {
        'form': form, 'page_obj': page, 'filter_query': urlencode(params), 'has_filters': bool(params),
        'scope': scope or '', 'object_id': object_id, 'object_label': object_label,
        'object_url': object_url, 'counts': counts, 'show_hub': not scope,
        'list_url': request.path,
        'page_title': 'Historique de ' + object_label if object_label else 'Historique et récupération',
        'extra_filters_open': bool(data.get('actor') or data.get('since') or data.get('until')),
    }))


@catalog_required
@require_GET
def index(request):
    return _listing(request)


@catalog_required
@require_GET
def product(request, pk):
    if not 0 < pk <= 9223372036854775807:
        raise Http404
    item = Product.objects.filter(pk=pk).first()
    previous = HistoryVersion.objects.filter(scope='product', object_id=pk).first()
    if item is None and previous is None:
        raise Http404
    return _listing(request, scope='product', object_id=pk,
        object_label=item.name if item else previous.object_label)


@catalog_required
@require_GET
def homepage(request):
    return _listing(request, scope='homepage', object_id=1, object_label='l’accueil')


@catalog_required
@require_GET
def detail(request, pk):
    version = _version(pk)
    previous = HistoryVersion.objects.filter(scope=version.scope, object_id=version.object_id,
        sequence__lt=version.sequence).order_by('-sequence').first()
    return _private(render(request, 'historique/detail.html', {
        **_context(version, check_current=True), 'previous': previous, 'baseline': previous is None,
        'sections': _differences(version, previous.snapshot if previous else None, before_version=previous),
        'warnings': _warnings(version),
        'restorations': version.restorations.order_by('-created_at')[:5],
        'page_title': f'Version {version.sequence} · {version.object_label}',
    }))


@catalog_required
@require_GET
def preview(request, pk):
    version = _version(pk)
    return _private(render(request, 'historique/preview.html', {
        **_context(version, check_current=True), 'warnings': _warnings(version),
        'page_title': f'Aperçu · {version.object_label}',
    }))


@catalog_required
@require_GET
@xframe_options_sameorigin
def frame(request, pk):
    version = _version(pk)
    request._history_preview = True
    try:
        response = _backend(version).preview_response(request, version)
    except HistoryError as error:
        response = render(request, 'historique/frame_error.html', {'error': str(error)}, status=409)
    response['Content-Security-Policy'] = "sandbox allow-same-origin; script-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'self'"
    return _private(response)


@catalog_required
@require_http_methods(['GET', 'POST'])
def restore(request, pk):
    version = _version(pk)
    form = RestoreForm(request.POST if request.method == 'POST' else None)
    error, status, prepared, refreshed = '', 200, None, False
    if request.method == 'POST':
        if form.is_valid():
            try:
                result = services.apply_restore(request.user, form.cleaned_data['confirmation'], expected_entry_id=version.pk)
            except HistoryConflict as exc:
                error, status = str(exc), 409
            except HistoryError as exc:
                error, status = str(exc), 400
            else:
                messages.success(request, 'La version a été rétablie. L’état précédent reste conservé dans l’historique.')
                return _private(redirect('historique:detail', pk=result.pk))
        else:
            status = 400
    try:
        prepared = services.prepare_restore(request.user, version)
    except HistoryError as exc:
        error = error + (' ' if error else '') + str(exc)
        if request.method == 'POST' and status == 200:
            status = 409
    else:
        if request.method == 'GET':
            form = RestoreForm(initial={'confirmation': prepared['token']})
        elif error:
            # A failed confirmation requires a fresh review, never a blind retry.
            form = RestoreForm({'confirmation': prepared['token']})
            refreshed = True
        else:
            # Keep validation errors and the unchecked checkbox; the renewed
            # token is valid for the comparison rendered on this response.
            data = form.data.copy()
            data['confirmation'] = prepared['token']
            data.pop('confirmed', None)
            form = RestoreForm(data)
    return _private(render(request, 'historique/restore.html', {
        **_context(version), 'form': form, 'error': error, 'prepared': prepared,
        'refreshed': refreshed, 'warnings': prepared['warnings'] if prepared else [],
        'sections': _differences(version, prepared['current_snapshot']) if prepared else [],
        'page_title': f'Rétablir la version {version.sequence}',
    }, status=status))
