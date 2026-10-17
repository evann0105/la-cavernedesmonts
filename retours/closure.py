"""Signed, short-lived confirmation of the exact closure quantities."""
from django.core import signing
from .services import ReturnError


def snapshot(record, lines=None):
    lines = list(lines) if lines is not None else list(record.lines.order_by('pk'))
    return {'amount':record.amount, 'refund_state':record.refund_state,
            'refund_id':record.stripe_refund_id, 'return_state':record.return_state, 'state':record.state,
            'lines':sorted([[line.pk, line.quantity, line.received, line.restocked, line.variant_id] for line in lines])}


def prepare(record, user, form):
    if record.refund_state != 'succeeded':
        raise ReturnError('Le remboursement doit être confirmé avant de préparer la clôture.')
    received, restock = form.counts('received'), form.counts('restock')
    payload = {'record':str(record.pk), 'actor':user.pk, 'snapshot':snapshot(record),
               'received':received, 'restock':restock}
    token = signing.dumps(payload, salt='return-completion-v1', compress=True)
    return {'token':token, 'received_total':sum(received.values()), 'restock_total':sum(restock.values()),
            'not_restocked':sum(received.values())-sum(restock.values()),
            'rows':[{'line':line, 'received':received[line.pk], 'restock':restock[line.pk]} for line in form.rows],
            'correction_fields':[{'name':field.name, 'value':form.cleaned_data[field.name]} for field in form if field.name != 'confirm']}


def unpack(token, record, user):
    try:
        payload = signing.loads(token, salt='return-completion-v1', max_age=900)
        if payload['record'] != str(record.pk) or payload['actor'] != user.pk:
            raise ValueError
        return {int(key):value for key,value in payload['received'].items()}, {int(key):value for key,value in payload['restock'].items()}, payload['snapshot']
    except (signing.BadSignature, ValueError, TypeError, KeyError):
        raise ReturnError('La confirmation a expiré ou ne correspond pas à ce dossier. Vérifiez à nouveau les quantités avant de terminer.')
