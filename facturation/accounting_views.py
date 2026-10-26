"""Private monthly downloads: reading accounting records never issues documents."""
from urllib.parse import urlencode

from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import FileResponse, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from retours.access import returns_required
from .accounting import MONTHS, PARIS, report, sales_csv
from .accounting_exports import MAX_ARCHIVE_INVOICES, dossier_archive, invoice_archive
from .accounting_search import filter_report
from .services import DocumentError
from .views import confidential


def selected_report(request):
    today = timezone.localtime(timezone.now(), PARIS).date()
    return report(request.GET.get('annee', today.year), request.GET.get('mois', today.month))


def period_url(selected):
    return reverse('facturation:accounting') + '?' + urlencode({
        'annee': selected['year'], 'mois': selected['month']})


def error_response(request, message, status=400, data=None, invoice=None):
    return confidential(render(request, 'facturation/accounting_error.html', {
        'error': message, 'invoice_to_check': invoice,
        'period': data['period'] if data else None,
        'overview_url': period_url(data['period']) if data else reverse('facturation:accounting'),
    }, status=status))


def pagination_links(page, parameter, anchor, params):
    def link(number):
        return '?' + urlencode({**params, parameter: number}) + '#' + anchor
    return {'page': page,
            'previous_url': link(page.previous_page_number()) if page.has_previous() else '',
            'next_url': link(page.next_page_number()) if page.has_next() else ''}


@returns_required
@require_GET
def overview(request):
    try:
        data = selected_report(request)
    except ValidationError as error:
        return error_response(request, ' '.join(error.messages))
    filtered = filter_report(data, request.GET.get('q', ''), request.GET.get('type', ''))
    movements = Paginator(filtered['rows'], 25).get_page(request.GET.get('page'))
    documents = Paginator(filtered['invoices'], 25).get_page(request.GET.get('factures_page'))
    missing = Paginator(filtered['missing_invoice_orders'], 25).get_page(request.GET.get('a_facturer_page'))
    params = {'annee': data['period']['year'], 'mois': data['period']['month'],
              'q': filtered['query'], 'type': filtered['kind'],
              'page': movements.number, 'factures_page': documents.number, 'a_facturer_page': missing.number}
    return confidential(render(request, 'facturation/accounting.html', {
        'report': data, 'period': data['period'], 'summary': data['summary'],
        'months': list(enumerate(MONTHS, 1)),
        'movements': movements, 'documents': documents, 'missing_invoices': missing,
        'movements_pagination': pagination_links(movements, 'page', 'mouvements', params),
        'documents_pagination': pagination_links(documents, 'factures_page', 'factures', params),
        'missing_pagination': pagination_links(missing, 'a_facturer_page', 'a-facturer', params),
        'query': filtered['query'], 'kind': filtered['kind'], 'is_filtered': filtered['is_filtered'],
        'reset_url': period_url(data['period']) + '#rechercher',
        'has_dossier': bool(data['rows'] or data['invoices'] or data['incomplete_orders']),
        'archive_too_large': len(data['invoices']) > MAX_ARCHIVE_INVOICES,
    }))


@returns_required
@require_GET
def export_sales(request):
    try:
        data = selected_report(request)
    except ValidationError as error:
        return error_response(request, ' '.join(error.messages))
    response = HttpResponse(sales_csv(data), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="ventes-{data["period"]["slug"]}.csv"'
    return confidential(response)


def archive_response(request, builder, filename):
    data = None
    try:
        data = selected_report(request)
        stream = builder(data)
    except ValidationError as error:
        return error_response(request, ' '.join(error.messages))
    except DocumentError as error:
        return error_response(request, str(error), status=409, data=data,
                              invoice=getattr(error, 'invoice', None))
    return confidential(FileResponse(stream, as_attachment=True,
        filename=f'{filename}-{data["period"]["slug"]}.zip', content_type='application/zip'))


@returns_required
@require_GET
def export_invoices(request):
    return archive_response(request, invoice_archive, 'factures')


@returns_required
@require_GET
def export_bundle(request):
    return archive_response(request, dossier_archive, 'dossier-comptable')
