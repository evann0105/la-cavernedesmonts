from django import template
from django.utils.translation import gettext
register = template.Library()

@register.filter
def shop_text(value):
    return gettext(str(value)) if value else ''
