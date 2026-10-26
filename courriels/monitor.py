"""Private, read-only view of transactional outboxes with exact-message retries.

Never expose captured HTML, reset/verification tokens, order access tokens,
benefit codes or raw SMTP exceptions in this overview.
"""
from datetime import datetime, timezone as datetime_timezone
import hashlib
import json

from django.conf import settings
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from avoirs.models import BenefitMail
from avoirs.mail import blocked_reason as benefit_blocked_reason
from connexion.email_verification import email_access_allowed
from config.encrypted_fields import email_digest
from paiement.models import Order
from retours.access import manager
from retours.models import ReturnMail
from retours.mail import blocked_reason as return_blocked_reason


STATUS_LABELS = {
    'pending': 'En attente', 'failed': 'Échec de l’envoi',
    'blocked': 'À vérifier', 'sent_local': 'Dans la boîte de test',
    'sent_provider': 'Pris en charge par le service d’envoi',
    'unknown': 'Ancien traitement enregistré', 'cancelled': 'Annulé ou remplacé',
}
TITLES = {
    'order': 'Confirmation de commande', 'shipping_ready': 'Commande prête',
    'shipping': 'Commande expédiée', 'shipping_update': 'Suivi d’expédition corrigé',
    'refund': 'Confirmation du remboursement', 'return_shipment': 'Expédition d’un retour signalée',
    'return_access': 'Lien de retour demandé', 'return_requested': 'Demande de retour reçue',
    'return_completed': 'Dossier de retour terminé',
}
EPOCH = datetime.min.replace(tzinfo=datetime_timezone.utc)
RECIPIENT_CONFIRMATION_SALT = 'mail-monitor-legacy-recipient-v1'
LEGACY_CUSTOMER_KINDS = ('order', 'refund', 'return_access', 'return_requested', 'return_completed')


def _authorize(actor):
    if not actor or not manager(actor) or not email_access_allowed(actor):
        raise PermissionDenied


def _status(notice, *, cancelled, reason):
    if notice.sent_at:
        payload = notice.payload if isinstance(notice.payload, dict) else {}
        mode = payload.get('delivery_mode')
        if mode in ('local', 'local_test'):
            return 'sent_local', 'Message conservé dans la boîte e-mail de test, sans envoi au destinataire.'
        if mode in ('email', 'provider'):
            return 'sent_provider', 'Le service d’envoi a accepté le message. Sa réception dans la boîte du client n’est pas confirmée.'
        return 'unknown', 'Ce message a été traité auparavant, mais son mode d’envoi n’a pas été conservé.'
    if cancelled:
        return 'cancelled', 'Ce message a été remplacé ou annulé et ne sera pas relancé.'
    if reason:
        return 'blocked', reason
    if notice.attempts:
        return 'failed', 'La dernière tentative n’a pas abouti. Vous pouvez réessayer pour ce message uniquement.'
    return 'pending', 'Ce message attend son prochain traitement.'


def _may_confirm_recipient(notice):
    payload = notice.payload
    return bool(notice.sent_at is None and notice.superseded_at is None
        and isinstance(payload, dict) and not payload.get('fulfillment_superseded')
        and payload.get('recipient') is None and payload.get('kind') in LEGACY_CUSTOMER_KINDS
        and not return_blocked_reason(notice, require_frozen=False))


def _confirmation_snapshot(notice):
    return {'source': 'return', 'pk': notice.pk, 'order': str(notice.order_id),
            'recipient': email_digest(notice.order.customer_email), 'attempts': notice.attempts,
            'payload': hashlib.sha256(json.dumps(notice.payload, sort_keys=True, default=str).encode()).hexdigest()}


@transaction.atomic
def _confirm_legacy_recipient(actor, pk, token, expected_attempts):
    try:
        snapshot = signing.loads(token, salt=RECIPIENT_CONFIRMATION_SALT, max_age=20 * 60)
    except (signing.BadSignature, TypeError, ValueError) as error:
        raise ValidationError('La confirmation du destinataire a expiré ou n’est pas valide. Actualisez la page avant de réessayer.') from error
    identity = ReturnMail.objects.filter(pk=pk).values('order_id').first()
    if identity is None:
        raise Http404
    # The same order-first ordering as all transactional message deliveries.
    order = Order.objects.select_for_update().get(pk=identity['order_id'])
    notice = ReturnMail.objects.select_for_update().get(pk=pk)
    notice.order = order
    if (not _may_confirm_recipient(notice) or notice.attempts != expected_attempts
            or snapshot != _confirmation_snapshot(notice)):
        raise ValidationError('Le destinataire ou le message a changé. Actualisez la page et vérifiez de nouveau l’adresse avant de relancer.')
    notice.payload = {**notice.payload, 'recipient': order.customer_email,
                      'recipient_source': 'order', 'recipient_confirmed_at': timezone.now().isoformat(),
                      'recipient_confirmed_by': actor.pk}
    notice.save(update_fields=['payload'])
    # Keep the order lock until the delivery check completes. The confirmed
    # address cannot change between freezing it and the exact-message retry.
    from retours.mail import deliver_pending
    deliver_pending(limit=1, notice_id=pk, force=True, expected_attempts=expected_attempts)


