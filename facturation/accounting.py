"""Read-only monthly cash movements and issued-document indexes.

Payments, refunds and invoice issuance each keep their own date. This is an
export of activity recorded by the shop, not a bank settlement report or FEC.
Amounts stay in integer cents until presentation; unknown tax is never zero.
"""
import csv
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import StringIO
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.utils import timezone

from paiement.models import Order
from retours.models import ReturnRequest
from .models import Invoice


PARIS = ZoneInfo('Europe/Paris')
MONTHS = ('Janvier', 'Février', 'Mars', 'Avril', 'Mai', 'Juin', 'Juillet',
          'Août', 'Septembre', 'Octobre', 'Novembre', 'Décembre')
CENT = Decimal('.01')


def period(year, month):
    """A half-open calendar month in the shop's French timezone."""
    try:
        if isinstance(year, bool) or isinstance(month, bool):
            raise ValueError
        year_text, month_text = str(year), str(month)
        if not year_text.isdigit() or not month_text.isdigit():
            raise ValueError
        year, month = int(year), int(month)
        if not 2000 <= year <= 2100 or not 1 <= month <= 12:
            raise ValueError
    except (TypeError, ValueError, OverflowError) as error:
        raise ValidationError('Choisissez un mois valide entre janvier 2000 et décembre 2100.') from error
    start = datetime(year, month, 1, tzinfo=PARIS)
    end = datetime(year + 1, 1, 1, tzinfo=PARIS) if month == 12 else datetime(year, month + 1, 1, tzinfo=PARIS)
    return {'year': year, 'month': month, 'start': start, 'end': end,
            'label': f'{MONTHS[month - 1]} {year}', 'slug': f'{year:04d}-{month:02d}'}


def euros(cents):
    return Decimal(cents) / 100


def _cents(value):
    return type(value) is int and value >= 0


def _money(value):
    """Accept only exact, finite, non-negative saved monetary values."""
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount != amount.quantize(CENT):
            return None
        return amount
    except (InvalidOperation, ValueError, TypeError):
        return None


def _snapshot(invoice):
    return invoice.snapshot if invoice and isinstance(invoice.snapshot, dict) else {}


def _snapshot_matches(invoice):
    data = _snapshot(invoice)
    return bool(invoice and data.get('number') == invoice.number
                and data.get('order_reference') == str(invoice.order_id))


def _identity(order, invoice):
    # Once issued, retain the buyer identity actually written on the document.
    buyer = _snapshot(invoice).get('buyer') if invoice else order.billing_details
    buyer = buyer if isinstance(buyer, dict) else {}
    return str(buyer.get('name') or ''), str(buyer.get('email') or '') if invoice else str(order.customer_email or '')


def _invoice_tax(invoice, total_cents):
    data = _snapshot(invoice)
    if invoice and not _snapshot_matches(invoice):
        return None, None, '', '', 'Références du document enregistré à vérifier : ventilation HT/TVA non reprise.'
    total, base, vat = (_money(data.get(key)) for key in ('total', 'tax_base', 'tax_amount'))
    mode = data.get('vat_mode')
    rate = data.get('vat_rate', '')
    if (not invoice or mode not in ('standard', 'franchise') or total is None
            or base is None or vat is None or base + vat != total
            or total != euros(total_cents) or (mode == 'franchise' and vat != 0)):
        return None, None, '', '', 'Ventilation HT/TVA indisponible : vérifier la facture et le profil fiscal historique.'
    if mode == 'standard':
        parsed_rate = _money(rate)
        if parsed_rate is None or not 0 < parsed_rate <= 100:
            return None, None, '', '', 'Taux de TVA de la facture à vérifier.'
    return base, vat, mode, str(rate) if mode == 'standard' else '', ''


def _sale_breakdown(order, invoice):
    """Only expose a breakdown that balances the confirmed payment exactly."""
    data = _snapshot(invoice)
    if invoice:
        if not _snapshot_matches(invoice):
            return None, None, None, 'Références du document enregistré à vérifier : ventilation articles/remises/livraison non reprise.'
        gross, discount, shipping, total = (_money(data.get(key)) for key in ('gross', 'discount', 'shipping_amount', 'total'))
        if (None not in (gross, discount, shipping, total) and discount <= gross
                and gross - discount + shipping == total == euros(order.total_paid)):
            return int(gross * 100), int(discount * 100), int(shipping * 100), ''
        return None, None, None, 'Ventilation articles/remises/livraison de la facture à vérifier.'
    items = order.items
    if not isinstance(items, list) or not items or not _cents(order.subtotal) or not _cents(order.return_shipping_amount):
        return None, None, None, 'Ventilation articles/remises/livraison manquante ou incomplète.'
    gross_lines = []
    for item in items:
        if (not isinstance(item, dict) or type(item.get('quantity')) is not int or item['quantity'] < 1
                or not _cents(item.get('unit_amount'))):
            return None, None, None, 'Articles enregistrés à l’achat à vérifier.'
        gross_lines.append(item['quantity'] * item['unit_amount'])
    if sum(gross_lines) != order.subtotal:
        return None, None, None, 'Le sous-total enregistré ne correspond pas aux articles.'
    paid_lines = order.paid_line_totals
    if paid_lines is None and sum(gross_lines) + order.return_shipping_amount == order.total_paid:
        paid_lines = gross_lines
    if (not isinstance(paid_lines, list) or len(paid_lines) != len(gross_lines)
            or not all(_cents(value) and value <= gross for value, gross in zip(paid_lines, gross_lines))
            or sum(paid_lines) + order.return_shipping_amount != order.total_paid):
        return None, None, None, 'La répartition des remises et de la livraison doit être rapprochée du paiement.'
    return order.subtotal, order.subtotal - sum(paid_lines), order.return_shipping_amount, ''


