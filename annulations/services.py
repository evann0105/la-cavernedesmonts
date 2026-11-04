"""Cancel a whole unshipped purchase, preserving payment and inventory history.

The Order lock serializes cancellation, shipping, return submissions and refunds.
The immutable ReturnRequest is the existing financial ledger and Stripe key.
Sold reservations stay sold: inventory is restored in a separate, once-only step.
"""
import hashlib
import json
import logging
import uuid
from collections import Counter
from decimal import Decimal

import stripe
from django.conf import settings
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from connexion.email_verification import email_access_allowed
from core.models import Product
from expeditions.models import Fulfillment, FulfillmentEvent
from paiement.models import Order
from retours.access import manager
from retours.models import ReturnRequest, ReturnLine, ReturnEvent, ReturnMail
from retours.services import ReturnError
from stocks.identity import matches_historical_label
from stocks.models import Variant
from .models import Cancellation, CancellationStockMovement
from .reasons import internal_note_text, public_reason

SALT = 'cancel-before-shipping-preview-v1'
logger = logging.getLogger(__name__)


class CancellationError(ValueError):
    pass


class InternalNoteConflict(CancellationError):
    pass


def _authorized(actor):
    if not actor or not manager(actor) or not email_access_allowed(actor):
        raise PermissionDenied


def _amounts(order):
    from paiement.line_totals import validate_totals
    from stocks.reservations import StockUnavailable
    if (not isinstance(order.items, list) or not order.items or any(
        not isinstance(item, dict) or type(item.get('quantity')) is not int or item['quantity'] <= 0
        or type(item.get('unit_amount')) is not int or item['unit_amount'] < 0
        or not isinstance(item.get('name'), str) or not item['name'].strip() for item in order.items)):
        raise CancellationError('Les articles et quantités de cette commande doivent être vérifiés avant son annulation.')
    gross = [item['quantity'] * item['unit_amount'] for item in order.items]
    if (type(order.total_paid) is not int or type(order.return_shipping_amount) is not int
            or sum(gross) != order.subtotal):
        raise CancellationError('Le montant réellement payé et les frais de livraison doivent être vérifiés avant le remboursement.')
    totals = order.paid_line_totals
    if totals is None and sum(gross) + order.return_shipping_amount == order.total_paid:
        totals = gross
    try:
        totals = validate_totals(order, totals, order.total_paid, order.return_shipping_amount)
    except (StockUnavailable, TypeError, ValueError) as error:
        raise CancellationError('Le détail des remises réellement payées doit être vérifié avant l’annulation. Aucun montant approximatif ne sera remboursé.') from error
    return totals, order.return_shipping_amount


def _inventory(order, *, lock=False):
    if order.is_demo:
        return {}, []
    quantities = Counter()
    for item in order.items:
        if type(item.get('variant')) is not int or type(item.get('product')) is not int:
            raise CancellationError('Cette ancienne commande ne possède pas de déclinaisons de stock identifiables. Vérifiez son inventaire avant l’annulation.')
        quantities[item['variant']] += item['quantity']
    if lock:
        list(Product.objects.select_for_update().filter(pk__in=[item['product'] for item in order.items]).order_by('pk'))
    query = Variant.objects.filter(pk__in=quantities).order_by('pk')
    variants = {variant.pk: variant for variant in (query.select_for_update() if lock else query)}
    holds = list(order.reservations.order_by('variant_id').values('variant_id', 'quantity', 'state'))
    if (len(holds) != len(quantities) or any(
            hold['state'] != 'sold' or quantities.get(hold['variant_id']) != hold['quantity'] for hold in holds)):
        raise CancellationError('La sortie de stock de cette commande doit être vérifiée. Aucun article ne peut être remis deux fois en stock.')
    for item in order.items:
        variant = variants.get(item['variant'])
        if variant is None or variant.quantity is None:
            raise CancellationError('Renseignez d’abord le stock des tailles et couleurs concernées avant de confirmer la remise en stock.')
        if not matches_historical_label(variant, item.get('size', ''), item.get('color', ''), item['product']):
            raise CancellationError('Une taille, une couleur ou son produit a changé depuis l’achat. Corrigez son identité dans le stock avant de continuer.')
    return variants, holds


