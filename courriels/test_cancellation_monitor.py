"""Cancellation notifications remain private and lead to their own workflow."""
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from connexion.test_helpers import verify_test_user
from paiement.models import Order
from retours.mail import enqueue
from retours.models import ReturnMail, ReturnRequest

from .monitor import get_mail_row, retry_mail


@override_settings(DEBUG=True, EMAIL_TEST_MODE=False, LOCAL_SKIP_EMAIL_VERIFICATION=False,
                   EMAIL_DELIVERY_ENABLED=True,
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class CancellationMailMonitorTests(TestCase):
    def setUp(self):
        self.seller = get_user_model().objects.create_user(
            'cancellation-mail-seller', email='seller@example.test', is_staff=True)
        self.seller.user_permissions.add(Permission.objects.get(
            content_type__app_label='retours', codename='manage_returns'))
        verify_test_user(self.seller)
        self.order = Order.objects.create(status='paid', paid_at=timezone.now(),
            session_key='synthetic-cancellation-mail', subtotal=8000, total_paid=8000,
            items=[], customer_email='client@example.test')
        self.refund = ReturnRequest.objects.create(order=self.order, kind='cancellation',
            submission_id=uuid.uuid4(), amount=8000)

    def notice(self, kind, *, record=None):
        enqueue(self.order, 'synthetic-' + kind, kind,
                'PRIVATE_CANCELLATION_CONTENT https://example.test/?token=PRIVATE_TOKEN',
                request=record)
        return ReturnMail.objects.get(key='synthetic-' + kind)

    def test_cancellation_notices_have_clear_titles_and_order_based_destination(self):
        for kind, title in (
                ('cancellation_requested', 'Annulation de commande enregistrée'),
                ('cancellation_refund', 'Remboursement de la commande annulée')):
            with self.subTest(kind=kind):
                notice = self.notice(kind, record=self.refund)
                before = ReturnMail.objects.values().get(pk=notice.pk)
                row = get_mail_row(self.seller, 'return', notice.pk)
                self.assertEqual(row['title'], title)
                self.assertEqual(row['url'], reverse('annulations:detail', args=[self.order.pk]))
                self.assertEqual(row['action_label'], 'Ouvrir l’annulation')
                self.assertEqual(row['status'], 'pending')
                self.assertNotIn('PRIVATE_CANCELLATION_CONTENT', str(row))
                self.assertNotIn('PRIVATE_TOKEN', str(row))
                self.assertEqual(before, ReturnMail.objects.values().get(pk=notice.pk))
        self.assertEqual(len(mail.outbox), 0)

    def test_legacy_generic_refund_for_cancellation_uses_cancellation_destination(self):
        notice = self.notice('refund', record=self.refund)
        row = get_mail_row(self.seller, 'return', notice.pk)
        self.assertEqual(row['url'], reverse('annulations:detail', args=[self.order.pk]))
        self.assertEqual(row['action_label'], 'Ouvrir l’annulation')

    def test_physical_return_keeps_its_existing_destination(self):
        self.refund.kind = 'withdrawal'
        self.refund.save(update_fields=['kind'])
        notice = self.notice('refund', record=self.refund)
        row = get_mail_row(self.seller, 'return', notice.pk)
        self.assertEqual(row['url'], reverse('retours:manage_detail', args=[self.refund.pk]))
        self.assertEqual(row['action_label'], 'Ouvrir le retour')

    def test_retry_sends_only_selected_cancellation_notification_to_test_outbox(self):
        first = self.notice('cancellation_requested', record=self.refund)
        other = self.notice('cancellation_refund', record=self.refund)
        row = retry_mail(self.seller, 'return', first.pk, expected_attempts=0)
        self.assertEqual(row['status'], 'sent_provider')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['client@example.test'])
        other.refresh_from_db()
        self.assertIsNone(other.sent_at)
        self.assertEqual(other.attempts, 0)
