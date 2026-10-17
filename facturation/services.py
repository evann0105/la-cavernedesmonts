"""Printable purchase data and one-time emission; no Stripe calls or money transfers."""
import json
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
from django.core.exceptions import PermissionDenied
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone
from paiement.models import Order
from .models import Invoice, InvoiceSequence
from .profile import profile_blockers


class DocumentError(ValueError):
    pass


def _cents(value):
    return type(value) is int and value >= 0


def _euros(value):
    return None if value is None else Decimal(value) / 100


def _address(value):
    if not isinstance(value, dict):
        return {'name': '', 'address': {}}
    address = value.get('address')
    address = address if isinstance(address, dict) else {}
    return {
        'name': str(value.get('name') or '').strip(),
        'address': {key: str(address.get(key) or '').strip() for key in
                    ('line1', 'line2', 'postal_code', 'city', 'state', 'country')},
    }


def _complete_buyer(value):
    return bool(value['name'] and all(value['address'].get(key) for key in ('line1', 'postal_code', 'city', 'country')))


def _display_date(value):
    if value is None:
        return ''
    if timezone.is_aware(value):
        value = timezone.localtime(value)
    return value.strftime('%d/%m/%Y à %H:%M')


def build_preview(order):
    """Return template data. Money is Decimal EUR, unknown amounts are None.

    This is read-only: it neither allocates a number nor fills a historical tax
    profile from current settings. ``final_ready`` is separate from an estimate.
    """
    from espace.legal import profile as legal_profile
    errors = []
    historical = getattr(order, 'invoice_profile', None)
    historical = historical if isinstance(historical, dict) else {}
    if not historical:
        errors.append('Le profil fiscal au moment de cette commande manque ; aucun document comptable ne peut être reconstitué automatiquement.')
        profile_errors = errors[:]
    else:
        profile_errors = profile_blockers(historical)
        errors.extend(profile_errors)
    seller = deepcopy(historical.get('seller') or legal_profile())
    buyer = _address(getattr(order, 'billing_details', {}))
    buyer['email'] = str(order.customer_email or '')
    shipping = _address(order.shipping_details)
    if not _complete_buyer(buyer):
        errors.append('Le nom et l’adresse de facturation enregistrés à l’achat sont incomplets. Demandez une vérification du paiement à la boutique.')
    if order.status != 'paid':
        errors.append('Seule une commande dont le paiement réel est confirmé peut donner lieu à une facture définitive.')
    if order.is_demo:
        errors.append('Une simulation ne produit aucune facture comptable.')
    if not order.paid_at:
        errors.append('La date de confirmation du paiement manque.')

    raw_items = order.items if isinstance(order.items, list) else []
    rows = []
    gross_cents = []
    items_valid = bool(raw_items)
    for item in raw_items:
        if not isinstance(item, dict):
            items_valid = False
            continue
        quantity = item.get('quantity')
        unit = item.get('unit_amount')
        if (type(quantity) is not int or quantity <= 0 or not _cents(unit)
                or not isinstance(item.get('name'), str) or not item['name'].strip()):
            items_valid = False
            continue
        gross = unit * quantity
        gross_cents.append(gross)
        rows.append({
            'name': item['name'], 'size': str(item.get('size') or ''),
            'color': str(item.get('color') or ''), 'quantity': quantity,
            'unit': _euros(unit), 'gross': _euros(gross),
            'discount': None, 'net': None, 'ht': None, 'vat': None,
            'unit_ht': None, 'gross_ht': None, 'discount_ht': None,
        })
    if not items_valid:
        errors.append('Les articles, quantités et prix enregistrés dans cette commande doivent être vérifiés.')
    if not _cents(order.subtotal) or sum(gross_cents) != order.subtotal:
        errors.append('Le sous-total ne correspond pas aux articles enregistrés à l’achat.')

    shipping_cents = order.return_shipping_amount
    shipping_known = _cents(shipping_cents)
    paid = order.total_paid
    paid_known = _cents(paid)
    totals = order.paid_line_totals
    # Old orders with no discount have an exact, provable allocation without
    # contacting Stripe. Any other missing allocation remains blocked.
    if totals is None and items_valid and shipping_known and paid_known and sum(gross_cents) + shipping_cents == paid:
        totals = gross_cents[:]
    verified = (
        items_valid and len(rows) == len(raw_items)
        and isinstance(totals, list) and len(totals) == len(rows)
        and all(_cents(value) for value in totals)
        and shipping_known and paid_known
        and sum(totals) + shipping_cents == paid
        and all(net <= gross for net, gross in zip(totals, gross_cents))
        and _cents(order.subtotal) and sum(gross_cents) == order.subtotal
    )
    if not shipping_known:
        errors.append('Les frais de livraison effectivement payés doivent être rapprochés du paiement.')
    if not verified:
        errors.append('Les montants réellement payés par article, leurs remises et la livraison doivent correspondre exactement au paiement avant facturation.')
    if verified:
        for row, net, gross in zip(rows, totals, gross_cents):
            row.update(net=_euros(net), discount=_euros(gross - net))

    mode = historical.get('vat_mode', '') if not profile_errors else ''
    rate = historical.get('vat_rate', '') if mode == 'standard' else ''
    shipping_ht = shipping_vat = tax_base = tax_amount = None
    if verified and mode:
        divisor = Decimal(1) + (Decimal(rate) / 100 if mode == 'standard' else Decimal(0))
        for row in rows:
            row['ht'] = (row['net'] / divisor).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
            row['unit_ht'] = (row['unit'] / divisor).quantize(Decimal('.0001'), rounding=ROUND_HALF_UP)
            row['gross_ht'] = (row['gross'] / divisor).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
            row['discount_ht'] = row['gross_ht'] - row['ht']
            row['vat'] = row['net'] - row['ht']
        shipping_ht = (_euros(shipping_cents) / divisor).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        shipping_vat = _euros(shipping_cents) - shipping_ht
        tax_base = sum((row['ht'] for row in rows), Decimal(0)) + shipping_ht
        tax_amount = sum((row['vat'] for row in rows), Decimal(0)) + shipping_vat

    return {
        'seller': seller, 'buyer': buyer, 'shipping': shipping,
        'lines': rows, 'gross': _euros(sum(gross_cents)) if items_valid else None,
        'discount': _euros(sum(gross_cents) - sum(totals)) if verified else None,
        'shipping_amount': _euros(shipping_cents) if shipping_known else None,
        'shipping_ht': shipping_ht, 'shipping_vat': shipping_vat,
        'total': _euros(paid) if paid_known else (_euros(order.subtotal) if _cents(order.subtotal) else None),
        'tax_base': tax_base, 'tax_amount': tax_amount,
        'vat_mode': mode, 'vat_rate': rate,
        'amounts_verified': bool(verified), 'shipping_known': shipping_known,
        'final_ready': not errors, 'blockers': list(dict.fromkeys(errors)),
        'is_demo': order.is_demo, 'order_reference': str(order.pk),
        'ordered_at': order.created_at, 'paid_at': order.paid_at,
        'ordered_at_display': _display_date(order.created_at),
        'paid_at_display': _display_date(order.paid_at),
        'document_note': 'Cette facture conserve le montant de la vente initiale. Les remboursements éventuels font l’objet d’avoirs distincts ; ils ne modifient pas la facture émise.',
    }