def _facts(order, *, lock=False):
    if order.status not in ('paid', 'demo') or (order.is_demo and not settings.DEBUG):
        raise CancellationError('Seule une commande payée et non expédiée peut être annulée ici.')
    fulfillment_query = Fulfillment.objects.filter(order=order)
    fulfillment = (fulfillment_query.select_for_update() if lock else fulfillment_query).first()
    if fulfillment and (fulfillment.shipped_at or fulfillment.state not in ('preparing', 'ready')):
        raise CancellationError('Cette commande est déjà expédiée ou annulée. Consultez son suivi avant toute autre action.')
    if order.returns.exists():
        raise CancellationError('Un dossier de retour ou de remboursement existe déjà. Traitez ce dossier pour éviter un second remboursement.')
    if not order.is_demo and not order.stripe_session_id:
        raise CancellationError('Aucun paiement Stripe n’est lié à cette commande. Le paiement doit être vérifié avant son annulation.')
    amounts, shipping = _amounts(order)
    variants, holds = _inventory(order, lock=lock)
    identity = {'order': str(order.pk), 'items': order.items, 'amounts': amounts,
                'shipping': shipping, 'paid': order.total_paid, 'status': order.status,
                'payment': order.stripe_session_id, 'revision': fulfillment.revision if fulfillment else 0,
                'holds': holds, 'variants': [(v.pk, v.revision, v.quantity, v.reserved, v.size, v.color, v.product_id)
                                             for v in variants.values()]}
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return fulfillment, amounts, shipping, variants, fingerprint


def preview(order):
    """Read only, including for old purchases: no API calls or inferred discounts."""
    result = {'eligible': False, 'blocked_reason': '', 'token': '', 'amount': None,
              'shipping_amount': None, 'merchandise_amount': None, 'lines': []}
    try:
        fulfillment, amounts, shipping, variants, fingerprint = _facts(order)
    except CancellationError as error:
        result['blocked_reason'] = str(error)
        return result
    lines = []
    for item, amount in zip(order.items, amounts):
        variant = variants.get(item.get('variant'))
        lines.append({'name': item['name'], 'size': item.get('size', ''), 'color': item.get('color', ''),
                      'quantity': item['quantity'], 'amount': Decimal(amount) / 100,
                      'stock_label': f'{variant.size} · {variant.color}' if variant else 'Simulation : aucun stock réel modifié'})
    result.update(eligible=True, token=signing.dumps({'order': str(order.pk), 'fingerprint': fingerprint}, salt=SALT),
                  amount=Decimal(order.total_paid)/100, shipping_amount=Decimal(shipping)/100,
                  merchandise_amount=Decimal(sum(amounts))/100, lines=lines)
    return result


def _validate_token(token, order, fingerprint):
    try:
        payload = signing.loads(token, salt=SALT, max_age=1800)
    except (signing.BadSignature, TypeError, ValueError) as error:
        raise CancellationError('Le récapitulatif a expiré. Relisez les montants actualisés avant de confirmer à nouveau.') from error
    if payload != {'order': str(order.pk), 'fingerprint': fingerprint}:
        raise CancellationError('La commande ou son stock a changé. Relisez le récapitulatif actualisé avant de confirmer à nouveau.')


