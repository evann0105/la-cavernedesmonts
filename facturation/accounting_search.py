"""Filter the already selected month's private lists, never the export totals.

Encrypted contact values are matched in Python after authorized report loading.
No extra query, document issuance or financial operation is performed here.
"""
import unicodedata
import uuid


def normalize_query(value):
    """Keep a short display value; case/accent folding is only for matching."""
    return str(value or '').strip()[:100]


def _fold(value):
    return ''.join(character for character in unicodedata.normalize('NFKD', str(value or '').casefold())
                   if not unicodedata.combining(character))


def _references(value):
    """A full UUID also supports the displayed eight-character reference."""
    text = str(value or '')
    try:
        return (text, uuid.UUID(text).hex)
    except (ValueError, AttributeError):
        return (text,)


def _matches(words, fields):
    haystack = _fold(' '.join(str(value or '') for value in fields))
    return all(word in haystack for word in words)


def _row_fields(row):
    # report() already freezes these identity fields to the issued invoice.
    # Do not inspect today's order/account contact values as additional aliases.
    values = [row.get('customer_name', ''), row.get('customer_email', ''), row.get('invoice_number', '')]
    values.extend(_references(row.get('reference', '')))
    order, refund = row.get('order'), row.get('refund')
    if order is not None:
        values.extend(_references(order.pk))
    if refund is not None:
        values.extend(_references(refund.pk))
    return values


def _invoice_fields(invoice):
    snapshot = invoice.snapshot if isinstance(invoice.snapshot, dict) else {}
    buyer = snapshot.get('buyer') if isinstance(snapshot.get('buyer'), dict) else {}
    return [invoice.number, buyer.get('name', ''), buyer.get('email', ''), *_references(invoice.order_id)]


def _missing_fields(order):
    buyer = order.billing_details if isinstance(order.billing_details, dict) else {}
    return [buyer.get('name', ''), order.customer_email, *_references(order.pk)]


def filter_report(data, query='', kind=''):
    """Return new display lists while leaving the original monthly report intact.

    All entered words must match one of the allowed visible references/contact
    fields, ignoring accents and case. Symbols stay literal, not SQL patterns
    or regular expressions. Kind applies only to the movement list. Downloads
    must continue to use the original ``data`` report for the complete month.
    """
    query = normalize_query(query)
    kind = kind if kind in ('payment', 'refund') else ''
    words = _fold(query).split()
    return {
        'query': query, 'kind': kind, 'is_filtered': bool(query or kind),
        'rows': [row for row in data['rows']
                 if (not kind or row.get('kind') == kind) and _matches(words, _row_fields(row))],
        'invoices': [invoice for invoice in data['invoices'] if _matches(words, _invoice_fields(invoice))],
        'missing_invoice_orders': [order for order in data['missing_invoice_orders']
                                   if _matches(words, _missing_fields(order))],
    }
