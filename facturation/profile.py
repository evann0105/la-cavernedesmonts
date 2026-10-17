"""Explicit fiscal configuration; never infer a tax regime from a SIREN."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from django.conf import settings
from django.utils import timezone
from espace.legal import profile as legal_profile


SELLER_FIELDS = ('business_name', 'legal_form', 'address', 'siren', 'siret', 'registration', 'vat_notice', 'vat_number', 'email', 'phone')


def profile_blockers(value):
    """Validate either a checkout snapshot or the current candidate profile."""
    if not isinstance(value, dict):
        return ['Le profil fiscal enregistré au moment de la commande manque.']
    errors = []
    if value.get('ready') is not True:
        errors.append('La configuration de facturation doit être validée par la gérante et son comptable.')
    if value.get('numbering_confirmed') is not True:
        errors.append('La série annuelle WEB et son numéro de départ doivent être confirmés avec la comptabilité.')
    if value.get('issue_policy_confirmed') is not True:
        errors.append('La règle d’émission manuelle des factures doit être confirmée avec le comptable.')
    seller = value.get('seller')
    if not isinstance(seller, dict):
        seller = {}
    for key, label in (
        ('business_name', 'nom juridique'), ('legal_form', 'forme juridique'),
        ('address', 'adresse professionnelle'), ('siren', 'SIREN'),
        ('siret', 'SIRET'), ('registration', 'immatriculation'),
        ('vat_notice', 'mention fiscale confirmée'),
    ):
        if not isinstance(seller.get(key), str) or not seller[key].strip():
            errors.append('Identité du vendeur à compléter : ' + label + '.')
    mode = value.get('vat_mode')
    if mode not in ('standard', 'franchise'):
        errors.append('Le régime de TVA doit être confirmé : TVA applicable ou franchise en base.')
    elif mode == 'standard':
        if not isinstance(seller.get('vat_number'), str) or not seller['vat_number'].strip():
            errors.append('Le numéro de TVA actif doit être confirmé avant l’émission.')
        try:
            rate = Decimal(str(value.get('vat_rate', '')))
            if not rate.is_finite() or rate <= 0 or rate > 100 or rate.as_tuple().exponent < -2:
                raise InvalidOperation
        except (InvalidOperation, ValueError, TypeError):
            errors.append('Le taux unique de TVA applicable aux articles et à la livraison doit être explicitement confirmé.')
    elif '293' not in seller.get('vat_notice', ''):
        errors.append('La mention de franchise en base doit être confirmée par le comptable (article 293 B du CGI).')
    return errors


def _candidate():
    config = getattr(settings, 'SHOP_INVOICING', {})
    if not isinstance(config, dict):
        config = {}
    seller = legal_profile()
    return {
        'seller': {key: str(seller.get(key) or '').strip() for key in SELLER_FIELDS},
        'vat_mode': config.get('vat_mode', ''),
        'vat_rate': str(config.get('vat_rate') or ''),
        'numbering_confirmed': config.get('numbering_confirmed') is True,
        'issue_policy_confirmed': config.get('issue_policy_confirmed') is True,
        'ready': config.get('ready') is True,
    }


def blockers():
    return profile_blockers(_candidate())


def capture_profile():
    value = _candidate()
    if profile_blockers(value):
        return {}
    if value['vat_mode'] == 'franchise':
        value['vat_rate'] = ''
    else:
        value['vat_rate'] = format(Decimal(value['vat_rate']), '.2f')
    value['captured_at'] = timezone.now().isoformat()
    return deepcopy(value)