def _sale_row(order):
    invoice = getattr(order, 'invoice', None)
    name, email = _identity(order, invoice)
    gross, discount, shipping, allocation_note = _sale_breakdown(order, invoice)
    base, vat, mode, rate, tax_note = _invoice_tax(invoice, order.total_paid)
    notes = [allocation_note, tax_note]
    if invoice is None:
        notes.insert(0, 'Facture non émise.')
    return {'kind': 'payment', 'date': order.paid_at, 'order': order, 'refund': None,
            'invoice': invoice, 'reference': str(order.pk), 'invoice_number': invoice.number if invoice else '',
            'customer_name': name, 'customer_email': email,
            'amount_cents': order.total_paid, 'amount': euros(order.total_paid),
            'gross_cents': gross, 'discount_cents': discount, 'shipping_cents': shipping,
            'tax_base': base, 'tax_amount': vat, 'vat_mode': mode, 'vat_rate': rate,
            'notes': ' '.join(note for note in notes if note)}


def _refund_row(refund):
    order = refund.order
    invoice = getattr(order, 'invoice', None)
    name, email = _identity(order, invoice)
    notes = ['Remboursement confirmé ; ventilation HT/TVA à rapprocher de l’avoir comptable.']
    lines = list(refund.lines.all())
    shipping = refund.shipping_amount
    # amount includes shipping already. Never add shipping a second time.
    if (not _cents(shipping) or shipping > refund.amount
            or not lines or not all(_cents(line.amount) for line in lines)
            or sum(line.amount for line in lines) + shipping != refund.amount):
        shipping = None
        notes.append('Répartition du remboursement entre articles et livraison à vérifier.')
    if invoice is None:
        notes.append('Facture de la vente non émise.')
    if not order.paid_at or not _cents(order.total_paid):
        notes.append('Données du paiement d’origine incomplètes.')
    return {'kind': 'refund', 'date': refund.refunded_at, 'order': order, 'refund': refund,
            'invoice': invoice, 'reference': str(refund.pk), 'invoice_number': invoice.number if invoice else '',
            'customer_name': name, 'customer_email': email,
            'amount_cents': -refund.amount, 'amount': -euros(refund.amount),
            'gross_cents': None, 'discount_cents': None,
            'shipping_cents': -shipping if shipping is not None else None,
            'tax_base': None, 'tax_amount': None, 'vat_mode': '', 'vat_rate': '',
            'notes': ' '.join(notes)}


def report(year, month):
    selected = period(year, month)
    start, end = selected['start'], selected['end']
    # No bank/provider call, fallback date or current catalogue price is used.
    paid_in_month = list(Order.objects.filter(status='paid', paid_at__gte=start, paid_at__lt=end)
                         .select_related('invoice').order_by('paid_at', 'pk'))
    incomplete = [order for order in paid_in_month if not _cents(order.total_paid)]
    payments = [_sale_row(order) for order in paid_in_month if _cents(order.total_paid)]
    refunds = [_refund_row(refund) for refund in ReturnRequest.objects.filter(
        order__status='paid', refund_state='succeeded', refunded_at__gte=start, refunded_at__lt=end)
        .select_related('order__invoice').prefetch_related('lines').order_by('refunded_at', 'pk')]
    rows = sorted([*payments, *refunds], key=lambda row: (row['date'], row['kind'], row['reference']))
    invoices = list(Invoice.objects.filter(issued_at__gte=start, issued_at__lt=end)
                    .select_related('order').order_by('issued_at', 'number', 'pk'))
    undated_orders = Order.objects.filter(status='paid', paid_at__isnull=True).count()
    undated_refunds = ReturnRequest.objects.filter(order__status='paid', refund_state='succeeded', refunded_at__isnull=True).count()
    warnings = []
    if incomplete:
        warnings.append(f'{len(incomplete)} commande(s) payée(s) du mois sans montant confirmé : exclue(s) des totaux et du fichier des ventes.')
    if undated_orders:
        warnings.append(f'{undated_orders} commande(s) payée(s) sans date de paiement, toutes périodes confondues : impossible de les rattacher à un mois.')
    if undated_refunds:
        warnings.append(f'{undated_refunds} remboursement(s) confirmé(s) sans date, toutes périodes confondues : impossible de les rattacher à un mois.')
    payments_cents = sum(row['amount_cents'] for row in payments)
    refunds_cents = -sum(row['amount_cents'] for row in refunds)
    return {'period': selected, 'rows': rows, 'invoices': invoices,
            'missing_invoice_orders': [order for order in paid_in_month if getattr(order, 'invoice', None) is None],
            'incomplete_orders': incomplete, 'undated_orders_count': undated_orders,
            'undated_refunds_count': undated_refunds, 'warnings': warnings,
            'summary': {'payment_count': len(payments), 'refund_count': len(refunds),
                'invoice_count': len(invoices), 'payments_cents': payments_cents,
                'refunds_cents': refunds_cents, 'net_cents': payments_cents - refunds_cents,
                'payments': euros(payments_cents), 'refunds': euros(refunds_cents),
                'net': euros(payments_cents - refunds_cents)}}