def request_cancellation(order_id, actor, reason, preview_token, confirmed, reason_code='', internal_note=''):
    _authorized(actor)
    if confirmed is not True:
        raise CancellationError('Confirmez que le colis n’a pas été remis au transporteur et vérifiez le remboursement.')
    try:
        reason = public_reason(reason, reason_code)
        internal_note = internal_note_text(internal_note)
    except ValueError as error:
        raise CancellationError(str(error)) from error
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        previous = Cancellation.objects.select_related('refund').filter(order=order).first()
        if previous:
            return previous
        fulfillment, amounts, shipping, variants, fingerprint = _facts(order, lock=True)
        _validate_token(preview_token, order, fingerprint)
        refund = ReturnRequest.objects.create(order=order, submission_id=uuid.uuid4(), kind='cancellation',
                    reason=reason.strip(), amount=order.total_paid, shipping_amount=shipping)
        for index, (item, amount) in enumerate(zip(order.items, amounts)):
            ReturnLine.objects.create(request=refund, item_index=index, variant_id=item.get('variant'),
                name=item['name'], size=item.get('size', ''), color=item.get('color', ''),
                quantity=item['quantity'], amount=amount, unit_amount=item['unit_amount'])
        cancellation = Cancellation.objects.create(order=order, refund=refund,
            reason=reason, reason_code=reason_code, requested_by=actor, internal_note=internal_note,
            internal_note_revision=1 if internal_note else 0,
            internal_note_updated_at=timezone.now() if internal_note else None,
            internal_note_updated_by=actor if internal_note else None)
        fulfillment = fulfillment or Fulfillment(order=order)
        previous_state = fulfillment.state
        fulfillment.state = 'cancelled'; fulfillment.revision += 1; fulfillment.updated_by = actor
        fulfillment.full_clean(); fulfillment.save()
        FulfillmentEvent.objects.create(fulfillment=fulfillment, actor=actor, action='cancel',
            from_state=previous_state, to_state='cancelled', label='Annulation avant expédition enregistrée ; remboursement à confirmer.', note=reason.strip())
        from expeditions.mail import supersede_pending
        supersede_pending(fulfillment)
        ReturnMail.objects.filter(key=f'order-{order.pk}', sent_at__isnull=True, superseded_at__isnull=True).update(superseded_at=timezone.now())
        ReturnEvent.objects.create(request=refund, actor=actor, label='Annulation de toute la commande avant expédition, livraison comprise. Aucun retour physique demandé.')
        from .mail import notice
        notice(cancellation, 'cancellation_requested')
    return _run_refund(cancellation.pk, actor)


def save_internal_note(order_id, actor, note, revision):
    """Private seller note; serialize with financial work without changing it."""
    _authorized(actor)
    try:
        note = internal_note_text(note)
    except ValueError as error:
        raise CancellationError(str(error)) from error
    if type(revision) is not int or not 0 <= revision <= 2147483647:
        raise CancellationError('Actualisez le dossier avant d’enregistrer la note interne.')
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        cancellation = Cancellation.objects.select_for_update().filter(order=order).first()
        if cancellation is None:
            raise CancellationError('Enregistrez d’abord l’annulation pour modifier sa note interne.')
        # An identical retry is safe even if its original revision is now old.
        if cancellation.internal_note == note:
            return cancellation
        if cancellation.internal_note_revision != revision:
            raise InternalNoteConflict('La note a été modifiée dans un autre onglet ou par une autre personne. Vérifiez la dernière version avant d’enregistrer votre saisie.')
        if revision == 2147483647:
            raise CancellationError('Cette note ne peut plus être modifiée. Contactez l’assistance.')
        cancellation.internal_note = note
        cancellation.internal_note_revision += 1
        cancellation.internal_note_updated_at = timezone.now()
        cancellation.internal_note_updated_by = actor
        cancellation.save(update_fields=['internal_note', 'internal_note_revision',
            'internal_note_updated_at', 'internal_note_updated_by'])
        return cancellation


def _run_refund(pk, actor):
    cancellation = Cancellation.objects.select_related('order', 'refund').get(pk=pk)
    try:
        from retours.refunds import initiate, reconcile
        if cancellation.refund.refund_state == 'none':
            initiate(cancellation.refund_id, actor)
        elif cancellation.refund.refund_state != 'succeeded':
            reconcile(cancellation.refund_id)
        cancellation.refresh_from_db()
        if cancellation.refund.refund_state == 'succeeded':
            finalize_refund(cancellation.refund_id)
        Cancellation.objects.filter(pk=pk).update(refund_error='')
    except ReturnError as error:
        Cancellation.objects.filter(pk=pk).update(refund_error=str(error))
    except stripe.StripeError:
        Cancellation.objects.filter(pk=pk).update(refund_error='La confirmation du prestataire est indisponible. Utilisez « Vérifier le remboursement » : aucune nouvelle demande indépendante ne sera créée.')
    return Cancellation.objects.select_related('order', 'refund').get(pk=pk)