def return_row(notice):
    payload = notice.payload if isinstance(notice.payload, dict) else {}
    kind = payload.get('kind') if isinstance(payload.get('kind'), str) else ''
    cancelled = bool(notice.superseded_at or payload.get('fulfillment_superseded'))
    reason = return_blocked_reason(notice, require_frozen=True) if not notice.sent_at and not cancelled else ''
    status, reason = _status(notice, cancelled=cancelled, reason=reason)
    requires_confirmation = _may_confirm_recipient(notice)
    if requires_confirmation:
        reason = 'Le destinataire de cet ancien message n’avait pas été conservé. Vérifiez puis confirmez l’adresse actuelle de la commande affichée ci-dessus avant de relancer.'
    try:
        prepared_at = parse_datetime(payload.get('queued_at', ''))
        if prepared_at and timezone.is_naive(prepared_at):
            prepared_at = None
    except (ValueError, TypeError):
        prepared_at = None
    if kind in ('shipping_ready', 'shipping', 'shipping_update'):
        url, action = reverse('expeditions:detail', args=[notice.order_id]), 'Ouvrir l’expédition'
    elif notice.request_id:
        url, action = reverse('retours:manage_detail', args=[notice.request_id]), 'Ouvrir le retour'
    else:
        url, action = reverse('facturation:purchase_order', args=[notice.order_id]), 'Ouvrir la commande'
    return {
        'source': 'return', 'pk': notice.pk,
        'title': TITLES.get(kind, 'Message de commande'),
        'recipient': payload.get('recipient') or notice.order.customer_email or 'Non renseigné',
        'reference': 'Commande ' + str(notice.order_id)[:8].upper(),
        'status': status, 'status_label': STATUS_LABELS[status], 'reason': reason,
        'attempts': notice.attempts, 'date': notice.last_attempt_at or notice.sent_at or prepared_at or notice.order.created_at,
        'sent_at': notice.sent_at, 'can_retry': status in ('pending', 'failed') or requires_confirmation,
        'requires_recipient_confirmation': requires_confirmation,
        'recipient_confirmation_token': signing.dumps(_confirmation_snapshot(notice), salt=RECIPIENT_CONFIRMATION_SALT) if requires_confirmation else '',
        'url': url, 'action_label': action, 'is_demo': notice.order.is_demo,
    }


def benefit_row(notice):
    reason = benefit_blocked_reason(notice) if not notice.sent_at and not notice.cancelled_at else ''
    if not reason and not settings.EMAIL_DELIVERY_ENABLED:
        reason = 'L’envoi des e-mails est désactivé pour le moment.'
    status, reason = _status(notice, cancelled=bool(notice.cancelled_at), reason=reason)
    payload = notice.payload if isinstance(notice.payload, dict) else {}
    return {
        'source': 'benefit', 'pk': notice.pk, 'title': 'Avoir ou bon personnel',
        'recipient': payload.get('recipient') or 'Non renseigné',
        'reference': f'Avoir n°{notice.benefit_id}', 'status': status,
        'status_label': STATUS_LABELS[status], 'reason': reason,
        'attempts': notice.attempts, 'date': notice.last_attempt_at or notice.sent_at or notice.created_at,
        'sent_at': notice.sent_at, 'can_retry': status in ('pending', 'failed'),
        'requires_recipient_confirmation': False, 'recipient_confirmation_token': '',
        'url': reverse('avoirs:detail', args=[notice.benefit_id]), 'action_label': 'Ouvrir l’avoir',
        'is_demo': False,
    }


def mail_rows(actor, state='all'):
    _authorize(actor)
    rows = [return_row(notice) for notice in ReturnMail.objects.select_related('order', 'order__fulfillment').iterator(chunk_size=250)]
    rows += [benefit_row(notice) for notice in BenefitMail.objects.select_related('benefit', 'benefit__user').prefetch_related('benefit__redemptions').iterator(chunk_size=250)]
    if state in STATUS_LABELS:
        rows = [row for row in rows if row['status'] == state]
    return sorted(rows, key=lambda row: (row['date'] or EPOCH, row['source'], row['pk']), reverse=True)


def get_mail_row(actor, source, pk):
    _authorize(actor)
    try:
        if source == 'return':
            return return_row(ReturnMail.objects.select_related('order', 'order__fulfillment').get(pk=pk))
        if source == 'benefit':
            return benefit_row(BenefitMail.objects.select_related('benefit', 'benefit__user').get(pk=pk))
    except (ReturnMail.DoesNotExist, BenefitMail.DoesNotExist, ValueError, TypeError):
        pass
    raise Http404


def retry_mail(actor, source, pk, expected_attempts=None, recipient_confirmation_token=''):
    _authorize(actor)
    row = get_mail_row(actor, source, pk)
    if not row['can_retry']:
        raise ValidationError(row['reason'])
    if expected_attempts is not None and (type(expected_attempts) is not int or expected_attempts != row['attempts']):
        raise ValidationError('Ce message a été traité depuis votre ouverture. Actualisez son état avant de le relancer.')
    # Snapshot even for internal callers omitting a version: concurrent attempts
    # re-check this value while holding the notification lock in its own worker.
    expected_attempts = row['attempts']
    if row.get('requires_recipient_confirmation'):
        if not recipient_confirmation_token:
            raise ValidationError('Confirmez l’adresse e-mail affichée avant de relancer cet ancien message.')
        _confirm_legacy_recipient(actor, pk, recipient_confirmation_token, expected_attempts)
        return get_mail_row(actor, source, pk)
    if source == 'return':
        from retours.mail import deliver_pending
    else:
        from avoirs.mail import deliver_pending
    deliver_pending(limit=1, notice_id=pk, force=True, expected_attempts=expected_attempts)
    return get_mail_row(actor, source, pk)
