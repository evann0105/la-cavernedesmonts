"""Audited parcel-only contact corrections, separate from the paid purchase."""
from copy import deepcopy
import unicodedata

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.validators import validate_email
from django.db import transaction

from boutique.shipping_countries import COUNTRIES
from paiement.models import Order
from .models import Fulfillment, FulfillmentEvent


ADDRESS_LIMITS = {'line1': 255, 'line2': 255, 'postal_code': 20,
                  'city': 120, 'state': 120, 'country': 2}


def _fulfillment(order, fulfillment=None):
    if fulfillment is not None:
        return fulfillment
    try:
        return order.fulfillment
    except ObjectDoesNotExist:
        return None


def _shipping_copy(value):
    """Tolerate incomplete legacy records without mutating their source."""
    if not isinstance(value, dict):
        return {}
    result = deepcopy(value)
    if not isinstance(result.get('address'), dict):
        result['address'] = {}
    if not isinstance(result.get('name', ''), str):
        result['name'] = ''
    return result


def effective_shipping(order, fulfillment=None):
    fulfillment = _fulfillment(order, fulfillment)
    override = fulfillment.delivery_address_override if fulfillment is not None else None
    if isinstance(override, dict) and override:
        return _shipping_copy(override)
    return _shipping_copy(order.shipping_details)


def effective_email(order, fulfillment=None):
    fulfillment = _fulfillment(order, fulfillment)
    override = fulfillment.notification_email_override if fulfillment is not None else ''
    value = override if isinstance(override, str) and override.strip() else order.customer_email
    return value.strip() if isinstance(value, str) else ''


def locked_country(order):
    """Country of the paid delivery; operational overrides cannot redefine it."""
    snapshot = order.shipping_snapshot if isinstance(order.shipping_snapshot, dict) else {}
    shipping = order.shipping_details if isinstance(order.shipping_details, dict) else {}
    address = shipping.get('address') if isinstance(shipping.get('address'), dict) else {}
    for country in (snapshot.get('country'), address.get('country')):
        if isinstance(country, str) and country.strip().upper() in COUNTRIES:
            return country.strip().upper()
    return ''


def _plain_text(value, limit, label, required=False):
    from .services import FulfillmentError
    if not isinstance(value, str):
        raise FulfillmentError(f'{label} doit être renseigné sous forme de texte.')
    value = value.strip()
    if any(unicodedata.category(character) == 'Cc' for character in value):
        raise FulfillmentError(f'{label} ne doit pas contenir de retour à la ligne ou de caractère de contrôle.')
    if len(value) > limit or (required and not value):
        raise FulfillmentError(f'{label} est obligatoire et doit comporter au maximum {limit} caractères.'
                               if required else f'{label} doit comporter au maximum {limit} caractères.')
    return value


def _address(values, order):
    from .services import FulfillmentError
    if not isinstance(values, dict) or not isinstance(values.get('address'), dict):
        raise FulfillmentError('Renseignez le nom et l’adresse complète du destinataire.')
    name = _plain_text(values.get('name', ''), 200, 'Le nom du destinataire', required=True)
    labels = {'line1': 'La rue', 'line2': 'Le complément d’adresse', 'postal_code': 'Le code postal',
              'city': 'La ville', 'state': 'La région', 'country': 'Le pays'}
    required = {'line1', 'postal_code', 'city', 'country'}
    address = {}
    for key, limit in ADDRESS_LIMITS.items():
        value = values['address'].get(key, '')
        if value is None and key not in required:
            value = ''
        address[key] = _plain_text(value, limit, labels[key], key in required)
    address['country'] = address['country'].upper()
    if address['country'] not in COUNTRIES:
        raise FulfillmentError('Choisissez un pays de livraison valide dans la liste proposée.')
    paid_country = locked_country(order)
    if paid_country and address['country'] != paid_country:
        raise FulfillmentError('Le pays de livraison ne peut pas changer : les frais de la commande ont déjà été fixés pour ce pays.')
    return {'name': name, 'address': address}


