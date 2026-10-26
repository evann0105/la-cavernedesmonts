from datetime import timedelta
from decimal import Decimal
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.test import TestCase, override_settings
from django.utils import timezone

from avoirs.mail import enqueue as enqueue_benefit, deliver_pending as deliver_benefits
from avoirs.models import CustomerBenefit, BenefitMail, BenefitRedemption
from connexion.test_helpers import verify_test_user
from paiement.models import Order
from retours.mail import enqueue, deliver_pending
from retours.models import ReturnMail
from .monitor import mail_rows, get_mail_row, retry_mail


@override_settings(DEBUG=True, EMAIL_TEST_MODE=False, LOCAL_SKIP_EMAIL_VERIFICATION=False,
                   EMAIL_DELIVERY_ENABLED=True, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class MailMonitorTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.seller = User.objects.create_user('mail-seller', email='seller@example.test', is_staff=True)
        self.seller.user_permissions.add(Permission.objects.get(codename='manage_returns'))
        verify_test_user(self.seller)
        self.customer = User.objects.create_user('mail-customer', email='client@example.test')
        self.order = Order.objects.create(user=self.customer, session_key='mail-monitor-test',
            status='paid', paid_at=timezone.now(), total_paid=8000, subtotal=8000, items=[],
            customer_email=self.customer.email)
        enqueue(self.order, 'monitor-order', 'order', 'PRIVATE_MAIL_BODY https://example.test/?token=PRIVATE_TOKEN')
        self.notice = ReturnMail.objects.get(key='monitor-order')
        self.benefit = CustomerBenefit.objects.create(user=self.customer, bound_user_id=self.customer.pk,
            recipient_email=self.customer.email, label='Geste commercial', value=Decimal('15'))
        self.benefit_notice = enqueue_benefit(self.benefit)

    def test_private_role_and_verified_access_required(self):
        staff = get_user_model().objects.create_user('mail-staff', is_staff=True)
        for actor in (None, self.customer, staff):
            with self.subTest(actor=actor), self.assertRaises(PermissionDenied):
                mail_rows(actor)
            with self.subTest(actor=actor), self.assertRaises(PermissionDenied):
                retry_mail(actor, 'return', self.notice.pk)
        self.seller.email_verification.delete()
        with self.assertRaises(PermissionDenied):
            get_mail_row(self.seller, 'return', self.notice.pk)
        self.assertEqual(len(mail.outbox), 0)

    def test_monitor_is_read_only_and_exposes_no_message_body_token_or_code(self):
        before = (list(ReturnMail.objects.values()), list(BenefitMail.objects.values()))
        rows = mail_rows(self.seller)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row['status'] == 'pending' for row in rows))
        for secret in ('PRIVATE_MAIL_BODY', 'PRIVATE_TOKEN', self.benefit.code):
            self.assertNotIn(secret, str(rows))
        self.assertEqual(before, (list(ReturnMail.objects.values()), list(BenefitMail.objects.values())))
        self.assertEqual(len(mail.outbox), 0)

    def test_retry_exact_order_notice_does_not_drain_any_other_message(self):
        enqueue(self.order, 'other-monitor-order', 'refund', 'Other pending notification.')
        other = ReturnMail.objects.get(key='other-monitor-order')
        ReturnMail.objects.filter(pk=self.notice.pk).update(attempts=2, last_attempt_at=timezone.now())
        self.assertEqual(mail_rows(self.seller, state='failed')[0]['pk'], self.notice.pk)
        result = retry_mail(self.seller, 'return', self.notice.pk, expected_attempts=2)
        self.assertEqual(result['status'], 'sent_provider')
        self.assertEqual(result['attempts'], 3)
        self.assertEqual(len(mail.outbox), 1)
        other.refresh_from_db(); self.benefit_notice.refresh_from_db()
        self.assertIsNone(other.sent_at)
        self.assertIsNone(self.benefit_notice.sent_at)
        with self.assertRaises(ValidationError):
            retry_mail(self.seller, 'return', self.notice.pk, expected_attempts=2)
        self.assertEqual(deliver_pending(notice_id=self.notice.pk, force=True), 0)
        self.assertEqual(len(mail.outbox), 1)

    def test_retry_exact_benefit_does_not_send_other_notices(self):
        other = enqueue_benefit(self.benefit, resend=True)
        result = retry_mail(self.seller, 'benefit', self.benefit_notice.pk, expected_attempts=0)
        self.assertEqual(result['status'], 'sent_provider')
        self.assertEqual(len(mail.outbox), 1)
        other.refresh_from_db(); self.notice.refresh_from_db()
        self.assertIsNone(other.sent_at)
        self.assertIsNone(self.notice.sent_at)
        with self.assertRaises(ValidationError):
            retry_mail(self.seller, 'benefit', self.benefit_notice.pk)

    def test_stale_attempt_count_and_force_without_target_are_rejected(self):
        for source, notice, worker in (('return', self.notice, deliver_pending), ('benefit', self.benefit_notice, deliver_benefits)):
            with self.subTest(source=source):
                with self.assertRaises(ValidationError):
                    retry_mail(self.seller, source, notice.pk, expected_attempts=9)
                with self.assertRaises(ValidationError):
                    worker(notice_id=notice.pk, force=True, expected_attempts=9)
                with self.assertRaises(ValueError):
                    worker(force=True)
                notice.refresh_from_db()
                self.assertEqual(notice.attempts, 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_failed_delivery_records_attempt_without_raw_exception(self):
        with patch('django.core.mail.EmailMultiAlternatives.send', side_effect=RuntimeError('SMTP_PASSWORD_SECRET')):
            row = retry_mail(self.seller, 'return', self.notice.pk, expected_attempts=0)
            other_row = retry_mail(self.seller, 'benefit', self.benefit_notice.pk, expected_attempts=0)
        for item in (row, other_row):
            self.assertEqual(item['status'], 'failed')
            self.assertEqual(item['attempts'], 1)
            self.assertNotIn('SMTP_PASSWORD_SECRET', str(item))
        self.assertEqual(len(mail.outbox), 0)
        with self.assertRaises(ValidationError):
            retry_mail(self.seller, 'return', self.notice.pk, expected_attempts=0)

    def test_changed_recipients_are_blocked_at_read_and_send(self):
        Order.objects.filter(pk=self.order.pk).update(customer_email='changed@example.test')
        self.benefit.recipient_email = 'changed@example.test'
        self.benefit.save()
        for source, notice, worker in (('return', self.notice, deliver_pending), ('benefit', self.benefit_notice, deliver_benefits)):
            self.assertEqual(get_mail_row(self.seller, source, notice.pk)['status'], 'blocked')
            with self.assertRaises(ValidationError):
                retry_mail(self.seller, source, notice.pk)
            self.assertEqual(worker(notice_id=notice.pk, force=True), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_cancelled_superseded_and_inactive_benefits_cannot_be_retried(self):
        ReturnMail.objects.filter(pk=self.notice.pk).update(superseded_at=timezone.now())
        BenefitMail.objects.filter(pk=self.benefit_notice.pk).update(cancelled_at=timezone.now())
        for source, notice in (('return', self.notice), ('benefit', self.benefit_notice)):
            self.assertEqual(get_mail_row(self.seller, source, notice.pk)['status'], 'cancelled')
            with self.assertRaises(ValidationError):
                retry_mail(self.seller, source, notice.pk)
        new = enqueue_benefit(self.benefit, resend=True)
        CustomerBenefit.objects.filter(pk=self.benefit.pk).update(active=False)
        self.assertEqual(get_mail_row(self.seller, 'benefit', new.pk)['status'], 'blocked')
        self.assertEqual(deliver_benefits(notice_id=new.pk, force=True), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_expired_or_used_benefits_are_never_retried(self):
        CustomerBenefit.objects.filter(pk=self.benefit.pk).update(ends_at=timezone.now()-timedelta(days=1))
        self.assertEqual(get_mail_row(self.seller, 'benefit', self.benefit_notice.pk)['status'], 'blocked')
        with self.assertRaises(ValidationError):
            retry_mail(self.seller, 'benefit', self.benefit_notice.pk)
        CustomerBenefit.objects.filter(pk=self.benefit.pk).update(ends_at=None)
        BenefitRedemption.objects.create(benefit=self.benefit, order=self.order, state='used', discount_amount=1500)
        with self.assertRaises(ValidationError):
            retry_mail(self.seller, 'benefit', self.benefit_notice.pk)
        self.assertEqual(deliver_benefits(notice_id=self.benefit_notice.pk, force=True), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_demo_is_local_only_and_local_capture_is_labelled(self):
        Order.objects.filter(pk=self.order.pk).update(status='demo')
        self.assertEqual(get_mail_row(self.seller, 'return', self.notice.pk)['status'], 'blocked')
        self.assertEqual(deliver_pending(notice_id=self.notice.pk, force=True), 0)
        with tempfile.TemporaryDirectory() as directory, override_settings(EMAIL_TEST_MODE=True,
                EMAIL_BACKEND='courriels.backend.LocalMailboxBackend', EMAIL_TEST_DIRECTORY=directory):
            result = retry_mail(self.seller, 'return', self.notice.pk)
            self.assertEqual(result['status'], 'sent_local')
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(get_mail_row(self.seller, 'return', self.notice.pk)['status'], 'sent_local')

    def test_email_delivery_disabled_does_not_retry(self):
        with override_settings(EMAIL_DELIVERY_ENABLED=False):
            for source, notice in (('return', self.notice), ('benefit', self.benefit_notice)):
                self.assertEqual(get_mail_row(self.seller, source, notice.pk)['status'], 'blocked')
                with self.assertRaises(ValidationError):
                    retry_mail(self.seller, source, notice.pk)
        self.assertEqual(len(mail.outbox), 0)

    def test_old_sent_mode_unknown_and_legacy_recipient_requires_review(self):
        ReturnMail.objects.filter(pk=self.notice.pk).update(sent_at=timezone.now())
        self.assertEqual(get_mail_row(self.seller, 'return', self.notice.pk)['status'], 'unknown')
        old = ReturnMail.objects.create(order=self.order, key='old-without-recipient',
                                       payload={'kind': 'order', 'details': 'Historical message.'})
        self.assertEqual(get_mail_row(self.seller, 'return', old.pk)['status'], 'blocked')
        with self.assertRaises(ValidationError):
            retry_mail(self.seller, 'return', old.pk)
        self.assertEqual(deliver_pending(notice_id=old.pk), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_legacy_explicit_confirmation_freezes_only_that_message_recipient(self):
        old = ReturnMail.objects.create(order=self.order, key='confirm-legacy-recipient', attempts=1,
            payload={'kind': 'order', 'recipient': None, 'details': 'Order confirmation.'})
        before_order = Order.objects.values().get(pk=self.order.pk)
        row = get_mail_row(self.seller, 'return', old.pk)
        self.assertEqual(row['status'], 'blocked')
        self.assertTrue(row['can_retry'])
        self.assertTrue(row['requires_recipient_confirmation'])
        self.assertTrue(row['recipient_confirmation_token'])
        result = retry_mail(self.seller, 'return', old.pk, expected_attempts=1,
                            recipient_confirmation_token=row['recipient_confirmation_token'])
        self.assertEqual(result['status'], 'sent_provider')
        self.assertFalse(result['requires_recipient_confirmation'])
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.customer.email])
        old.refresh_from_db()
        self.assertEqual(old.payload['recipient'], self.customer.email)
        self.assertEqual(old.payload['recipient_confirmed_by'], self.seller.pk)
        self.assertEqual(old.attempts, 2)
        self.assertEqual(before_order, Order.objects.values().get(pk=self.order.pk))
        self.notice.refresh_from_db()
        self.assertIsNone(self.notice.sent_at)

    def test_legacy_confirmation_rejects_missing_tampered_expired_or_changed_token(self):
        old = ReturnMail.objects.create(order=self.order, key='stale-legacy-recipient',
            payload={'kind': 'refund', 'details': 'Refund notification.'})
        row = get_mail_row(self.seller, 'return', old.pk)
        token = row['recipient_confirmation_token']
        for invalid in ('', token + 'x'):
            with self.assertRaises(ValidationError):
                retry_mail(self.seller, 'return', old.pk, expected_attempts=0, recipient_confirmation_token=invalid)
        with patch('django.core.signing.time.time', return_value=timezone.now().timestamp()+1300):
            with self.assertRaises(ValidationError):
                retry_mail(self.seller, 'return', old.pk, expected_attempts=0, recipient_confirmation_token=token)
        Order.objects.filter(pk=self.order.pk).update(customer_email='new-owner@example.test')
        with self.assertRaises(ValidationError):
            retry_mail(self.seller, 'return', old.pk, expected_attempts=0, recipient_confirmation_token=token)
        old.refresh_from_db()
        self.assertNotIn('recipient', old.payload)
        self.assertEqual(old.attempts, 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_legacy_confirmation_cannot_authorize_changed_content_or_other_notice(self):
        first = ReturnMail.objects.create(order=self.order, key='legacy-first', payload={'kind':'order','details':'First'})
        second = ReturnMail.objects.create(order=self.order, key='legacy-second', payload={'kind':'order','details':'Second'})
        token = get_mail_row(self.seller, 'return', first.pk)['recipient_confirmation_token']
        with self.assertRaises(ValidationError):
            retry_mail(self.seller, 'return', second.pk, recipient_confirmation_token=token)
        ReturnMail.objects.filter(pk=first.pk).update(payload={'kind':'order','details':'Changed'})
        with self.assertRaises(ValidationError):
            retry_mail(self.seller, 'return', first.pk, recipient_confirmation_token=token)
        self.assertEqual(len(mail.outbox), 0)

    def test_legacy_shipping_or_shop_notices_never_offer_customer_recipient_confirmation(self):
        for kind in ('shipping', 'shipping_ready', 'shipping_update', 'return_shipment'):
            notice = ReturnMail.objects.create(order=self.order, key=f'legacy-{kind}',payload={'kind':kind,'details':'Legacy content.'})
            row = get_mail_row(self.seller, 'return', notice.pk)
            self.assertFalse(row['can_retry'])
            self.assertFalse(row['requires_recipient_confirmation'])
            self.assertEqual(row['recipient_confirmation_token'], '')
        self.assertEqual(len(mail.outbox), 0)

    def test_unknown_messages_and_bad_payload_do_not_break_monitor_or_worker(self):
        for source, pk in (('wrong', 1), ('return', 999999), ('benefit', 'bad-id')):
            with self.assertRaises(Http404):
                get_mail_row(self.seller, source, pk)
        for payload in ([], {'kind': [], 'details': 'Malformed legacy content.'}):
            ReturnMail.objects.filter(pk=self.notice.pk).update(payload=payload, last_attempt_at=None)
            self.assertEqual(get_mail_row(self.seller, 'return', self.notice.pk)['status'], 'blocked')
            self.assertEqual(deliver_pending(notice_id=self.notice.pk), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_ordinary_worker_backoff_remains_intact(self):
        ReturnMail.objects.filter(pk=self.notice.pk).update(attempts=1, last_attempt_at=timezone.now())
        BenefitMail.objects.filter(pk=self.benefit_notice.pk).update(attempts=1, last_attempt_at=timezone.now())
        self.assertEqual(deliver_pending(notice_id=self.notice.pk), 0)
        self.assertEqual(deliver_benefits(notice_id=self.benefit_notice.pk), 0)
        self.assertEqual(retry_mail(self.seller, 'return', self.notice.pk, expected_attempts=1)['status'], 'sent_provider')
        self.assertEqual(len(mail.outbox), 1)
