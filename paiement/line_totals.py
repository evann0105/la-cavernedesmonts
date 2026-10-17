"""Exact paid merchandise totals, matched to immutable order item indexes."""
import stripe
from django.conf import settings
from stocks.reservations import StockUnavailable


def cents(value):
    if type(value) is not int or value < 0:
        raise StockUnavailable('Le détail des montants du paiement doit être vérifié.')
    return value


def payment_confirmed(session):
    return session.get('payment_status') == 'paid' or (
        session.get('payment_status') == 'no_payment_required' and
        session.get('status') == 'complete' and session.get('amount_total') == 0)


def item_name(item):
    return f"{item['name']} — {item.get('size', '')} · {item.get('color', '')}"


def validate_totals(order, totals, paid, shipping):
    if not isinstance(totals, list) or len(totals) != len(order.items):
        raise StockUnavailable('Le détail des remises par article est incomplet.')
    for value in totals:
        cents(value)
    if sum(totals) + cents(shipping) != cents(paid):
        raise StockUnavailable('Les montants par article ne correspondent pas au total payé.')
    return totals


def capture_line_totals(order, session):
    """Session and line items must come from server-side Stripe retrievals."""
    details = session.get('total_details') or {}
    shipping = cents(details.get('amount_shipping', 0))
    paid = cents(session.get('amount_total'))
    gross = [int(i['unit_amount']) * int(i['quantity']) for i in order.items]
    # Without discounts or added taxes, the purchase snapshot is already exact.
    if not details.get('amount_discount', 0) and sum(gross) + shipping == paid:
        return validate_totals(order, gross, paid, shipping)

    totals = [None] * len(order.items)
    seen_ids = set()
    cursor = None
    discount_sum = 0
    modern = any((row.get('price_data', {}).get('product_data', {}).get('metadata') or {}).get('order_id')
                 for row in order.checkout_payload.get('line_items', []))
    while True:
        params = {'limit':100, 'expand':['data.price.product'], 'api_key':settings.STRIPE_SECRET_KEY}
        if cursor:params['starting_after'] = cursor
        page = stripe.checkout.Session.list_line_items(session['id'], **params)
        rows = page.get('data') or []
        if not rows:
            raise StockUnavailable('Le détail des articles payés est incomplet.')
        for row in rows:
            if not row.get('id') or row['id'] in seen_ids or len(seen_ids) >= len(order.items):
                raise StockUnavailable('Le détail des articles payés est incohérent.')
            seen_ids.add(row['id'])
            price = row.get('price') or {}
            product = price.get('product') or {}
            metadata = product.get('metadata', {}) if isinstance(product, dict) else {}
            if metadata.get('order_id') == str(order.pk):
                try:index = int(metadata['order_item'])
                except (KeyError, TypeError, ValueError):
                    raise StockUnavailable('Un article payé ne correspond pas à la commande.')
                candidates = [index] if 0 <= index < len(order.items) else []
            elif metadata.get('order_id') or modern:
                candidates = []
            else:
                # Older checkouts did not carry our index. Match only a unique
                # immutable description, unit price and quantity; never list order.
                candidates = [n for n, item in enumerate(order.items)
                    if row.get('description') in (item_name(item), f"{item['name']} — {item.get('size', '')}")
                    and price.get('unit_amount') == item['unit_amount']
                    and row.get('quantity') == item['quantity']]
            if len(candidates) != 1:
                raise StockUnavailable('Le détail des remises doit être rapproché des articles de la commande.')
            index = candidates[0]
            item = order.items[index]
            if (totals[index] is not None or row.get('currency') != 'eur' or
                row.get('quantity') != item['quantity'] or price.get('unit_amount') != item['unit_amount'] or
                cents(row.get('amount_subtotal')) != gross[index]):
                raise StockUnavailable('Un montant ou une quantité ne correspond pas à l’article acheté.')
            totals[index] = cents(row.get('amount_total'))
            discount_sum += cents(row.get('amount_discount'))
        if not page.get('has_more'):break
        cursor = rows[-1]['id']
    if any(value is None for value in totals):
        raise StockUnavailable('Le détail des articles payés est incomplet.')
    if 'amount_discount' in details and discount_sum != details['amount_discount']:
        raise StockUnavailable('Le détail des remises ne correspond pas au paiement.')
    return validate_totals(order, totals, paid, shipping)


def recover_line_totals(order):
    """Caller holds the order lock. Never rewrite existing return records."""
    if not settings.STRIPE_SECRET_KEY or not order.stripe_session_id:
        raise StockUnavailable('Le détail des remises de cette ancienne commande doit être vérifié avant remboursement.')
    session = stripe.checkout.Session.retrieve(order.stripe_session_id, api_key=settings.STRIPE_SECRET_KEY)
    if (session.get('id') != order.stripe_session_id or not payment_confirmed(session) or
        session.get('currency') != 'eur' or session.get('amount_total') != order.total_paid or
        session.get('amount_subtotal') != order.subtotal or session.get('client_reference_id') != str(order.pk) or
        session.get('metadata', {}).get('order_id') != str(order.pk)):
        raise StockUnavailable('Le paiement de cette ancienne commande doit être vérifié.')
    totals = capture_line_totals(order, session)
    shipping = (session.get('total_details') or {}).get('amount_shipping', 0)
    if order.return_shipping_amount is not None and shipping != order.return_shipping_amount:
        raise StockUnavailable('Les frais de livraison ne correspondent pas au paiement.')
    order.paid_line_totals = totals
    order.return_shipping_amount = shipping
    order.save(update_fields=['paid_line_totals', 'return_shipping_amount'])
    return totals
