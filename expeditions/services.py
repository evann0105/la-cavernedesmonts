"""Manual fulfillment transitions; order locks always precede fulfillment locks."""
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from connexion.email_verification import email_access_allowed
from paiement.models import Order
from retours.access import manager
from .models import Fulfillment, FulfillmentEvent
from .tracking import TrackingError, normalize_tracking
from .mail import enqueue_notice, supersede_pending


class FulfillmentError(ValueError):
    pass


class RevisionConflict(FulfillmentError):
    pass


def _authorized(actor):
    if not actor or not actor.is_authenticated or not manager(actor) or not email_access_allowed(actor):
        raise PermissionDenied


def recipient_email(order):
    email = (order.customer_email or '').strip()
    try:
        validate_email(email)
    except ValidationError:
        raise FulfillmentError('L’adresse e-mail enregistrée avec cette commande manque ou doit être vérifiée. Aucun avis d’expédition ne peut être envoyé.')
    return email


def _order_reason(order):
    if order.status not in ('paid', 'demo'):
        return 'Seules les commandes payées peuvent être préparées et expédiées.'
    if order.is_demo and not settings.DEBUG:
        return 'Les commandes de démonstration ne peuvent pas être traitées hors développement.'
    return ''


def blocked_reason(order):
    """Read-only conservative check before preparation and physical shipment."""
    reason = _order_reason(order)
    if reason:
        return reason
    returns = list(order.returns.all())
    if any(record.refund_state == 'succeeded' for record in returns):
        return 'Un remboursement a déjà été confirmé sur cette commande. Vérifiez le dossier avant toute expédition.'
    if any(record.state != 'completed' for record in returns):
        return 'Un dossier de retour ou de remboursement est encore ouvert. Vérifiez-le avant de préparer ou d’expédier cette commande.'
    items = order.items
    if (not isinstance(items, list) or not items or any(
            not isinstance(item, dict) or not isinstance(item.get('name'), str)
            or not item['name'].strip() or type(item.get('quantity')) is not int
            or item['quantity'] <= 0 for item in items)):
        return 'Les articles et quantités de la commande doivent être vérifiés avant de préparer le colis.'
    shipping = order.shipping_details
    if not isinstance(shipping, dict):
        return 'L’adresse de livraison enregistrée avec la commande est incomplète.'
    address = shipping.get('address')
    if (not str(shipping.get('name') or '').strip() or not isinstance(address, dict)
            or any(not str(address.get(key) or '').strip() for key in ('line1', 'postal_code', 'city', 'country'))):
        return 'Le nom et l’adresse de livraison enregistrés avec la commande doivent être complétés ou vérifiés avant de préparer le colis.'
    return ''


def _tracking(carrier, carrier_name, tracking_number):
    if carrier not in ('colissimo', 'other'):
        raise FulfillmentError('Choisissez Colissimo / La Poste ou un autre transporteur.')
    if not isinstance(carrier_name, str) or len(carrier_name.strip()) > 150:
        raise FulfillmentError('Le nom du transporteur doit comporter au maximum 150 caractères.')
    name = carrier_name.strip() if carrier == 'other' else ''
    if carrier == 'other' and not name:
        raise FulfillmentError('Indiquez le nom de l’autre transporteur.')
    try:
        number = normalize_tracking(tracking_number, carrier)
    except TrackingError as error:
        raise FulfillmentError(str(error)) from error
    return carrier, name, number