def csv_text(value):
    """Neutralize spreadsheet formulas even after whitespace/control prefixes."""
    text = str(value or '').replace('\x00', '').replace('\r', ' ').replace('\n', ' ').replace('\t', ' ')
    probe = re.sub(r'^[\s\ufeff]+', '', text)
    if probe.startswith(('=', '+', '-', '@')):
        text = "'" + text
    return text


def _amount(value):
    return '' if value is None else format(value, '.2f').replace('.', ',')


def _date(value):
    return timezone.localtime(value, PARIS).strftime('%d/%m/%Y %H:%M:%S') if value else ''


def _csv(header, rows):
    stream = StringIO(newline='')
    writer = csv.writer(stream, delimiter=';', lineterminator='\r\n')
    writer.writerow(header)
    writer.writerows(rows)
    return stream.getvalue().encode('utf-8-sig')


def sales_csv(data):
    """One row per confirmed payment/refund; no duplicate discount movement."""
    header = ('Date opération (Europe/Paris)', 'Type opération', 'Référence opération', 'Référence commande',
              'Numéro facture', 'Date émission facture', 'Client', 'E-mail client',
              'Articles avant remise EUR', 'Remise articles EUR', 'Livraison EUR',
              'Montant mouvement EUR', 'Base HT facture EUR', 'TVA facture EUR',
              'Régime TVA facture', 'Taux TVA facture %', 'Devise', 'Observations')
    rows = []
    for row in data['rows']:
        invoice = row['invoice']
        rows.append((
            _date(row['date']), 'Paiement' if row['kind'] == 'payment' else 'Remboursement',
            csv_text(row['reference']), csv_text(row['order'].pk), csv_text(row['invoice_number']),
            _date(invoice.issued_at) if invoice else '', csv_text(row['customer_name']), csv_text(row['customer_email']),
            *(_amount(euros(row[key])) if row[key] is not None else '' for key in ('gross_cents', 'discount_cents', 'shipping_cents')),
            _amount(row['amount']), _amount(row['tax_base']), _amount(row['tax_amount']),
            csv_text({'standard': 'TVA applicable', 'franchise': 'Franchise en base'}.get(row['vat_mode'], '')),
            csv_text(row['vat_rate']).replace('.', ','), 'EUR', csv_text(row['notes'])))
    return _csv(header, rows)


def invoice_index_csv(invoices):
    header = ('Numéro facture', 'Date émission (Europe/Paris)', 'Référence commande',
              'Client', 'E-mail client', 'Total facture EUR', 'Base HT EUR', 'TVA EUR', 'Devise', 'Observations')
    rows = []
    for invoice in invoices:
        snapshot = _snapshot(invoice)
        buyer = snapshot.get('buyer') if isinstance(snapshot.get('buyer'), dict) else {}
        total = _money(snapshot.get('total'))
        notes = ''
        if total is None:
            base = vat = None
            notes = 'Montant enregistré sur la facture à vérifier.'
        else:
            base, vat, _, _, notes = _invoice_tax(invoice, int(total * 100))
        if snapshot.get('number') != invoice.number or snapshot.get('order_reference') != str(invoice.order_id):
            notes = (notes + ' Références du document enregistré à vérifier.').strip()
        rows.append((csv_text(invoice.number), _date(invoice.issued_at), csv_text(invoice.order_id),
            csv_text(buyer.get('name')), csv_text(buyer.get('email')), _amount(total), _amount(base),
            _amount(vat), 'EUR', csv_text(notes)))
    return _csv(header, rows)
