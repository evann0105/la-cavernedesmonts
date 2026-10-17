"""Price-free packing slip from the original order, without carrier requests."""
from copy import deepcopy

from espace.legal import profile as legal_profile
from .services import _address, _complete_buyer, _display_date


def build_delivery_slip(order):
    historical = order.invoice_profile if isinstance(order.invoice_profile, dict) else {}
    shipping = _address(order.shipping_details)
    rows = []
    raw_items = order.items if isinstance(order.items, list) else []
    for item in raw_items:
        if (not isinstance(item, dict) or not isinstance(item.get('name'), str)
                or not item['name'].strip() or type(item.get('quantity')) is not int
                or item['quantity'] <= 0):
            continue
        rows.append({
            'name': item['name'], 'size': str(item.get('size') or ''),
            'color': str(item.get('color') or ''), 'quantity': item['quantity'],
        })
    return {
        'seller': deepcopy(historical.get('seller') or legal_profile()),
        'shipping': shipping, 'shipping_complete': _complete_buyer(shipping),
        'lines': rows, 'items_verified': bool(rows) and len(rows) == len(raw_items),
        'quantity': sum(row['quantity'] for row in rows),
        'order_reference': str(order.pk), 'ordered_at_display': _display_date(order.created_at),
        'is_demo': order.is_demo,
    }
