from django import template
from espace.legal import short_legal_form


register = template.Library()
register.filter('short_legal_form', short_legal_form)