@transaction.atomic
def change_state(order_id, actor, expected_revision, action, carrier='colissimo',
                 carrier_name='', tracking_number='', notify_customer=False, note=''):
    _authorized(actor)
    if type(expected_revision) is not int or expected_revision < 0:
        raise FulfillmentError('Rechargez la préparation pour vérifier sa version actuelle.')
    if action not in ('ready', 'reopen', 'ship', 'update_tracking', 'resend_notice'):
        raise FulfillmentError('Cette action de préparation n’est pas disponible.')
    if not isinstance(note, str) or len(note.strip()) > 2000:
        raise FulfillmentError('La note interne doit comporter au maximum 2 000 caractères.')
    note = note.strip()
    order = Order.objects.select_for_update().get(pk=order_id)
    reason = _order_reason(order)
    if reason:
        raise FulfillmentError(reason)
    fulfillment = Fulfillment.objects.select_for_update().filter(order=order).first()
    revision = fulfillment.revision if fulfillment else 0
    if revision != expected_revision:
        raise RevisionConflict('Cette préparation a été modifiée dans un autre onglet. Rechargez la page avant de continuer.')
    if fulfillment is None:
        if action != 'ready':
            raise FulfillmentError('Préparez et vérifiez d’abord les articles de cette commande.')
        fulfillment = Fulfillment(order=order, state='preparing')
    else:
        # Reuse the already locked original purchase for mail and validation.
        fulfillment.order = order
    previous = fulfillment.state
    now = timezone.now()
    if action == 'ready':
        if previous != 'preparing':
            raise FulfillmentError('Seule une commande en préparation peut être marquée prête.')
        reason = blocked_reason(order)
        if reason:
            raise FulfillmentError(reason)
        if notify_customer:
            recipient_email(order)
        fulfillment.state = 'ready'
        fulfillment.ready_at = now
        label = 'Commande vérifiée et marquée prête à expédier.'
    elif action == 'reopen':
        if previous != 'ready':
            raise FulfillmentError('Seule une commande prête peut être remise en préparation. Une expédition enregistrée ne peut pas être annulée ici.')
        fulfillment.state = 'preparing'
        fulfillment.ready_at = None
        supersede_pending(fulfillment)
        label = 'Commande remise en préparation.'
    elif action == 'ship':
        if previous != 'ready':
            raise FulfillmentError('Marquez d’abord la commande prête avant de confirmer son expédition.')
        reason = blocked_reason(order)
        if reason:
            raise FulfillmentError(reason)
        if notify_customer is not True:
            raise FulfillmentError('L’avis au client est obligatoire lors de l’enregistrement de l’expédition.')
        recipient_email(order)
        fulfillment.carrier, fulfillment.carrier_name, fulfillment.tracking_number = _tracking(carrier, carrier_name, tracking_number)
        fulfillment.state = 'shipped'
        fulfillment.shipped_at = now
        supersede_pending(fulfillment)
        label = 'Expédition enregistrée par la boutique ; avis au client mis en attente d’envoi.'
    elif action == 'update_tracking':
        if previous != 'shipped':
            raise FulfillmentError('Le suivi se corrige uniquement après l’enregistrement de l’expédition.')
        if not note:
            raise FulfillmentError('Indiquez le motif interne de la correction du suivi.')
        if notify_customer is not True:
            raise FulfillmentError('L’avis de correction au client est obligatoire.')
        recipient_email(order)
        changed = _tracking(carrier, carrier_name, tracking_number)
        if changed == (fulfillment.carrier, fulfillment.carrier_name, fulfillment.tracking_number):
            raise FulfillmentError('Le suivi est inchangé. Utilisez le renvoi de l’avis si nécessaire.')
        previous_tracking = f'{fulfillment.carrier_label} · {fulfillment.tracking_number}'
        fulfillment.carrier, fulfillment.carrier_name, fulfillment.tracking_number = changed
        note = (f'Ancien suivi : {previous_tracking}\n'
                f'Nouveau suivi : {fulfillment.carrier_label} · {fulfillment.tracking_number}\n'
                f'Motif interne : {note}')
        supersede_pending(fulfillment)
        label = 'Suivi corrigé ; nouvel avis au client mis en attente d’envoi.'
    else:
        if previous != 'shipped':
            raise FulfillmentError('L’avis d’expédition peut être renvoyé uniquement après l’enregistrement de l’expédition.')
        recipient_email(order)
        notice, created = enqueue_notice(fulfillment, 'shipping', resend=True)
        if created:
            FulfillmentEvent.objects.create(fulfillment=fulfillment, action=action,
                from_state=previous, to_state=previous, actor=actor,
                label='Renvoi de l’avis d’expédition demandé ; message mis en attente d’envoi.', note=note)
        return fulfillment
    fulfillment.revision = revision + 1
    fulfillment.updated_by = actor
    fulfillment.full_clean()
    fulfillment.save()
    FulfillmentEvent.objects.create(fulfillment=fulfillment, action=action,
        from_state=previous, to_state=fulfillment.state, actor=actor, label=label, note=note)
    if action == 'ready' and notify_customer:
        enqueue_notice(fulfillment, 'shipping_ready')
    elif action == 'ship':
        enqueue_notice(fulfillment, 'shipping')
    elif action == 'update_tracking':
        enqueue_notice(fulfillment, 'shipping_update')
    return fulfillment