def issue(order_id, user):
    """Issue once after an explicit staff confirmation, using purchase snapshots."""
    from retours.access import manager
    from connexion.email_verification import email_access_allowed
    if user is None or not manager(user) or not email_access_allowed(user):
        raise PermissionDenied
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        existing = Invoice.objects.filter(order_id=order.pk).first()
        if existing:
            return existing
        data = build_preview(order)
        if not data['final_ready']:
            raise DocumentError(' '.join(data['blockers']))
        year = timezone.localdate().year
        while True:
            InvoiceSequence.objects.get_or_create(year=year)
            sequence = InvoiceSequence.objects.select_for_update().get(year=year)
            # Timestamp after acquiring the sequence: a waiting transaction
            # must not issue a later number with an earlier date.
            issued_at = timezone.now()
            current_year = timezone.localtime(issued_at).year
            if current_year == year:
                break
            year = current_year
        number = f'WEB-{year}-{sequence.next_number:06d}'
        if Invoice.objects.filter(number=number).exists():
            raise DocumentError('La séquence de facturation doit être rapprochée des numéros déjà émis avec le comptable.')
        data.update(number=number, issued_at=issued_at, issued_at_display=_display_date(issued_at),
                    profile=deepcopy(order.invoice_profile))
        # Store only JSON-native values, keeping decimal strings exact.
        snapshot = json.loads(json.dumps(data, cls=DjangoJSONEncoder, ensure_ascii=False))
        invoice = Invoice.objects.create(
            order=order, number=number, issued_at=issued_at, snapshot=snapshot,
            issued_by_identifier=str(user.pk),
        )
        sequence.next_number += 1
        sequence.save(update_fields=['next_number'])
        return invoice
