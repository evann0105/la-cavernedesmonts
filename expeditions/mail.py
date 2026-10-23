"""Queue frozen dispatch-recipient notifications; delivery belongs to the worker."""
from django.utils import timezone
from retours.access import order_link
from retours.mail import enqueue
from retours.models import ReturnMail
from .tracking import tracking_url


KINDS = ('shipping_ready', 'shipping', 'shipping_update')


def notice_prefix(order):
    return f'fulfillment-{order.pk}-'


def _notices(fulfillment):
    return list(ReturnMail.objects.filter(order_id=fulfillment.order_id,
        key__startswith=f'fulfillment-{fulfillment.order_id}-').order_by('-pk'))


def supersede_pending(fulfillment):
    """Keep the old payload as history, but prevent an obsolete queued notice."""
    for notice in _notices(fulfillment):
        if notice.sent_at is None and notice.superseded_at is None and not notice.payload.get('fulfillment_superseded'):
            notice.payload = {**notice.payload, 'fulfillment_superseded': True}
            notice.superseded_at = timezone.now()
            notice.save(update_fields=['payload', 'superseded_at'])


def enqueue_notice(fulfillment, kind, resend=False):
    from .contacts import effective_email
    order = fulfillment.order
    suffix = 'resend' if resend else kind
    key = f'{notice_prefix(order)}{suffix}-r{fulfillment.revision}'
    existing = ReturnMail.objects.filter(key=key).first()
    if existing is not None:
        return existing, False
    if resend:
        current_notices = [notice for notice in _notices(fulfillment)
            if notice.superseded_at is None and not notice.payload.get('fulfillment_superseded')
            and notice.payload.get('fulfillment_revision') == fulfillment.revision]
        for notice in current_notices:
            if (notice.sent_at is None and notice.superseded_at is None and not notice.payload.get('fulfillment_superseded')
                    and notice.payload.get('fulfillment_revision') == fulfillment.revision):
                return notice, False
        # Repeating an ordinary dispatch notice must not claim a correction.
        # A corrected tracking reference, however, keeps its correction wording.
        previous_kind = current_notices[0].payload.get('kind') if current_notices else 'shipping'
        kind = previous_kind if previous_kind in ('shipping', 'shipping_update') else 'shipping'
    details = f'Commande {order.pk}\n'
    if kind == 'shipping_ready':
        details += 'Votre commande a été préparée par la boutique. Son expédition n’est pas encore enregistrée.\n'
    else:
        details += ('Les informations de suivi ont été corrigées par la boutique.\n'
                    if kind == 'shipping_update' else 'La boutique a enregistré l’expédition de votre commande.\n')
        if fulfillment.shipped_at:
            details += 'Expédition enregistrée le ' + timezone.localtime(fulfillment.shipped_at).strftime('%d/%m/%Y à %H:%M %Z') + '.\n'
        details += f'Transporteur : {fulfillment.carrier_label}\nNuméro de suivi : {fulfillment.tracking_number}\n'
        details += 'Le numéro est renseigné par la boutique ; le suivi du transporteur fait foi pour son acheminement.\n'
    for item in order.items:
        details += f"{item['quantity']} × {item['name']} — {item.get('size', '')} {item.get('color', '')}\n"
    url = tracking_url(fulfillment) if kind != 'shipping_ready' else ''
    details += '\nVotre commande et vos retours : ' + order_link(order)
    enqueue(order, key, kind, details, recipient=effective_email(order, fulfillment),
            url=url or order_link(order))
    notice = ReturnMail.objects.get(key=key)
    notice.payload = {**notice.payload, 'fulfillment_id': fulfillment.pk,
                      'fulfillment_revision': fulfillment.revision}
    notice.save(update_fields=['payload'])
    return notice, True


def notification_state(fulfillment):
    empty = {'pending': False, 'processed_at': None, 'mode': 'unknown', 'failed': False, 'attempts': 0}
    if fulfillment is None:
        return empty
    notices = [notice for notice in _notices(fulfillment)
               if notice.superseded_at is None and not notice.payload.get('fulfillment_superseded')]
    if not notices:
        return empty
    latest = notices[0]
    pending = latest.sent_at is None
    mode = latest.payload.get('delivery_mode', 'unknown') if not pending else 'unknown'
    if mode not in ('local', 'email'):
        mode = 'unknown'
    return {'pending': pending, 'processed_at': latest.sent_at, 'mode': mode,
            'failed': pending and latest.attempts > 0, 'attempts': latest.attempts}
