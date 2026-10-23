"""Only the customer's shipment information, never seller notes or events."""
from django.core.exceptions import ObjectDoesNotExist


def public_status(order):
    if order.status not in ('paid', 'demo'):
        return {'show': False}
    try:
        record = order.fulfillment
    except ObjectDoesNotExist:
        record = None
    state = record.state if record else 'preparing'
    labels = {'preparing': 'En préparation', 'ready': 'Prête à être remise au transporteur',
              'shipped': 'Expédiée'}
    result = {'show': True, 'state': state, 'label': labels[state], 'is_demo': order.is_demo,
              'ready_at': record.ready_at if record else None,
              'shipped_at': record.shipped_at if record else None,
              'carrier_name': '', 'tracking_number': '', 'tracking_url': ''}
    if record and state == 'shipped':
        from .tracking import tracking_url
        result.update(carrier_name='Colissimo · La Poste' if record.carrier == 'colissimo' else record.carrier_name,
                      tracking_number=record.tracking_number, tracking_url=tracking_url(record))
    return result
