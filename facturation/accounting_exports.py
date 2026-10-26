"""Read-only, all-or-nothing accounting archives for a chosen period.

Callers pass the complete period report, before any on-screen search or
pagination. Generating copies never issues invoices or changes their snapshots.
"""
import re
from decimal import Decimal
from tempfile import SpooledTemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile

from django.utils import timezone

from .accounting import PARIS, invoice_index_csv, sales_csv
from .services import DocumentError


MAX_ARCHIVE_INVOICES = 500
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024


def _period_copy(data, monthly, other):
    return monthly if data['period'].get('kind', 'month') == 'month' else other


class InvoiceArchiveError(DocumentError):
    """An issued document requiring attention, with its corrective-link target."""

    def __init__(self, message, invoice):
        super().__init__(message)
        self.invoice = invoice
        self.order_id = invoice.order_id
        self.number = invoice.number


def _text(value):
    # Customer-provided names must not impersonate another recap line.
    return ' '.join(str(value or '').split())


def _date(value):
    return timezone.localtime(value, PARIS).strftime('%d/%m/%Y %H:%M:%S') if value else 'date non renseignée'


def _amount(value):
    return format(value, '.2f').replace('.', ',') + ' EUR'


def _invoice_names(invoices):
    names = set()
    for invoice in invoices:
        number = re.sub(r'[^A-Za-z0-9_-]', '_', invoice.number).strip('_')
        name = 'factures/' + (number or 'facture') + '.pdf'
        if name in names:
            raise InvoiceArchiveError(
                'Les noms de deux factures doivent être vérifiés avant le téléchargement groupé. '
                f'Facture concernée : {_text(invoice.number)}. Aucun dossier partiel n’a été téléchargé.', invoice)
        names.add(name)
        yield invoice, name


def _missing_orders(data):
    """Include an old sale without invoice when refunded during the chosen period."""
    orders = {str(order.pk): order for order in data['missing_invoice_orders']}
    for row in data['rows']:
        if row['invoice'] is None:
            orders.setdefault(str(row['order'].pk), row['order'])
    return list(orders.values())


def _order_line(order):
    buyer = order.billing_details if isinstance(order.billing_details, dict) else {}
    return (f'- Commande {_text(order.pk)} | {_text(buyer.get("name")) or "Client non renseigné"}'
            f' | paiement : {_date(order.paid_at)} (Europe/Paris)')


