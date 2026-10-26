"""Private export log and explicitly declared hand-offs, without sending mail."""
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from retours.access import returns_required
from .accounting import PARIS
from .accounting_history import export_history_queryset, transmission_service
from .accounting_history_forms import ExportTransmissionForm
from .accounting_views import pagination_links
from .views import confidential


@returns_required
@require_GET
def history(request):
    state = request.GET.get('state', '')
    state = state if state in ('pending', 'sent') else ''
    kind = request.GET.get('kind', '')
    kind = kind if kind in ('bundle', 'sales', 'invoices') else ''
    records = export_history_queryset(state=state)
    if kind:
        records = records.filter(kind=kind)
    page = Paginator(records, 25).get_page(request.GET.get('page'))
    return confidential(render(request, 'facturation/accounting_history.html', {
        'exports': page, 'state': state, 'kind': kind,
        'pagination': pagination_links(page, 'page', 'exports', {'state': state, 'kind': kind}),
    }))


def detail_response(request, record, form=None, status=200):
    today = timezone.localdate(timezone.now(), PARIS)
    if form is None:
        form = ExportTransmissionForm(initial={
            'action': 'reopen' if record.is_transmitted else 'sent',
            'expected_revision': record.revision, 'transmitted_on': today})
    form.fields['transmitted_on'].widget.attrs.update({
        'min': timezone.localdate(record.generated_at, PARIS).isoformat(), 'max': today.isoformat()})
    route = {'bundle': 'accounting_bundle', 'sales': 'sales_export', 'invoices': 'invoice_archive'}[record.kind]
    query = urlencode(record.period_params)
    response = confidential(render(request, 'facturation/accounting_export_detail.html', {
        'export': record, 'events': list(record.transmissions.all()), 'form': form,
        'period_url': reverse('facturation:accounting') + '?' + query,
        'recreate_url': reverse('facturation:' + route) + '?' + query,
    }, status=status))
    # Chromium sends Origin: null for POST forms on no-referrer pages. Keep
    # first-party form origins while suppressing referrers to other sites.
    response['Referrer-Policy'] = 'same-origin'
    return response


@returns_required
@require_GET
def detail(request, pk):
    record = get_object_or_404(export_history_queryset(), pk=pk)
    return detail_response(request, record)


@returns_required
@require_POST
def transmission(request, pk):
    record = get_object_or_404(export_history_queryset(), pk=pk)
    form = ExportTransmissionForm(request.POST)
    if form.is_valid():
        data = form.cleaned_data
        try:
            transmission_service(record.pk, request.user, action=data['action'],
                sent_on=data.get('transmitted_on'), recipient=data['recipient'], note=data['note'],
                expected_revision=data['expected_revision'])
        except ValidationError as error:
            form.add_error(None, error)
            return detail_response(request, record, form, status=409 if getattr(error, 'code', '') == 'stale' else 400)
        messages.success(request, 'Transmission notée. Aucun e-mail n’a été envoyé.' if data['action'] == 'sent'
                         else 'Le fichier est à nouveau indiqué « À transmettre ». La correction reste dans l’historique.')
        return confidential(HttpResponseRedirect(reverse('facturation:accounting_export_detail', args=[pk])))
    return detail_response(request, record, form, status=400)
