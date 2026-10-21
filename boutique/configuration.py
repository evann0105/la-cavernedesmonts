"""One current shop profile, with deployment values as a read-only fallback.

Issued invoices, order seller profiles and queued mail details are snapshots.
This module never edits those records and never changes the mail transport.
"""
from copy import deepcopy
import re
from urllib.parse import quote, urlencode

from django.conf import settings


DEFAULTS = {
    'phone': '04 50 40 46 17',
    'email': 'contact@la-cavernedesmonts.fr',
    'store_address_line1': '225 rue des Monts-Jura',
    'store_address_line2': 'Résidence Les Gentianes',
    'store_postal_code': '01410',
    'store_city': 'Lélex',
    'store_country': 'France',
    'business_name': 'Élodie FOLOPPE',
    'legal_form': 'EI',
    'siren': '525 114 914',
    'siret': '525 114 914 00019',
    'registration': '525 114 914 RCS Bourg-en-Bresse',
    'publisher': '',
    'business_address': 'Résidence Les Gentianes, 225 rue des Monts-Jura, 01410 Lélex, France',
    'business_same_as_store': False,
    'return_address': 'La Caverne des Monts\n225 Rue des Monts Jura\n01410 Lélex',
    'returns_same_as_store': False,
}
STORED_FIELDS = tuple(DEFAULTS)


def normalize_legal_form(value):
    value = str(value or '').strip()
    if re.fullmatch(
        r'(?:EI|entrepreneur(?:e)?\s+individuel(?:le)?|entreprise\s+individuelle)'
        r'(?:\s*\(\s*EI\s*\))?', value, flags=re.IGNORECASE,
    ):
        return 'EI'
    return value


def _fallback():
    data = deepcopy(DEFAULTS)
    legal = getattr(settings, 'SHOP_LEGAL', {})
    if isinstance(legal, dict):
        for key in ('phone', 'email', 'business_name', 'legal_form', 'siren',
                    'siret', 'registration', 'publisher'):
            if key in legal and isinstance(legal[key], str):
                data[key] = legal[key]
        if isinstance(legal.get('address'), str):
            data['business_address'] = legal['address']
    configured_return = getattr(settings, 'RETURN_ADDRESS', None)
    if isinstance(configured_return, str):
        data['return_address'] = configured_return
    data.update(revision=0, updated_at=None, updated_by=None)
    return data


def _phone_href(value):
    digits = re.sub(r'\D', '', str(value or ''))
    if not digits:
        return ''
    if str(value).strip().startswith('+'):
        number = '+' + digits
    elif digits.startswith('00'):
        number = '+' + digits[2:]
    elif len(digits) == 10 and digits.startswith('0'):
        number = '+33' + digits[1:]
    else:
        number = digits
    return 'tel:' + number


def configuration(instance=None):
    """Read only: GET and document rendering never create the singleton.

    The address keys used publicly are effective addresses. ``*_custom`` keys
    retain the independently saved addresses for the corresponding edit forms.
    """
    if instance is None:
        from .models import ShopConfiguration
        instance = ShopConfiguration.objects.select_related('updated_by').filter(pk=1).first()
    if instance is None:
        data = _fallback()
    else:
        data = {field: getattr(instance, field) for field in STORED_FIELDS}
        data.update(revision=instance.revision, updated_at=instance.updated_at,
                    updated_by=instance.updated_by)
    data['legal_form'] = normalize_legal_form(data['legal_form'])
    locality = ' '.join(part.strip() for part in
                        (data['store_postal_code'], data['store_city']) if part.strip())
    store_lines = [data['store_address_line1'], data['store_address_line2'],
                   locality, data['store_country']]
    data['store_address'] = '\n'.join(str(line).strip() for line in store_lines if str(line).strip())
    for prefix, same_field in (('business', 'business_same_as_store'),
                               ('return', 'returns_same_as_store')):
        name = prefix + '_address'
        data[name + '_custom'] = data[name]
        effective = data['store_address'] if data[same_field] else str(data[name] or '').strip()
        data[name] = data[name + '_effective'] = effective
    data['phone_href'] = _phone_href(data['phone'])
    data['email_href'] = 'mailto:' + quote(str(data['email']).strip(), safe='@.+-') if data['email'] else ''
    destination = 'La Caverne des Monts, ' + data['store_address'].replace('\n', ', ')
    data['maps_url'] = 'https://www.google.com/maps/search/?' + urlencode({'api': '1', 'query': destination})
    return data


def legal_overrides(data=None):
    """Only an explicitly saved database profile overrides the existing legal config."""
    data = configuration() if data is None else data
    if data.get('revision', 0) <= 0:
        return {}
    overrides = {key: data[key] for key in
                 ('business_name', 'legal_form', 'siren', 'siret', 'registration',
                  'publisher', 'phone', 'email')}
    overrides['address'] = data['business_address']
    overrides['return_address'] = data['return_address']
    return overrides


def stored_initial(data):
    """Seed first edit from the current fallback without losing deployment values."""
    values = {field: data[field] for field in STORED_FIELDS}
    values['business_address'] = data['business_address_custom']
    values['return_address'] = data['return_address_custom']
    return values