def _recap(data):
    selected, summary = data['period'], data['summary']
    missing = _missing_orders(data)
    notes = [row for row in data['rows'] if row['notes']]
    needs_attention = bool(missing or data['incomplete_orders'] or data['warnings'] or notes
                           or (data['rows'] and not data['invoices']))
    generated = timezone.localtime(timezone.now(), PARIS)
    invoice_total = sum((Decimal(invoice.snapshot['total']) for invoice in data['invoices']), Decimal(0))
    invoice_base = sum((Decimal(invoice.snapshot['tax_base']) for invoice in data['invoices']), Decimal(0))
    invoice_tax = sum((Decimal(invoice.snapshot['tax_amount']) for invoice in data['invoices']), Decimal(0))
    lines = [
        'LA CAVERNE DES MONTS — RÉCAPITULATIF COMPTABLE',
        f'Période : {selected["label"]} (Europe/Paris).',
        f'Du {_date(selected["start"])} inclus au {_date(selected["end"])} exclu.',
        f'Généré le {generated.strftime("%d/%m/%Y à %H:%M:%S %Z (UTC%z)")} — Europe/Paris.',
        'État du dossier : ' + ('À COMPLÉTER' if needs_attention else 'EXPORT GÉNÉRÉ'),
        _period_copy(data, 'Périmètre : totalité du mois choisi, indépendamment de la recherche et des filtres affichés.', 'Périmètre : totalité de la période choisie, indépendamment de la recherche et des filtres affichés.'),
        '',
        _period_copy(data, '1. ENCAISSEMENTS ET REMBOURSEMENTS CONFIRMÉS DU MOIS', '1. ENCAISSEMENTS ET REMBOURSEMENTS CONFIRMÉS DE LA PÉRIODE'),
        f'Paiements : {summary["payment_count"]} | total encaissé : {_amount(summary["payments"])}.',
        f'Remboursements : {summary["refund_count"]} | total remboursé : {_amount(summary["refunds"])}.',
        f'Solde des mouvements (paiements moins remboursements) : {_amount(summary["net"])}.',
        'Les remboursements apparaissent avec un montant négatif dans le fichier des ventes.',
        'Ces montants correspondent aux opérations confirmées du site, avant rapprochement bancaire.',
        '',
        _period_copy(data, '2. FACTURES ÉMISES PENDANT LE MOIS', '2. FACTURES ÉMISES PENDANT LA PÉRIODE'),
        f'Factures PDF jointes : {len(data["invoices"])}.',
        f'Total des factures émises, livraison comprise : {_amount(invoice_total)}.',
        f'Base HT des factures émises : {_amount(invoice_base)}.',
        f'TVA des factures émises : {_amount(invoice_tax)}.',
        'Ces totaux reprennent les données historiques des factures, sans déduire les remboursements.',
        'La date d’émission d’une facture est distincte de la date de paiement.',
        _period_copy(data, 'Une facture émise ce mois peut concerner un paiement d’un autre mois, et réciproquement.', 'Une facture émise pendant la période peut concerner un paiement d’une autre période, et réciproquement.'),
        'Ne pas additionner les totaux des factures aux encaissements : ce sont deux lectures des mêmes ventes.',
    ]
    if not data['invoices']:
        lines.append('Aucune facture n’a été émise pendant cette période. Le dossier ne contient aucun PDF de facture.')

    included = {invoice.number for invoice in data['invoices']}
    outside = {row['invoice'].number: row['invoice'] for row in data['rows']
               if row['invoice'] is not None and row['invoice'].number not in included}
    if outside:
        lines += ['', _period_copy(data, 'Factures liées aux mouvements mais émises hors du mois (non jointes) :', 'Factures liées aux mouvements mais émises hors de la période (non jointes) :')]
        for invoice in outside.values():
            lines.append(f'- {_text(invoice.number)} | commande {_text(invoice.order_id)} | émission : {_date(invoice.issued_at)}.')
        lines.append('Télécharger ces factures depuis leur mois d’émission ou depuis la commande correspondante.')

    lines += ['', '3. PIÈCES ET INFORMATIONS À COMPLÉTER', f'Commandes sans facture émise : {len(missing)}.']
    if missing:
        lines += [_order_line(order) for order in missing]
        lines.append('Ces commandes ne sont pas représentées par un PDF. Aucune facture n’a été créée par cet export.')
    lines.append(_period_copy(data, 'Commandes payées du mois', 'Commandes payées de la période')
                 + f' sans montant confirmé : {len(data["incomplete_orders"])}.')
    if data['incomplete_orders']:
        lines += [_order_line(order) for order in data['incomplete_orders']]
        lines.append('Ces commandes sont exclues des totaux des mouvements et du fichier des ventes ; leurs montants ne sont pas supposés nuls.')
    if data['warnings']:
        lines += ['', 'Alertes du rapport :', *('- ' + _text(warning) for warning in data['warnings'])]
    if notes:
        lines += ['', 'Points de contrôle par opération :']
        for row in notes:
            kind = 'Paiement' if row['kind'] == 'payment' else 'Remboursement'
            lines.append(f'- {kind} {_text(row["reference"])} | commande {_text(row["order"].pk)}'
                         f' | {_date(row["date"])} : {_text(row["notes"])}')
    if not needs_attention:
        lines.append('Aucune pièce manquante ni anomalie signalée par le rapport du site pour cette sélection.')
    lines += [
        '', '4. UTILISATION DU DOSSIER',
        'Les lignes, remises, frais de livraison et données fiscales sont détaillés dans les PDF et les CSV.',
        'Une valeur HT/TVA vide signifie indisponible, jamais zéro. Vérifier les observations de chaque mouvement.',
        'Rapprocher les remboursements de leurs avoirs comptables et les paiements des relevés du prestataire.',
        'Ce dossier ne constitue ni un FEC, ni un relevé bancaire, ni une certification de comptabilité complète.',
        'Les opérations de démonstration, paiements non confirmés et remboursements non confirmés sont exclus.',
        '',
    ]
    return '\n'.join(lines).encode('utf-8')