def _comparable_shipping(value):
    """Ignore absent empty optional fields when detecting an unchanged address."""
    value = _shipping_copy(value)
    address = value.get('address', {})
    result = {'name': value.get('name', '').strip()}
    result['address'] = {key: (address.get(key) or '').strip()
                         if isinstance(address.get(key) or '', str) else ''
                         for key in ADDRESS_LIMITS}
    result['address']['country'] = result['address']['country'].upper()
    return result


@transaction.atomic
def correct_contacts(order_id, actor, expected_revision, kind, values, reason, confirmed):
    # Local imports keep services/mail/effective-contact lookups independent.
    from .mail import supersede_pending
    from .services import FulfillmentError, RevisionConflict, _authorized, _order_reason
    _authorized(actor)
    if type(expected_revision) is not int or expected_revision < 0:
        raise FulfillmentError('Rechargez la préparation pour vérifier sa version actuelle.')
    if kind not in ('address', 'email'):
        raise FulfillmentError('Choisissez l’adresse de livraison ou l’adresse e-mail à corriger.')
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 1000:
        raise FulfillmentError('Indiquez un motif interne de correction, de 1 à 1 000 caractères.')
    if confirmed is not True:
        raise FulfillmentError('Confirmez que les coordonnées corrigées ont été vérifiées avec le client.')
    reason = reason.strip()
    order = Order.objects.select_for_update().get(pk=order_id)
    order_reason = _order_reason(order)
    if order_reason:
        raise FulfillmentError(order_reason)
    fulfillment = Fulfillment.objects.select_for_update().filter(order=order).first()
    revision = fulfillment.revision if fulfillment else 0
    if revision != expected_revision:
        raise RevisionConflict('Cette préparation a été modifiée dans un autre onglet. Rechargez la page avant de continuer.')
    if fulfillment is not None and fulfillment.state == 'shipped':
        raise FulfillmentError('Les coordonnées ne peuvent plus être corrigées après l’enregistrement de l’expédition.')
    if fulfillment is None:
        fulfillment = Fulfillment(order=order, state='preparing')
    else:
        fulfillment.order = order
    before = {'shipping': effective_shipping(order, fulfillment), 'email': effective_email(order, fulfillment)}
    if kind == 'address':
        corrected = _address(values, order)
        if corrected == _comparable_shipping(before['shipping']):
            raise FulfillmentError('L’adresse de livraison est inchangée.')
        fulfillment.delivery_address_override = corrected
        label = 'Adresse de livraison corrigée ; contrôles de préparation à refaire.'
    else:
        corrected = _plain_text(values, 254, 'L’adresse e-mail', required=True)
        try:
            validate_email(corrected)
        except ValidationError as error:
            raise FulfillmentError('Renseignez une adresse e-mail de notification valide.') from error
        if corrected.casefold() == before['email'].casefold():
            raise FulfillmentError('L’adresse e-mail de notification est inchangée.')
        fulfillment.notification_email_override = corrected
        label = 'E-mail de notification corrigé ; contrôles de préparation à refaire.'
    previous = fulfillment.state
    fulfillment.state = 'preparing'
    fulfillment.ready_at = None
    fulfillment.revision = revision + 1
    fulfillment.updated_by = actor
    fulfillment.full_clean()
    fulfillment.save()
    # Preserve old payloads as encrypted history, but never send to old contacts.
    supersede_pending(fulfillment)
    after = {'shipping': effective_shipping(order, fulfillment), 'email': effective_email(order, fulfillment)}
    FulfillmentEvent.objects.create(
        fulfillment=fulfillment, action=f'contact_{kind}', from_state=previous,
        to_state='preparing', label=label, actor=actor, note=reason,
        contact_snapshot={'before': deepcopy(before), 'after': deepcopy(after)},
    )
    return fulfillment
