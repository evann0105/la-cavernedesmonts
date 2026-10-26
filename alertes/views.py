from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from core.catalog_access import catalog_required
from retours.access import returns_required
from .access import can_configure, authorize_configuration
from .forms import AlertSettingsForm, StockRuleForm
from .monitor import all_rows, delivery_context, retry
from .services import get_settings
from .settings_service import save_settings
from .stock import stock_snapshot, update_rule


def private_render(request, template, context, status=200):
    response = render(request, 'alertes/' + template, {
        'can_configure_alerts': can_configure(request.user), **delivery_context(), **context}, status=status)
    response['Cache-Control'] = 'private, no-store, max-age=0'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    response['Referrer-Policy'] = 'same-origin'
    return response


def form_error(form, error):
    if hasattr(error, 'message_dict'):
        for name, errors in error.message_dict.items():
            for message in errors:
                form.add_error(name if name in form.fields else None, message)
    else:
        form.add_error(None, error)


@returns_required
@require_GET
def emails(request):
    rows = all_rows(request.user)
    state = request.GET.get('etat', 'attention')
    filters = {'attention': {'failed', 'blocked'}, 'pending': {'pending'},
               'processed': {'sent_local', 'sent_provider', 'unknown'}, 'cancelled': {'cancelled'}}
    if state not in (*filters, 'all'):
        state = 'attention'
    tabs = [('attention', 'À vérifier'), ('pending', 'En attente'), ('processed', 'Traités'), ('all', 'Tous')]
    counts = {key: sum(row['status'] in filters[key] for row in rows) if key != 'all' else len(rows)
              for key, _ in tabs}
    filtered = [row for row in rows if state == 'all' or row['status'] in filters[state]]
    return private_render(request, 'emails.html', {'active': 'emails', 'state': state,
        'tabs': [{'key': key, 'label': label, 'count': counts[key]} for key, label in tabs],
        'page_obj': Paginator(filtered, 25).get_page(request.GET.get('page')),
        'filter_query': urlencode({'etat': state}), 'preferences': get_settings() if can_configure(request.user) else None})


@returns_required
@require_http_methods(['GET', 'POST'])
def email_detail(request, source, pk):
    row = next((row for row in all_rows(request.user) if row['source'] == source and str(row['pk']) == pk), None)
    if row is None:
        raise Http404
    error, status = '', 200
    if request.method == 'POST':
        try:
            attempts = int(request.POST.get('attempts', ''))
            if attempts < 0:
                raise ValueError
            confirmation = None
            if row.get('requires_recipient_confirmation'):
                if request.POST.get('confirm_recipient') != 'yes':
                    raise ValidationError('Confirmez l’adresse affichée avant de relancer cet ancien message.')
                confirmation = request.POST.get('recipient_confirmation_token', '')
            row = retry(request.user, source, pk, attempts, recipient_confirmation_token=confirmation)
        except (ValueError, ValidationError) as exc:
            error = ' · '.join(exc.messages) if isinstance(exc, ValidationError) else 'Rechargez le suivi du message avant de le relancer.'
            status = 409
        else:
            if row['status'] == 'sent_local':
                messages.success(request, 'Le message est conservé dans la boîte e-mail de test. Aucun envoi à une adresse réelle.')
            elif row['status'] == 'sent_provider':
                messages.success(request, 'Le message a été transmis au service d’envoi.')
            elif row['status'] == 'failed':
                messages.error(request, 'La tentative n’a pas abouti. Le message reste dans « À vérifier ».')
            else:
                messages.info(request, 'Le suivi du message a été actualisé : ' + row['status_label'] + '.')
            return redirect('alertes:email_detail', source=source, pk=pk)
    return private_render(request, 'email_detail.html', {'active': 'emails', 'row': row, 'error': error}, status=status)


@returns_required
@require_http_methods(['GET', 'POST'])
def settings_view(request):
    authorize_configuration(request.user)
    current = get_settings()
    initial = {name: getattr(current, name) for name in AlertSettingsForm.base_fields}
    initial['revision'] = 0 if current._state.adding else current.revision
    form = AlertSettingsForm(request.POST if request.method == 'POST' else None, initial=initial)
    status = 200
    if request.method == 'POST':
        if form.is_valid():
            try:
                save_settings(request.user, form.cleaned_data)
            except ValidationError as exc:
                form_error(form, exc)
                status = 409
            else:
                messages.success(request, 'Les alertes sont configurées. Les nouvelles commandes suivront ces réglages ; les stocks seront vérifiés lors du prochain contrôle automatique.')
                return redirect('alertes:settings')
        else:
            status = 400
    return private_render(request, 'settings.html', {'active': 'settings', 'form': form}, status=status)


@catalog_required
@require_GET
def stocks(request):
    query = request.GET.get('q', '').strip()[:150]
    snapshot = stock_snapshot(query=query)
    state = request.GET.get('etat', 'low')
    states = {'low', 'unknown', 'disabled', 'all'}
    if state not in states:
        state = 'low'
    rows = snapshot['rows']
    if state == 'low':
        rows = [row for row in rows if row['is_low']]
    elif state != 'all':
        rows = [row for row in rows if row['state'] == state]
    if state in ('unknown', 'all'):
        rows = [*rows, *({'missing_stock': True, 'title': product.name,
                         'url': reverse('stocks:product', args=[product.pk])}
                        for product in snapshot['missing_products'])]
    return private_render(request, 'stocks.html', {'active': 'stocks', 'state': state, 'query': query,
        'to_count': snapshot['unknown_count'] + snapshot['missing_products_count'],
        'snapshot': snapshot, 'page_obj': Paginator(rows, 25).get_page(request.GET.get('page')),
        'filter_query': urlencode({'etat': state, 'q': query})})


@catalog_required
@require_http_methods(['GET', 'POST'])
def stock_rule(request, variant_id):
    row = next((row for row in stock_snapshot()['rows'] if row['variant_id'] == variant_id), None)
    if row is None:
        raise Http404
    form = StockRuleForm(request.POST if request.method == 'POST' else None, initial={
        'revision': row['revision'], 'threshold': row['override_threshold'], 'enabled': row['enabled']})
    status = 200
    if request.method == 'POST':
        if form.is_valid():
            try:
                update_rule(request.user, variant_id, **form.cleaned_data)
            except ValidationError as exc:
                form_error(form, exc)
                status = 409
            else:
                messages.success(request, 'Le seuil de cette taille et couleur a été enregistré.')
                return redirect('alertes:stocks')
        else:
            status = 400
    return private_render(request, 'stock_rule.html', {'active': 'stocks', 'row': row, 'form': form}, status=status)
