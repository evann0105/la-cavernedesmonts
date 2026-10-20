"""Distinguish a local capture from provider acceptance across configuration changes."""
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.core import mail
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from connexion.test_helpers import verify_test_user
from .mail import deliver_pending, enqueue, notification_state
from .models import BenefitMail, CustomerBenefit
from .test_domain import BenefitFixtures


@override_settings(DEBUG=True, EMAIL_DELIVERY_ENABLED=True, EMAIL_TEST_MODE=True,
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class BenefitDeliveryModeTests(BenefitFixtures, TestCase):
    def delivered(self):
        benefit = self.benefit()
        notice = enqueue(benefit)
        self.assertEqual(deliver_pending(), 1)
        notice.refresh_from_db()
        benefit.refresh_from_db()
        return benefit, notice

    def test_local_capture_records_its_mode_inside_encrypted_payload(self):
        benefit, notice = self.delivered()
        self.assertEqual(notice.payload['delivery_mode'], 'local_test')
        self.assertEqual(notification_state(benefit)['mode'], 'local_test')
        with connection.cursor() as cursor:
            cursor.execute('SELECT payload FROM avoirs_benefitmail WHERE id=%s', [notice.pk])
            stored = cursor.fetchone()[0]
        self.assertTrue(stored.startswith('enc:v1:'))
        self.assertNotIn('delivery_mode', stored)
        self.assertNotIn(benefit.recipient_email, stored)

    @override_settings(EMAIL_TEST_MODE=False)
    def test_provider_acceptance_is_recorded_separately(self):
        benefit, notice = self.delivered()
        self.assertEqual(notice.payload['delivery_mode'], 'provider')
        self.assertEqual(notification_state(benefit)['mode'], 'provider')

    def test_changing_to_real_delivery_never_relabels_or_resends_a_capture(self):
        benefit, notice = self.delivered()
        processed_at = notice.sent_at
        with override_settings(EMAIL_TEST_MODE=False):
            self.assertEqual(notification_state(benefit)['mode'], 'local_test')
            self.assertEqual(deliver_pending(), 0)
        notice.refresh_from_db()
        self.assertEqual(notice.sent_at, processed_at)
        self.assertEqual(len(mail.outbox), 1)

    def test_legacy_processed_notice_is_unknown_and_not_requeued(self):
        benefit = self.benefit()
        notice = enqueue(benefit)
        when = timezone.now()
        BenefitMail.objects.filter(pk=notice.pk).update(sent_at=when)
        CustomerBenefit.objects.filter(pk=benefit.pk).update(notified_at=when)
        benefit.refresh_from_db()
        with override_settings(EMAIL_TEST_MODE=False):
            self.assertEqual(notification_state(benefit)['mode'], 'unknown')
            self.assertEqual(notification_state(benefit)['processed_at'], when)
            self.assertEqual(deliver_pending(), 0)

    def test_failed_send_has_no_successful_delivery_mode(self):
        benefit = self.benefit()
        notice = enqueue(benefit)
        with patch('avoirs.mail.EmailMultiAlternatives.send', side_effect=RuntimeError('test failure')):
            self.assertEqual(deliver_pending(), 0)
        notice.refresh_from_db()
        self.assertNotIn('delivery_mode', notice.payload)
        self.assertIsNone(notice.sent_at)
        state = notification_state(benefit)
        self.assertTrue(state['pending'])
        self.assertIsNone(state['processed_at'])

    def login_manager(self, catalog=False):
        verify_test_user(self.staff)
        self.staff.user_permissions.add(Permission.objects.get(content_type__app_label='retours', codename='manage_returns'))
        if catalog:
            self.staff.user_permissions.add(Permission.objects.get(content_type__app_label='core', codename='manage_catalog'))
        self.client.force_login(self.staff, backend='connexion.backends.EmailOrUsernameBackend')

    def test_admin_capture_banner_and_inbox_link_are_clear_and_permission_scoped(self):
        self.login_manager(catalog=True)
        response = self.client.get(reverse('avoirs:create'))
        self.assertContains(response, 'Mode e-mail de test')
        self.assertContains(response, 'Aucun e-mail n’est envoyé au client.')
        self.assertContains(response, 'Préparer un e-mail de test')
        self.assertContains(response, reverse('test_email_inbox'))
        self.staff.user_permissions.remove(Permission.objects.get(content_type__app_label='core', codename='manage_catalog'))
        self.assertNotContains(self.client.get(reverse('avoirs:create')), reverse('test_email_inbox'))

    def test_admin_status_preserves_capture_after_current_mode_changes(self):
        benefit, _ = self.delivered()
        self.login_manager()
        with override_settings(EMAIL_TEST_MODE=False):
            response = self.client.get(reverse('avoirs:detail', args=[benefit.pk]))
        self.assertContains(response, 'Cette capture n’a envoyé aucun e-mail au client.')
        self.assertNotContains(response, 'Dernier message pris en charge par le service d’envoi')

    @override_settings(EMAIL_TEST_MODE=False)
    def test_admin_provider_status_does_not_claim_inbox_receipt(self):
        benefit, _ = self.delivered()
        self.login_manager()
        response = self.client.get(reverse('avoirs:detail', args=[benefit.pk]))
        self.assertContains(response, 'pris en charge par le service d’envoi')
        self.assertContains(response, 'La réception dans la boîte du client n’est pas confirmée.')

    def test_admin_legacy_status_does_not_claim_real_delivery(self):
        benefit = self.benefit()
        notice = enqueue(benefit)
        when = timezone.now()
        BenefitMail.objects.filter(pk=notice.pk).update(sent_at=when)
        CustomerBenefit.objects.filter(pk=benefit.pk).update(notified_at=when)
        self.login_manager()
        with override_settings(EMAIL_TEST_MODE=False):
            response = self.client.get(reverse('avoirs:detail', args=[benefit.pk]))
        self.assertContains(response, 'son mode d’envoi n’a pas été conservé')
        self.assertContains(response, 'Cela ne confirme pas un envoi réel')

    def test_create_and_notify_feedback_do_not_promise_a_real_email_in_test_mode(self):
        self.login_manager()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('avoirs:create'), {
                'recipient_email': self.client_user.email, 'label': 'Votre avoir de test',
                'kind': 'credit', 'value': '10', 'minimum_subtotal': '0',
                'active': 'on', 'notify': 'on',
            }, follow=True)
        self.assertContains(response, 'Aucun e-mail ne sera envoyé au client.')
        benefit = CustomerBenefit.objects.get(label='Votre avoir de test')
        notice = benefit.notifications.get()
        self.assertEqual(notice.payload['delivery_mode'], 'local_test')
        with self.captureOnCommitCallbacks(execute=True):
            notified = self.client.post(reverse('avoirs:notify', args=[benefit.pk]), follow=True)
        self.assertContains(notified, 'Aucun e-mail ne sera envoyé au client.')

