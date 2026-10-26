"""Paid-order seller notices are durable, private and never consume a second payment."""
from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.db import connection, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Product
from paiement.models import Order
from paiement.stock_checkout import settle
from stocks.models import Reservation, Variant
from .mail import deliver_pending
from .models import AlertNotice, AlertSettings
from .services import enqueue_paid_order, get_settings


@override_settings(EMAIL_DELIVERY_ENABLED=True, EMAIL_TEST_MODE=False,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', SITE_URL='https://shop.example.test',
    DEFAULT_FROM_EMAIL='boutique@example.test')
class PaidSellerNoticeTests(TestCase):
    def setUp(self):
        self.preferences = AlertSettings.objects.create(recipient='seller@example.test', order_notifications_enabled=True)

    def order(self, **changes):
        values = {'session_key': 'seller-notice-test', 'status': 'paid', 'paid_at': timezone.now(),
                  'customer_email': 'client@example.test', 'subtotal': 8000, 'total_paid': 8000,
                  'items': [{'name': 'Polaire verte', 'size': 'M', 'color': 'Vert', 'quantity': 1, 'unit_amount': 8000}],
                  'billing_details': {'name': 'Client confidentiel', 'address': {'line1': 'Adresse privée'}}}
        values.update(changes)
        return Order.objects.create(**values)

    def test_get_settings_is_read_only_and_defaults_do_not_enable_email(self):
        self.preferences.delete()
        config = get_settings()
        self.assertEqual(config.stock_threshold, 3)
        self.assertFalse(config.order_notifications_enabled)
        self.assertFalse(config.stock_notifications_enabled)
        self.assertFalse(AlertSettings.objects.exists())
        self.assertIsNone(enqueue_paid_order(self.order()))
        self.assertFalse(AlertNotice.objects.exists())

    def test_paid_transition_deduplicates_frozen_notice_and_keeps_customer_private(self):
        order = self.order()
        notice = enqueue_paid_order(order)
        self.assertEqual(enqueue_paid_order(order).pk, notice.pk)
        self.assertEqual(AlertNotice.objects.count(), 1)
        self.assertEqual(notice.payload['recipient'], 'seller@example.test')
        self.assertIn('80.00 €', notice.payload['details'])
        self.assertIn('Polaire verte', notice.payload['details'])
        self.assertNotIn('client@example.test', str(notice.payload))
        self.assertNotIn('Adresse privée', str(notice.payload))
        self.assertEqual(notice.payload['url'], f'/gestion/expeditions/{order.pk}/')
        self.assertEqual(len(mail.outbox), 0)

    def test_unpaid_demo_missing_date_and_missing_amount_never_queue(self):
        for fields in ({'status': 'pending'}, {'status': 'demo'}, {'status': 'expired'},
                       {'paid_at': None}, {'total_paid': None}):
            with self.subTest(fields=fields):
                self.assertIsNone(enqueue_paid_order(self.order(**fields)))
        self.assertFalse(AlertNotice.objects.exists())

    def test_recipient_and_payload_are_encrypted_at_rest(self):
        notice = enqueue_paid_order(self.order())
        with connection.cursor() as cursor:
            cursor.execute('SELECT recipient FROM alertes_alertsettings WHERE id=1')
            recipient = cursor.fetchone()[0]
            cursor.execute('SELECT payload FROM alertes_alertnotice WHERE id=%s', [notice.pk.hex if connection.vendor == 'sqlite' else notice.pk])
            payload = cursor.fetchone()[0]
        self.assertTrue(recipient.startswith('enc:v1:'))
        self.assertNotIn('seller@example.test', recipient + payload)
        self.assertNotIn('Polaire verte', payload)

    def test_delivery_tracks_provider_acceptance_and_does_not_repeat(self):
        notice = enqueue_paid_order(self.order())
        self.assertEqual(deliver_pending(notice_id=notice.pk), 1)
        notice.refresh_from_db()
        self.assertEqual(notice.attempts, 1)
        self.assertIsNotNone(notice.sent_at)
        self.assertEqual(notice.delivery_mode, 'provider')
        self.assertEqual(mail.outbox[0].to, ['seller@example.test'])
        self.assertIn('https://shop.example.test/gestion/expeditions/', mail.outbox[0].body)
        self.assertEqual(deliver_pending(notice_id=notice.pk, force=True), 0)
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(EMAIL_TEST_MODE=True)
    def test_local_capture_stays_local_when_provider_is_enabled_later(self):
        notice = enqueue_paid_order(self.order())
        self.assertEqual(deliver_pending(notice_id=notice.pk), 1)
        notice.refresh_from_db()
        self.assertEqual(notice.delivery_mode, 'local')
        self.assertTrue(mail.outbox[0].subject.startswith('[TEST]'))
        with override_settings(EMAIL_TEST_MODE=False):
            self.assertEqual(deliver_pending(notice_id=notice.pk, force=True), 0)
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(EMAIL_DELIVERY_ENABLED=False)
    def test_disabled_backend_keeps_notice_pending_without_attempt(self):
        notice = enqueue_paid_order(self.order())
        self.assertEqual(deliver_pending(notice_id=notice.pk), 0)
        notice.refresh_from_db()
        self.assertIsNone(notice.sent_at)
        self.assertEqual(notice.attempts, 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_delivery_failure_records_safe_reason_and_obeys_cooldown(self):
        notice = enqueue_paid_order(self.order())
        with patch('alertes.mail.EmailMultiAlternatives.send', side_effect=RuntimeError('private SMTP details')):
            self.assertEqual(deliver_pending(notice_id=notice.pk), 0)
        notice.refresh_from_db()
        self.assertEqual(notice.attempts, 1)
        self.assertEqual(notice.failure_code, 'delivery_failed')
        self.assertEqual(deliver_pending(notice_id=notice.pk), 0)
        self.assertEqual(deliver_pending(notice_id=notice.pk, force=True, expected_attempts=0), 0)
        self.assertEqual(deliver_pending(notice_id=notice.pk, force=True, expected_attempts=1), 1)
        self.assertEqual(deliver_pending(notice_id=notice.pk, force=True, expected_attempts=1), 0)
        notice.refresh_from_db()
        self.assertEqual(notice.attempts, 2)
        self.assertEqual(notice.failure_code, '')

    def test_worker_retries_only_after_cooldown_and_can_target_one_notice(self):
        first = enqueue_paid_order(self.order())
        second = enqueue_paid_order(self.order())
        AlertNotice.objects.filter(pk=first.pk).update(attempts=1, last_attempt_at=timezone.now()-timedelta(minutes=6))
        self.assertEqual(deliver_pending(notice_id=first.pk), 1)
        second.refresh_from_db()
        self.assertEqual(second.attempts, 0)
        self.assertEqual(len(mail.outbox), 1)

    def test_changed_recipient_disabled_alerts_and_invalid_order_cancel(self):
        for change in ('recipient', 'disabled', 'order'):
            notice = enqueue_paid_order(self.order())
            if change == 'recipient':
                AlertSettings.objects.filter(pk=1).update(recipient='new@example.test')
            elif change == 'disabled':
                AlertSettings.objects.filter(pk=1).update(order_notifications_enabled=False)
            else:
                Order.objects.filter(pk=notice.order_id).update(status='demo')
            self.assertEqual(deliver_pending(notice_id=notice.pk), 0)
            notice.refresh_from_db()
            self.assertIsNotNone(notice.cancelled_at)
            self.assertEqual(notice.attempts, 0)
            AlertSettings.objects.filter(pk=1).update(recipient='seller@example.test', order_notifications_enabled=True)
        self.assertEqual(len(mail.outbox), 0)

    def test_unrelated_settings_revision_does_not_lose_paid_notification(self):
        notice = enqueue_paid_order(self.order())
        AlertSettings.objects.filter(pk=1).update(stock_threshold=5, revision=2)
        self.assertEqual(deliver_pending(notice_id=notice.pk), 1)

    def test_payment_transaction_queues_once_and_sends_only_after_commit(self):
        product = Product.objects.create(name='Polaire', price=80)
        variant = Variant.objects.create(product=product, size='M', color='Vert', quantity=2, reserved=1)
        order = self.order(status='pending', paid_at=None, total_paid=None, stripe_session_id='cs_seller_test')
        Reservation.objects.create(order=order, variant=variant, quantity=1)
        session = {'id': 'cs_seller_test', 'metadata': {'order_id': str(order.pk)}, 'client_reference_id': str(order.pk),
                   'currency': 'eur', 'amount_subtotal': 8000, 'amount_total': 8000, 'payment_status': 'paid',
                   'status': 'complete', 'total_details': {'amount_shipping': 0, 'amount_discount': 0},
                   'customer_details': {'email': 'client@example.test'}}
        with patch('paiement.line_totals.capture_line_totals', return_value=[8000]), \
             patch('retours.mail.deliver_pending'), \
             self.captureOnCommitCallbacks(execute=True) as callbacks:
            self.assertTrue(settle(session))
            self.assertTrue(settle(session))
            self.assertEqual(AlertNotice.objects.count(), 1)
            self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(len(callbacks), 2)
        self.assertEqual(len(mail.outbox), 1)
        order.refresh_from_db(); variant.refresh_from_db()
        self.assertEqual(order.status, 'paid')
        self.assertEqual(variant.quantity, 1)

    def test_rollback_does_not_leave_pending_notification(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                enqueue_paid_order(self.order())
                raise RuntimeError('Rollback the paid transition')
        self.assertFalse(AlertNotice.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_worker_does_not_scan_historical_paid_orders(self):
        self.order()
        self.assertEqual(deliver_pending(), 0)
        self.assertFalse(AlertNotice.objects.exists())
