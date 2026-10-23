"""Format checks only; no carrier lookup or freely supplied tracking URL.

La Poste documents 13-character shipment numbers, 14-digit references and
15-character partner references. No check here certifies a real shipment.
https://aide.laposte.fr/contenu/comment-suivre-mon-colis-ou-ma-lettre
"""
import re
from urllib.parse import urlencode


class TrackingError(ValueError):
    pass


def normalize_tracking(value, carrier='colissimo'):
    if not isinstance(value, str) or len(value) > 200:
        raise TrackingError('Renseignez un numéro de suivi valide, sans lien internet.')
    if carrier == 'colissimo':
        normalized = re.sub(r'[\s-]', '', value).upper()
        if not re.fullmatch(r'[A-Z0-9]{13,15}', normalized):
            raise TrackingError('Le numéro Colissimo / La Poste doit contenir 13 à 15 lettres ou chiffres, sans URL.')
        return normalized
    if carrier != 'other':
        raise TrackingError('Choisissez Colissimo / La Poste ou un autre transporteur.')
    normalized = re.sub(r'\s', '', value).upper()
    if (not re.fullmatch(r'[A-Z0-9][A-Z0-9._/-]{2,119}', normalized)
            or '://' in value or normalized.startswith('WWW.')):
        raise TrackingError('Indiquez uniquement le numéro de suivi de l’autre transporteur, sans URL (3 à 120 caractères).')
    return normalized


def tracking_url(fulfillment):
    if fulfillment is None or fulfillment.carrier != 'colissimo':
        return ''
    try:
        number = normalize_tracking(fulfillment.tracking_number, fulfillment.carrier)
    except TrackingError:
        return ''
    return 'https://www.laposte.fr/outils/suivre-vos-envois?' + urlencode({'code': number})
