"""Exact cancellation messages, with the existing outbox retry protections."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.http import Http404
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from connexion.email_verification import email_access_allowed
from courriels.monitor import return_row, retry_mail
from paiement.models import Order
from retours.access import manager
from retours.models import ReturnMail


MESSAGE_KINDS = (
    ('cancellation_requested', 'Annulation enregistrée'),
    ('cancellation_refund', 'Remboursement confirmé'),
)


def _keys(cancellation):
    return {f'{kind}-{cancellation.pk}': (kind, title) for kind, title in MESSAGE_KINDS}


def _query(cancellation):
    return ReturnMail.objects.filter(
        key__in=_keys(cancellation), order_id=cancellation.order_id,
        request_id=cancellation.refund_id,
    ).select_related('order', 'order__fulfillment', 'request')


def _prepared_at(payload):
    try:
        date = parse_datetime(payload.get('queued_at', ''))
        return date if date and timezone.is_aware(date) else None
    except (TypeError, ValueError):
        return None


def _card(notice, kind, title):
    """Expose only status metadata, never message contents or secret links."""
    payload = notice.payload if isinstance(notice.payload, dict) else {}
    row = return_row(notice)
    recipient = payload.get('recipient')
    if not recipient:
        recipient = 'Non conservé'
    else:
        try:
            validate_email(recipient)
        except (TypeError, ValidationError):
            recipient = 'Adresse à vérifier'
    date = notice.sent_at or notice.last_attempt_at or _prepared_at(payload)
    date_label = 'Traité le' if notice.sent_at else ('Dernière tentative' if notice.last_attempt_at else 'Préparé le')
    matches_kind = payload.get('kind') == kind
    return {
        'pk': notice.pk, 'kind': kind, 'title': title, 'exists': True,
        'recipient': recipient, 'date': date, 'date_label': date_label,
        'attempts': row['attempts'],
        'status': row['status'] if matches_kind else 'blocked',
        'status_label': row['status_label'] if matches_kind else 'À vérifier',
        'reason': row['reason'] if matches_kind else 'L’identité de ce message doit être vérifiée avant toute relance.',
        'can_retry': bool(matches_kind and row['can_retry'] and not row['requires_recipient_confirmation']),
        'detail_url': reverse('alertes:email_detail', kwargs={'source': 'return', 'pk': str(notice.pk)}),
    }


def notification_cards(cancellation):
    notices = {notice.key: notice for notice in _query(cancellation)}
    cards = []
    for key, (kind, title) in _keys(cancellation).items():
        notice = notices.get(key)
        if notice:
            cards.append(_card(notice, kind, title))
            continue
        reason = 'Le message d’annulation n’a pas encore été préparé.'
        if kind == 'cancellation_refund':
            reason = ('Le remboursement est confirmé, mais son message n’a pas encore été préparé.'
                      if cancellation.refund.refund_state == 'succeeded'
                      else 'Ce message sera préparé après confirmation du remboursement.')
        cards.append({'kind': kind, 'title': title, 'exists': False,
                      'status': 'not_prepared', 'status_label': 'Pas encore préparé',
                      'reason': reason, 'can_retry': False})
    return cards


@transaction.atomic
def retry_notification(cancellation, actor, notice_id, expected_attempts):
    if not manager(actor) or not email_access_allowed(actor):
        raise PermissionDenied
    if type(notice_id) is not int or not 1 <= notice_id <= 9223372036854775807:
        raise Http404
    if type(expected_attempts) is not int or not 0 <= expected_attempts <= 2147483647:
        raise ValidationError('Actualisez le suivi du message avant de le relancer.')
    # Same lock order as the shared worker: the message cannot change dossiers
    # between the ownership check and the exact-message retry.
    Order.objects.select_for_update().get(pk=cancellation.order_id)
    notice = _query(cancellation).select_for_update(of=('self',)).filter(pk=notice_id).first()
    if notice is None:
        raise Http404
    kind, title = _keys(cancellation)[notice.key]
    card = _card(notice, kind, title)
    if not card['can_retry']:
        raise ValidationError(card['reason'])
    return retry_mail(actor, 'return', notice.pk, expected_attempts=expected_attempts)