def reconcile_cancellation(order_id, actor):
    _authorized(actor)
    cancellation = Cancellation.objects.get(order_id=order_id)
    return _run_refund(cancellation.pk, actor)


@transaction.atomic
def finalize_refund(refund_id, actor=None):
    """Provider success is never rolled back by an operational stock problem."""
    order_id = ReturnRequest.objects.values_list('order_id', flat=True).get(pk=refund_id)
    order = Order.objects.select_for_update().get(pk=order_id)
    refund = ReturnRequest.objects.select_for_update().get(pk=refund_id)
    cancellation = Cancellation.objects.select_for_update().filter(refund=refund).first()
    if not cancellation or refund.refund_state != 'succeeded' or cancellation.stock_completed_at:
        return cancellation
    try:
        with transaction.atomic():
            _amounts(order)
            fulfillment = Fulfillment.objects.filter(order=order).first()
            if not fulfillment or fulfillment.state != 'cancelled' or fulfillment.shipped_at:
                raise CancellationError('Le statut d’expédition doit être vérifié avant la remise en stock.')
            variants, holds = _inventory(order, lock=True)
            quantities = Counter()
            for item in order.items:
                quantities[item.get('variant')] += item['quantity']
            for pk, variant in variants.items():
                before = variant.quantity
                variant.quantity += quantities[pk]; variant.revision += 1
                variant.save(update_fields=['quantity', 'revision', 'updated_at'])
                CancellationStockMovement.objects.create(
                    cancellation=cancellation, variant=variant,
                    product_name=variant.product.name, size=variant.size, color=variant.color,
                    quantity_before=before, quantity_added=quantities[pk],
                    quantity_after=variant.quantity, reserved=variant.reserved,
                    source='manual' if actor is not None else 'automatic', actor=actor)
            now = timezone.now()
            cancellation.stock_completed_at = now; cancellation.stock_error = ''; cancellation.refund_error = ''
            cancellation.save(update_fields=['stock_completed_at', 'stock_error', 'refund_error'])
            refund.state = 'completed'; refund.completed_at = now
            refund.save(update_fields=['state', 'completed_at'])
            ReturnEvent.objects.create(request=refund, actor=actor,
                label=('SIMULATION : aucun stock réel modifié.' if order.is_demo else 'Articles non expédiés remis en stock une seule fois : ' + str(sum(quantities.values())) + ' unité(s).'))
            FulfillmentEvent.objects.create(fulfillment=fulfillment, actor=actor,
                action='cancel_completed', from_state='cancelled', to_state='cancelled',
                label='Annulation terminée : remboursement confirmé et stock traité.')
    except CancellationError as error:
        Cancellation.objects.filter(pk=cancellation.pk).update(stock_error=str(error), refund_error='')
    except Exception:
        # The savepoint has rolled back stock changes only. Keep the provider's
        # confirmed refund and make this independently retryable by the seller.
        logger.exception('Cancellation stock finalization failed for %s', cancellation.pk)
        Cancellation.objects.filter(pk=cancellation.pk).update(
            stock_error='Le remboursement est confirmé, mais le stock n’a pas pu être enregistré. Réessayez la remise en stock depuis ce dossier.', refund_error='')
    return Cancellation.objects.get(pk=cancellation.pk)


def retry_stock(order_id, actor):
    _authorized(actor)
    cancellation = Cancellation.objects.select_related('refund').get(order_id=order_id)
    if cancellation.refund.refund_state != 'succeeded':
        raise CancellationError('Attendez la confirmation du remboursement avant de remettre les articles en stock.')
    return finalize_refund(cancellation.refund_id, actor=actor)
