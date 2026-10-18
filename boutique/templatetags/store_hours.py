from django import template

from boutique.services import public_hours


register = template.Library()


@register.inclusion_tag('boutique/opening_hours.html')
def store_opening_hours():
    return public_hours()
