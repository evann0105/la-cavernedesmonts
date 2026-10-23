"""Read-only admin summaries and a small separate customer-facing projection."""
from django.core.exceptions import ObjectDoesNotExist
from .mail import notification_state
from .contacts import effective_email, effective_shipping, locked_country
from .services import FulfillmentError, _order_reason, blocking_issue, recipient_email
from .tracking import tracking_url


STATE_LABELS = {'preparing': 'En préparation', 'ready': 'Prête à expédier', 'shipped': 'Expédiée'}


def _fulfillment(order):
    try:
        return order.fulfillment
    except ObjectDoesNotExist:
        return None


def summary(order):
    fulfillment = _fulfillment(order)
    state = fulfillment.state if fulfillment else 'preparing'
    issue = blocking_issue(order)
    reason = issue['message']
    try:
        recipient_email(order)
        recipient_available = True
        mail_reason = ''
    except FulfillmentError as error:
        recipient_available = False
        mail_reason = str(error)
    ship_reason = reason or mail_reason
    return {
        'fulfillment': fulfillment, 'state': state, 'state_label': STATE_LABELS[state],
        'revision': fulfillment.revision if fulfillment else 0,
        'ready_at': fulfillment.ready_at if fulfillment else None,
        'shipped_at': fulfillment.shipped_at if fulfillment else None,
        'updated_at': fulfillment.updated_at if fulfillment else None,
        'carrier': fulfillment.carrier if fulfillment else 'colissimo',
        'carrier_name': fulfillment.carrier_name if fulfillment else '',
        'carrier_label': fulfillment.carrier_label if fulfillment else 'Colissimo / La Poste',
        'tracking_number': fulfillment.tracking_number if fulfillment else '',
        'tracking_url': tracking_url(fulfillment) if state == 'shipped' else '',
        'blocked_reason': ship_reason,
        'blocked_kind': issue['kind'] or ('email' if mail_reason else ''),
        'blocked_return_id': issue['return_id'],
        'ship_blocked_reason': ship_reason, 'recipient_available': recipient_available,
        'contact_shipping': effective_shipping(order, fulfillment),
        'contact_email': effective_email(order, fulfillment),
        'contact_country_locked': locked_country(order),
        'shipping_is_corrected': bool(fulfillment and fulfillment.delivery_address_override),
        'recipient_is_corrected': bool(fulfillment and fulfillment.notification_email_override),
        'can_correct_contacts': state != 'shipped' and not _order_reason(order),
        'can_ready': state == 'preparing' and not reason,
        'can_reopen': state == 'ready',
        'can_ship': state == 'ready' and not ship_reason,
        'can_update_tracking': state == 'shipped' and recipient_available,
        'can_resend_notice': state == 'shipped' and recipient_available,
        'notice': notification_state(fulfillment),
        'events': fulfillment.events.select_related('actor').all() if fulfillment else [],
    }


def state_for_order(order):
    return summary(order)


def public_status(order):
    """Never expose internal notes, blockers, staff identities or outbox state."""
    fulfillment = _fulfillment(order)
    state = fulfillment.state if fulfillment else 'preparing'
    if order.status not in ('paid', 'demo'):
        return None
    return {
        'state': state, 'state_label': STATE_LABELS[state],
        'ready_at': fulfillment.ready_at if fulfillment else None,
        'shipped_at': fulfillment.shipped_at if fulfillment else None,
        'carrier_label': fulfillment.carrier_label if state == 'shipped' else '',
        'tracking_number': fulfillment.tracking_number if state == 'shipped' else '',
        'tracking_url': tracking_url(fulfillment) if state == 'shipped' else '',
        'is_demo': order.is_demo,
    }
