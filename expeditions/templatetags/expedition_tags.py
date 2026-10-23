from django import template
from expeditions.public import public_status

register = template.Library()


@register.inclusion_tag('expeditions/customer_status.html')
def expedition_status(order):
    return {'shipment': public_status(order)}
