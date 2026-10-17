"""Signed, short-lived confirmation of the exact closure quantities."""
from django.core import signing
from stocks.models import Variant
from .services import ReturnError


def snapshot(record, lines=None, variants=None):
    lines = list(lines) if lines is not None else list(record.lines.order_by('pk'))
    if variants is None:
        variants = {variant.pk:variant for variant in Variant.objects.filter(
            pk__in=[line.variant_id for line in lines if line.variant_id])}
    return {'amount':record.amount, 'refund_state':record.refund_state,
            'refund_id':record.stripe_refund_id, 'return_state':record.return_state, 'state':record.state,
            'stock_labels':sorted([[variant.pk, variant.product_id, variant.size, variant.color]
                for variant in variants.values()]),
            'lines':sorted([[line.pk, line.quantity, line.received, line.restocked, line.variant_id] for line in lines])}


def prepare(record, user, form):
    if record.refund_state != 'succeeded':
        raise ReturnError('Le remboursement doit être confirmé avant de préparer la clôture.')
    received, restock = form.counts('received'), form.counts('restock')
    state = snapshot(record, form.rows)
    labels = {row[0]:row for row in state['stock_labels']}
    items = record.order.items if isinstance(record.order.items, list) else []
    rows = []
    for line in form.rows:
        stock = labels.get(line.variant_id)
        item = items[line.item_index] if 0 <= line.item_index < len(items) else {}
        item = item if isinstance(item, dict) else {}
        label = ''
        if (stock and stock[1] == item.get('product') and str(stock[0]) == str(item.get('variant'))
                and (stock[2], stock[3]) != (line.size, line.color)):
            label = f'{stock[2]} · {stock[3]}'
        rows.append({'line':line, 'received':received[line.pk], 'restock':restock[line.pk],
                     'current_stock_label':label})
    payload = {'record':str(record.pk), 'actor':user.pk, 'snapshot':state,
               'received':received, 'restock':restock}
    token = signing.dumps(payload, salt='return-completion-v1', compress=True)
    return {'token':token, 'received_total':sum(received.values()), 'restock_total':sum(restock.values()),
            'not_restocked':sum(received.values())-sum(restock.values()),
            'rows':rows,
            'correction_fields':[{'name':field.name, 'value':form.cleaned_data[field.name]} for field in form if field.name != 'confirm']}


def unpack(token, record, user):
    try:
        payload = signing.loads(token, salt='return-completion-v1', max_age=900)
        if payload['record'] != str(record.pk) or payload['actor'] != user.pk:
            raise ValueError
        return {int(key):value for key,value in payload['received'].items()}, {int(key):value for key,value in payload['restock'].items()}, payload['snapshot']
    except (signing.BadSignature, ValueError, TypeError, KeyError):
        raise ReturnError('La confirmation a expiré ou ne correspond pas à ce dossier. Vérifiez à nouveau les quantités avant de terminer.')
