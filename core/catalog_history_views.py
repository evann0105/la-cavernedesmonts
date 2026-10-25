"""Readable catalogue history and explicit, previewed reversal of a batch."""
from urllib.parse import urlencode

from django import forms
from django.contrib import messages
from django.contrib.admin.models import LogEntry
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q, TextField
from django.db.models.functions import Cast
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from .catalog_access import catalog_required
from .catalog_history import (CatalogUndoConflict, apply_catalog_undo,
                              history_rows, prepare_catalog_undo)
from .models import CatalogChange, Category, Product


class UndoForm(forms.Form):
    confirmation = forms.CharField(max_length=150000, widget=forms.HiddenInput)


def _private(response):
    response['X-Robots-Tag'] = 'noindex, nofollow'
    response['Cache-Control'] = 'private, no-store, max-age=0'
    return response


@catalog_required
@require_GET
def catalog_history(request):
    query = request.GET.get('q', '').strip()[:100]
    state = request.GET.get('etat', '')
    if state not in ('active', 'undone', 'undo'):
        state = ''
    changes = CatalogChange.objects.select_related('actor', 'undone_by', 'reverses')
    if query:
        changes = changes.alias(search_items=Cast('items', TextField())).filter(
            Q(actor_label__icontains=query) | Q(search_items__icontains=query))
    if state == 'active':
        changes = changes.filter(kind='bulk', undone_at__isnull=True)
    elif state == 'undone':
        changes = changes.filter(kind='bulk', undone_at__isnull=False)
    elif state == 'undo':
        changes = changes.filter(kind='undo')
    params = {key: value for key, value in {'q': query, 'etat': state}.items() if value}
    return _private(render(request, 'core/catalog/history/list.html', {
        'page_obj': Paginator(changes.order_by('-created_at', '-pk'), 20).get_page(request.GET.get('page')),
        'query': query, 'state': state, 'filter_query': urlencode(params),
    }))


@catalog_required
@require_GET
def catalog_history_detail(request, pk):
    change = get_object_or_404(CatalogChange.objects.select_related('actor', 'undone_by', 'reverses'), pk=pk)
    return _private(render(request, 'core/catalog/history/detail.html', {
        'change': change, 'rows': history_rows(change),
    }))


@catalog_required
@require_http_methods(['GET', 'POST'])
def catalog_history_undo(request, pk):
    change = get_object_or_404(CatalogChange, pk=pk)
    form = UndoForm(request.POST if request.method == 'POST' else None)
    rows, errors, status = [], [], 200
    if request.method == 'POST':
        if form.is_valid():
            try:
                change, count, already_undone = apply_catalog_undo(
                    request.user, form.cleaned_data['confirmation'], expected_change_id=pk)
            except ValidationError as exc:
                errors = exc.messages
                status = 409 if isinstance(exc, CatalogUndoConflict) else 400
            else:
                messages.success(request, 'Cette modification avait déjà été annulée.' if already_undone else
                                 f'Modification annulée : {count} article(s) rétabli(s).')
                return redirect('core:catalog_history_detail', pk=change.pk)
        else:
            errors = ['La confirmation est manquante ou invalide. Reprenez l’aperçu avant de continuer.']
            status = 400
    else:
        try:
            change, rows, token = prepare_catalog_undo(request.user, pk)
            form = UndoForm(initial={'confirmation': token})
        except ValidationError as exc:
            errors = exc.messages
            status = 409 if isinstance(exc, CatalogUndoConflict) else 400
    return _private(render(request, 'core/catalog/history/undo.html', {
        'change': change, 'rows': rows, 'form': form, 'errors': errors,
    }, status=status))


@catalog_required
@require_GET
def catalog_activity(request):
    query = request.GET.get('q', '').strip()[:100]
    product_type = ContentType.objects.get_for_model(Product)
    category_type = ContentType.objects.get_for_model(Category)
    entries = LogEntry.objects.filter(content_type__in=[product_type, category_type]).select_related('user')
    if query:
        entries = entries.filter(Q(object_repr__icontains=query) | Q(change_message__icontains=query) |
                                 Q(user__username__icontains=query))
    page = Paginator(entries.order_by('-action_time', '-pk'), 25).get_page(request.GET.get('page'))
    for entry in page:
        entry.catalog_kind = 'Rubrique' if entry.content_type_id == category_type.pk else 'Produit'
    return _private(render(request, 'core/catalog/history/activity.html', {
        'page_obj': page, 'query': query, 'filter_query': urlencode({'q': query}) if query else '',
    }))
