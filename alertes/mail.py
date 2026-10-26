"""Deliver durable seller notices through the shop's existing e-mail backend."""
import logging
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from courriels.messages import render_message
from paiement.models import Order
from .models import AlertNotice, AlertSettings


logger = logging.getLogger(__name__)


def local_capture_enabled():
    return bool(getattr(settings, 'EMAIL_TEST_MODE', False) or
                settings.EMAIL_BACKEND == 'courriels.backend.LocalMailboxBackend')


def _allowed_url(value):
    """Seller notices never carry customer access tokens or external actions."""
    value = str(value or '')
    if value.startswith('/gestion/') and not value.startswith('//'):
        return settings.SITE_URL.rstrip('/') + value
    origin, candidate = urlsplit(settings.SITE_URL), urlsplit(value)
    if ((candidate.scheme, candidate.netloc) == (origin.scheme, origin.netloc)
            and candidate.path.startswith('/gestion/') and not candidate.query and not candidate.fragment):
        return value
    return settings.SITE_URL.rstrip('/') + '/mon-compte/'


def notice_is_current(notice, preferences):
    payload = notice.payload
    recipient = preferences.recipient.strip().lower()
    if not recipient or payload.get('recipient') != recipient:
        return False
    if notice.kind == 'paid_order':
        if not preferences.order_notifications_enabled or notice.order_id is None:
            return False
        # Payment is re-read. Do not notify the seller for a demo/pending order.
        return Order.objects.filter(pk=notice.order_id, status='paid', paid_at__isnull=False,
                                    total_paid__isnull=False).exists()
    if notice.kind == 'low_stock':
        from .stock import stock_notice_is_current
        return preferences.stock_notifications_enabled and stock_notice_is_current(notice, settings=preferences)
    return False


def deliver_pending(limit=30, notice_id=None, force=False, expected_attempts=None):
    """Retries only unsent notices; a local capture is never sent again as SMTP.

    Forced processing is reserved for an explicit single-message seller retry,
    and still revalidates the recipient, payment/stock state and settings.
    """
    if not settings.EMAIL_DELIVERY_ENABLED or limit <= 0:
        return 0
    cutoff = timezone.now() - timedelta(minutes=5)
    queued = AlertNotice.objects.filter(sent_at__isnull=True, cancelled_at__isnull=True)
    if notice_id is not None:
        queued = queued.filter(pk=notice_id)
    if not (force and notice_id is not None):
        queued = queued.filter(Q(last_attempt_at__isnull=True) | Q(last_attempt_at__lt=cutoff))
    ids = list(queued.order_by('created_at', 'pk').values_list('pk', flat=True)[:limit])
    sent = 0
    for pk in ids:
        try:
            with transaction.atomic():
                # Configuration edits lock this same singleton, so a change of
                # destination cannot race with sending an old queued payload.
                preferences = AlertSettings.objects.select_for_update().filter(pk=1).first()
                notice = AlertNotice.objects.select_for_update().get(pk=pk)
                if notice.sent_at or notice.cancelled_at:
                    continue
                if expected_attempts is not None and notice.attempts != expected_attempts:
                    continue
                if preferences is None or not notice_is_current(notice, preferences):
                    notice.cancelled_at = timezone.now()
                    notice.failure_code = 'obsolete'
                    notice.save(update_fields=['cancelled_at', 'failure_code'])
                    continue
                # Recheck cooldown under lock when concurrent workers saw the
                # same unsent ID before the first worker attempted delivery.
                if not (force and notice_id is not None) and notice.last_attempt_at and notice.last_attempt_at >= cutoff:
                    continue
                notice.attempts += 1
                notice.last_attempt_at = timezone.now()
                notice.failure_code = ''
                notice.save(update_fields=['attempts', 'last_attempt_at', 'failure_code'])
                payload = notice.payload
                test = local_capture_enabled()
                context = {name: str(payload.get(name, '')) for name in ('subject', 'title', 'intro', 'details', 'action')}
                context.update(url=_allowed_url(payload.get('url')), security=False, test=test)
                try:
                    text, html = render_message(context)
                    message = EmailMultiAlternatives(('[TEST] ' if test else '') + context['subject'] + ' — La Caverne des Monts',
                        text, settings.DEFAULT_FROM_EMAIL, [payload['recipient']])
                    message.attach_alternative(html, 'text/html')
                    accepted = message.send() == 1
                except Exception:
                    # Provider responses can contain private addresses/secrets.
                    # Store and log only a stable, non-sensitive reason.
                    logger.warning('Seller notification delivery failed (notice %s).', pk)
                    accepted = False
                if not accepted:
                    notice.failure_code = 'delivery_failed'
                    notice.save(update_fields=['failure_code'])
                    continue
                notice.sent_at = timezone.now()
                notice.delivery_mode = 'local' if test else 'provider'
                notice.save(update_fields=['sent_at', 'delivery_mode'])
                sent += 1
        except AlertNotice.DoesNotExist:
            continue
    return sent