def _readme(data, *, dossier):
    lines = [
        'LA CAVERNE DES MONTS - ' + ('DOSSIER COMPTABLE' if dossier else 'FACTURES EMISES'),
        f'Période : {data["period"]["label"]} (Europe/Paris).',
        f'{len(data["invoices"])} facture(s) PDF, classée(s) par numéro.',
        '',
        _period_copy(data, 'La sélection des PDF suit la date d’émission de chaque facture, indépendamment du mois de paiement.', 'La sélection des PDF suit la date d’émission de chaque facture, indépendamment de la date de paiement.'),
        'Les PDF reprennent les données conservées lors de leur émission. Aucun nouveau numéro n’a été attribué.',
        'Les commandes sans facture émise ne sont pas incluses parmi les PDF.',
        'Les remboursements sont des opérations distinctes : ils ne modifient pas les factures déjà émises.',
        _period_copy(data, 'Le fichier index-factures.csv répertorie uniquement les factures émises pendant le mois.', 'Le fichier index-factures.csv répertorie uniquement les factures émises pendant la période.'),
        'Les CSV se lisent dans Excel ou un tableur (UTF-8, séparateur point-virgule, décimales avec une virgule).',
    ]
    if dossier:
        lines += [
            f'Le fichier ventes-{data["period"]["slug"]}.csv contient les paiements et remboursements confirmés '
            + _period_copy(data, 'du mois.', 'de la période.'),
            _period_copy(data, 'Une facture d’un autre mois peut y être référencée sans que son PDF soit joint.', 'Une facture émise hors de la période peut y être référencée sans que son PDF soit joint.'),
            'Lire RECAPITULATIF.txt avant transmission : il indique les totaux distincts, pièces manquantes et contrôles nécessaires.',
            'Un dossier À COMPLÉTER contient les éléments disponibles, mais ne doit pas être présenté comme complet.',
            _period_copy(data, 'Le téléchargement reprend la totalité du mois choisi, sans appliquer la recherche ni les filtres de l’écran.', 'Le téléchargement reprend la totalité de la période choisie, sans appliquer la recherche ni les filtres de l’écran.'),
        ]
    else:
        lines.append(_period_copy(data, 'Pour les encaissements et remboursements du mois, utilisez aussi « Exporter les ventes » ou le dossier comptable.', 'Pour les encaissements et remboursements de la période, utilisez aussi « Exporter les ventes » ou le dossier comptable.'))
    lines += [
        'Les remboursements doivent être rapprochés de leurs avoirs comptables ; les montants HT/TVA inconnus restent vides.',
        'Ces exports concernent les opérations du site ; ils ne constituent pas un FEC ni un relevé bancaire.',
        '',
    ]
    return '\n'.join(lines).encode('utf-8')


def _archive(data, *, dossier):
    from .pdf import invoice_pdf

    invoices = data['invoices']
    if dossier:
        if not (data['rows'] or invoices or data['incomplete_orders']):
            raise DocumentError(_period_copy(data, 'Aucune activité ni facture pour ce mois. Choisissez une période contenant des opérations ou des documents.', 'Aucune activité ni facture pour cette période. Choisissez une période contenant des opérations ou des documents.'))
    elif not invoices:
        raise DocumentError(_period_copy(data, 'Aucune facture n’a été émise pendant ce mois. Consultez les commandes à facturer dans « Comptabilité et exports ».', 'Aucune facture n’a été émise pendant cette période. Consultez les commandes à facturer dans « Comptabilité et exports ».'))
    if len(invoices) > MAX_ARCHIVE_INVOICES:
        raise DocumentError(_period_copy(data, 'Ce mois dépasse la limite de 500 factures par archive. Les documents restent accessibles dans « Commandes et factures ».', 'Cette période dépasse la limite de 500 factures par archive. Choisissez une période plus courte ou consultez les documents dans « Commandes et factures ».'))

    named_invoices = list(_invoice_names(invoices))
    stream = SpooledTemporaryFile(max_size=4 * 1024 * 1024, mode='w+b')
    try:
        with ZipFile(stream, 'w', compression=ZIP_DEFLATED) as archive:
            total_size = 0

            def write(name, content):
                nonlocal total_size
                total_size += len(content)
                if total_size > MAX_ARCHIVE_BYTES:
                    raise DocumentError(_period_copy(data, 'Les documents de ce mois dépassent 50 Mo. Téléchargez-les séparément depuis « Commandes et factures ».', 'Les documents de cette période dépassent 50 Mo. Choisissez une période plus courte ou téléchargez-les séparément depuis « Commandes et factures ».'))
                archive.writestr(name, content)

            for invoice, name in named_invoices:
                try:
                    pdf = invoice_pdf(invoice)
                except DocumentError as error:
                    raise InvoiceArchiveError(
                        f'La facture {_text(invoice.number)} nécessite une vérification. '
                        f'Aucun dossier partiel n’a été téléchargé. {error}', invoice) from error
                write(name, pdf)
            write('index-factures.csv', invoice_index_csv(invoices))
            if dossier:
                write(f'ventes-{data["period"]["slug"]}.csv', sales_csv(data))
                write('RECAPITULATIF.txt', _recap(data))
            write('A-LIRE.txt', _readme(data, dossier=dossier))
        stream.seek(0)
        return stream
    except Exception:
        stream.close()
        raise


def invoice_archive(data):
    """Return an open file containing copies of all invoices issued in the chosen period."""
    return _archive(data, dossier=False)


def dossier_archive(data):
    """Return one open ZIP of the complete period's invoices, movements and recap."""
    return _archive(data, dossier=True)
