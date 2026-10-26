"""Read-only unified presentation. Message bodies and access tokens stay private."""
from django.conf import settings
from django.urls import reverse
from django.core.exceptions import ValidationError

from courriels.monitor import mail_rows, retry_mail
from .access import can_configure, authorize_configuration
from .models import AlertNotice, AlertEvent
from .services import get_settings
from .mail import local_capture_enabled, notice_is_current, deliver_pending


def delivery_context():
    return {'delivery_enabled': settings.EMAIL_DELIVERY_ENABLED, 'local_delivery': local_capture_enabled()}


def seller_row(notice, preferences):
    current = not notice.cancelled_at and notice_is_current(notice, preferences)
    reason = ''
    if notice.cancelled_at:
        state, label = 'cancelled', 'Annulé'
        reason = 'Cette alerte a été remplacée ou n’est plus nécessaire.'
    elif notice.sent_at:
        state, label = ('sent_local', 'Conservé en boîte de test') if notice.delivery_mode == 'local' else ('sent_provider', 'Transmis au service d’envoi')
        if notice.delivery_mode not in ('local', 'provider'):
            state, label = 'unknown', 'Traité · mode non précisé'
    elif not current:
        state, label = 'blocked', 'À vérifier'
        reason = 'Le destinataire, les réglages ou la situation ont changé. Cette ancienne alerte ne sera pas envoyée.'
    elif not settings.EMAIL_DELIVERY_ENABLED:
        state, label = 'blocked', 'Envoi désactivé'
        reason = 'Le service d’envoi doit être activé pour traiter ce message.'
    elif notice.attempts:
        state, label = 'failed', 'En échec'
        reason = 'La dernière tentative n’a pas abouti. Vous pouvez la relancer.'
    else:
        state, label = 'pending', 'En attente'
    url, action, reference = '', '', ''
    if notice.order_id:
        url, action = reverse('expeditions:detail', args=[notice.order_id]), 'Ouvrir la commande'
        reference = 'Commande ' + str(notice.order_id)[:8].upper()
    elif notice.variant_id:
        url, action = reverse('stocks:product', args=[notice.variant.product_id]), 'Ouvrir le stock'
        reference = str(notice.variant)
    return {'source': 'alert', 'pk': str(notice.pk), 'title': notice.get_kind_display(),
            'recipient': notice.payload.get('recipient', ''), 'reference': reference,
            'status': state, 'status_label': label, 'reason': reason, 'attempts': notice.attempts,
            'date': notice.last_attempt_at or notice.sent_at or notice.created_at, 'sent_at': notice.sent_at, 'url': url, 'action_label': action,
            'can_retry': state in ('pending', 'failed') and current and settings.EMAIL_DELIVERY_ENABLED}


def all_rows(actor):
    rows = mail_rows(actor)
    if can_configure(actor):
        preferences = get_settings()
        rows.extend(seller_row(notice, preferences) for notice in AlertNotice.objects.select_related('variant__product').all())
    return sorted(rows, key=lambda row: (row['date'], str(row['pk'])), reverse=True)


def retry(actor, source, pk, expected_attempts, recipient_confirmation_token=None):
    if source == 'alert':
        authorize_configuration(actor)
        notice = AlertNotice.objects.select_related('variant__product').filter(pk=pk).first()
        if notice is None:
            raise ValidationError('Ce message n’est plus disponible.')
        before = seller_row(notice, get_settings())
        if not before['can_retry'] or notice.attempts != expected_attempts:
            raise ValidationError('L’état de ce message a changé. Consultez son suivi avant de le relancer.')
        deliver_pending(limit=1, notice_id=notice.pk, force=True, expected_attempts=expected_attempts)
        notice.refresh_from_db()
        row = seller_row(notice, get_settings())
    else:
        row = retry_mail(actor, source, pk, expected_attempts=expected_attempts,
                         recipient_confirmation_token=recipient_confirmation_token)
    AlertEvent.objects.create(actor=actor, kind='mail_retry', details={
        'source': source, 'message': str(pk), 'status': row['status'], 'attempts': row['attempts']})
    return row
