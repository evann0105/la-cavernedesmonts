"""Private monthly downloads: reading accounting records never issues documents."""
import re
from tempfile import SpooledTemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile

from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import FileResponse, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from retours.access import returns_required
from .accounting import MONTHS, PARIS, invoice_index_csv, report, sales_csv
from .services import DocumentError
from .views import confidential


MAX_ARCHIVE_INVOICES = 500
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024


def selected_report(request):
    today = timezone.localtime(timezone.now(), PARIS).date()
    return report(request.GET.get('annee', today.year), request.GET.get('mois', today.month))


def error_response(request, message, status=400):
    return confidential(render(request, 'facturation/accounting_error.html', {'error': message}, status=status))


@returns_required
@require_GET
def overview(request):
    try:
        data = selected_report(request)
    except ValidationError as error:
        return error_response(request, ' '.join(error.messages))
    return confidential(render(request, 'facturation/accounting.html', {
        'report': data, 'period': data['period'], 'summary': data['summary'],
        'months': list(enumerate(MONTHS, 1)),
        'movements': Paginator(data['rows'], 25).get_page(request.GET.get('page')),
        'documents': Paginator(data['invoices'], 25).get_page(request.GET.get('factures_page')),
        'missing_invoices': data['missing_invoice_orders'][:10],
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


def invoice_archive(data):
    """Generate all documents before serving the archive, without public files."""
    from .pdf import invoice_pdf
    invoices = data['invoices']
    if not invoices:
        raise DocumentError('Aucune facture n’a été émise pendant ce mois. Consultez les commandes à facturer dans « Comptabilité et exports ».')
    if len(invoices) > MAX_ARCHIVE_INVOICES:
        raise DocumentError('Ce mois dépasse la limite de 500 factures par archive. Les documents restent accessibles dans « Commandes et factures ».')
    stream = SpooledTemporaryFile(max_size=4 * 1024 * 1024, mode='w+b')
    try:
        with ZipFile(stream, 'w', compression=ZIP_DEFLATED) as archive:
            total_size = 0
            names = set()
            for invoice in invoices:
                number = re.sub(r'[^A-Za-z0-9_-]', '_', invoice.number).strip('_')
                name = 'factures/' + (number or 'facture') + '.pdf'
                if name in names:
                    raise DocumentError('Les noms de deux factures doivent être vérifiés avant le téléchargement groupé.')
                names.add(name)
                try:
                    pdf = invoice_pdf(invoice)
                except DocumentError as error:
                    raise DocumentError(f'La facture {invoice.number} nécessite une vérification. Aucun dossier partiel n’a été téléchargé. {error}') from error
                total_size += len(pdf)
                if total_size > MAX_ARCHIVE_BYTES:
                    raise DocumentError('Les documents de ce mois dépassent 50 Mo. Téléchargez-les séparément depuis « Commandes et factures ».')
                archive.writestr(name, pdf)
            archive.writestr('index-factures.csv', invoice_index_csv(invoices))
            archive.writestr('A-LIRE.txt', (
                'LA CAVERNE DES MONTS - FACTURES EMISES\n'
                f'Période : {data["period"]["label"]} (Europe/Paris).\n'
                f'{len(invoices)} facture(s) PDF, classée(s) par numéro.\n\n'
                'La sélection suit la date d’émission de chaque facture, indépendamment du mois de paiement.\n'
                'Les PDF reprennent les données conservées lors de leur émission. Aucun nouveau numéro n’a été attribué.\n'
                'Les commandes sans facture émise ne sont pas incluses.\n'
                'Les remboursements sont des opérations distinctes : ils ne modifient pas les factures déjà émises.\n'
                'Le fichier index-factures.csv se lit dans Excel ou un tableur (UTF-8, séparateur point-virgule).\n'
                'Pour les encaissements et remboursements du mois, utilisez aussi « Exporter les ventes ».\n'
                'Ces exports concernent les opérations du site ; ils ne constituent pas un FEC ni un relevé bancaire.\n'
            ).encode('utf-8'))
        stream.seek(0)
        return stream
    except Exception:
        stream.close()
        raise


@returns_required
@require_GET
def export_invoices(request):
    try:
        data = selected_report(request)
        stream = invoice_archive(data)
    except ValidationError as error:
        return error_response(request, ' '.join(error.messages))
    except DocumentError as error:
        return error_response(request, str(error), status=409)
    response = FileResponse(stream, as_attachment=True, filename=f'factures-{data["period"]["slug"]}.zip', content_type='application/zip')
    return confidential(response)
