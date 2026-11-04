"""Cancellation notification retries stay scoped to one reviewed message."""
import uuid
from unittest.mock import patch

from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from paiement.models import Order
from retours.models import ReturnMail, ReturnRequest

from . import tests_views
from .models import CancellationStockMovement
from .notifications import notification_cards


@override_settings(
    DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=False,
    EMAIL_DELIVERY_ENABLED=True, EMAIL_TEST_MODE=False,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    SITE_URL='http://testserver',
)
class CancellationNotificationTests(TestCase):
    record = tests_views.CancellationViewTests.record

    def setUp(self):
        tests_views.CancellationViewTests.setUp(self)
        self.cancellation = self.record()
        self.smtp_patch = patch('smtplib.SMTP', side_effect=AssertionError('No real email in tests'))
        self.smtp_patch.start()
        self.addCleanup(self.smtp_patch.stop)
        self.ssl_patch = patch('smtplib.SMTP_SSL', side_effect=AssertionError('No real email in tests'))
        self.ssl_patch.start()
        self.addCleanup(self.ssl_patch.stop)

    def notice(self, kind='cancellation_requested', **kwargs):
        payload = {
            'kind': kind, 'details': 'INTERNAL_BODY_TOKEN_NEVER_DISPLAY',
            'url': 'https://example.test/private/PRIVATE_ACCESS_TOKEN/',
            'recipient': 'customer@example.test', 'recipient_source': 'order',
            'queued_at': timezone.now().isoformat(),
            'raw_smtp_error': 'SMTP_PASSWORD_NEVER_DISPLAY',
        }
        payload.update(kwargs.pop('payload', {}))
        values = {
            'key': f'{kind}-{self.cancellation.pk}', 'order': self.order,
            'request': self.cancellation.refund, 'payload': payload,
        }
        values.update(kwargs)
        return ReturnMail.objects.create(**values)

    def retry(self, notice, **kwargs):
        return self.client.post(self.url, {
            'action': 'retry_email', 'notice_id': notice.pk,
            'expected_attempts': notice.attempts, **kwargs,
        })

    def test_two_cards_are_present_before_messages_are_prepared(self):
        response = self.client.get(self.url)
        cards = response.context['notification_cards']
        self.assertEqual(len(cards), 2)
        self.assertEqual([card['kind'] for card in cards], ['cancellation_requested', 'cancellation_refund'])
        self.assertTrue(all(not card['exists'] for card in cards))
        self.assertContains(response, 'Ce message sera préparé après confirmation du remboursement.')
        self.assertNotContains(response, 'name="action" value="retry_email"')

    def test_get_only_shows_safe_metadata_and_never_sends(self):
        notice = self.notice(attempts=2, last_attempt_at=timezone.now())
        with patch('annulations.notifications.retry_mail') as retry, patch('retours.mail.deliver_pending') as worker:
            response = self.client.get(self.url)
        card = response.context['notification_cards'][0]
        self.assertEqual(card['recipient'], 'customer@example.test')
        self.assertEqual(card['status'], 'failed')
        self.assertEqual(card['attempts'], 2)
        self.assertEqual(card['date'], notice.last_attempt_at)
        self.assertContains(response, 'Échec de l’envoi')
        self.assertContains(response, 'Relancer ce message')
        self.assertContains(response, reverse('alertes:email_detail', kwargs={'source': 'return', 'pk': str(notice.pk)}))
        for secret in ('INTERNAL_BODY_TOKEN_NEVER_DISPLAY', 'PRIVATE_ACCESS_TOKEN', 'SMTP_PASSWORD_NEVER_DISPLAY'):
            self.assertNotContains(response, secret)
        retry.assert_not_called()
        worker.assert_not_called()

    def test_frozen_recipient_is_kept_when_order_email_changes(self):
        notice = self.notice()
        self.order.customer_email = 'new-address@example.test'
        self.order.save(update_fields=['customer_email'])
        response = self.client.get(self.url)
        card = response.context['notification_cards'][0]
        self.assertEqual(card['recipient'], 'customer@example.test')
        self.assertEqual(card['status'], 'blocked')
        self.assertFalse(card['can_retry'])
        self.assertNotContains(response, 'new-address@example.test')
        with patch('annulations.notifications.retry_mail') as retry:
            self.assertEqual(self.retry(notice).status_code, 409)
        retry.assert_not_called()

    def test_missing_frozen_recipient_never_uses_current_address_as_fact(self):
        self.notice(payload={'recipient': None, 'queued_at': 'invalid'})
        card = notification_cards(self.cancellation)[0]
        self.assertEqual(card['recipient'], 'Non conservé')
        self.assertIsNone(card['date'])
        self.assertFalse(card['can_retry'])
        self.assertEqual(card['status'], 'blocked')

    def test_sent_and_replaced_messages_cannot_be_retried(self):
        notice = self.notice()
        for values, expected in (
            ({'sent_at': timezone.now(), 'payload': {**notice.payload, 'delivery_mode': 'local'}}, 'sent_local'),
            ({'sent_at': timezone.now(), 'payload': {**notice.payload, 'delivery_mode': 'email'}}, 'sent_provider'),
            ({'sent_at': None, 'superseded_at': timezone.now()}, 'cancelled'),
        ):
            with self.subTest(status=expected):
                ReturnMail.objects.filter(pk=notice.pk).update(**values)
                card = notification_cards(self.cancellation)[0]
                self.assertEqual(card['status'], expected)
                self.assertFalse(card['can_retry'])
                with patch('annulations.notifications.retry_mail') as retry:
                    self.assertEqual(self.retry(notice).status_code, 409)
                retry.assert_not_called()

    def test_exact_retry_uses_shared_worker_once_and_leaves_other_messages(self):
        chosen = self.notice()
        other = self.notice('cancellation_refund')
        response = self.retry(chosen)
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        chosen.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(chosen.attempts, 1)
        self.assertIsNotNone(chosen.sent_at)
        self.assertEqual(other.attempts, 0)
        self.assertIsNone(other.sent_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(self.retry(chosen).status_code, 409)
        self.assertEqual(len(mail.outbox), 1)

    def test_failed_delivery_remains_safe_and_can_be_retried(self):
        chosen = self.notice()
        with patch('retours.mail.build_message', side_effect=RuntimeError('smtp-secret-DO-NOT-EXPOSE')):
            response = self.retry(chosen)
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        chosen.refresh_from_db()
        self.assertEqual(chosen.attempts, 1)
        self.assertIsNone(chosen.sent_at)
        response = self.client.get(self.url)
        self.assertContains(response, 'Échec de l’envoi')
        self.assertNotContains(response, 'smtp-secret-DO-NOT-EXPOSE')
        self.assertTrue(response.context['notification_cards'][0]['can_retry'])

    def test_stale_attempt_count_blocks_a_second_delivery(self):
        chosen = self.notice(attempts=2)
        response = self.retry(chosen, expected_attempts=1)
        self.assertContains(response, 'Ce message a été traité depuis votre ouverture.', status_code=409)
        chosen.refresh_from_db()
        self.assertEqual(chosen.attempts, 2)
        self.assertIsNone(chosen.sent_at)

    def test_worker_rechecks_attempt_count_after_preview(self):
        chosen = self.notice()
        from retours.mail import deliver_pending

        def competing_attempt(**kwargs):
            ReturnMail.objects.filter(pk=chosen.pk).update(attempts=1)
            return deliver_pending(**kwargs)

        with patch('retours.mail.deliver_pending', side_effect=competing_attempt):
            response = self.retry(chosen)
        self.assertEqual(response.status_code, 409)
        chosen.refresh_from_db()
        # The transaction refuses this stale retry, including our simulated competitor.
        self.assertIsNone(chosen.sent_at)
        self.assertEqual(len(getattr(mail, 'outbox', [])), 0)

    def test_other_order_or_request_or_key_is_rejected_before_worker(self):
        other_order = Order.objects.create(session_key='other-cancel-mail', status='paid', items=[], subtotal=100)
        other_refund = ReturnRequest.objects.create(order=self.order, submission_id=uuid.uuid4(), amount=100)
        candidates = (
            {'order': other_order},
            {'request': other_refund},
            {'key': f'order-{self.order.pk}', 'payload': {'kind': 'order'}},
        )
        for values in candidates:
            with self.subTest(values=values):
                chosen = self.notice(**values)
                self.assertFalse(any(card.get('pk') == chosen.pk for card in notification_cards(self.cancellation)))
                with patch('annulations.notifications.retry_mail') as retry:
                    self.assertEqual(self.retry(chosen).status_code, 404)
                retry.assert_not_called()
                chosen.delete()

    def test_wrong_kind_on_matching_key_is_blocked_before_worker(self):
        chosen = self.notice(payload={'kind': 'order'})
        card = notification_cards(self.cancellation)[0]
        self.assertEqual(card['status'], 'blocked')
        self.assertFalse(card['can_retry'])
        with patch('annulations.notifications.retry_mail') as retry:
            self.assertEqual(self.retry(chosen).status_code, 409)
        retry.assert_not_called()

    def test_retry_requires_permission_csrf_and_version(self):
        chosen = self.notice()
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.seller, backend=tests_views.LOGIN_BACKEND)
        self.assertEqual(strict.post(self.url, {'action': 'retry_email', 'notice_id': chosen.pk, 'expected_attempts': 0}).status_code, 403)
        self.client.force_login(self.customer, backend=tests_views.LOGIN_BACKEND)
        self.assertEqual(self.retry(chosen).status_code, 403)
        self.client.force_login(self.seller, backend=tests_views.LOGIN_BACKEND)
        for value in ('', '-1', 'abc', '2147483648'):
            with self.subTest(value=value):
                self.assertEqual(self.retry(chosen, expected_attempts=value).status_code, 409)
        chosen.refresh_from_db()
        self.assertEqual(chosen.attempts, 0)

    def test_out_of_range_notice_id_does_not_reach_database_or_worker(self):
        chosen = self.notice()
        with patch('annulations.notifications.retry_mail') as retry:
            for notice_id in ('0', '-1', '9223372036854775808'):
                with self.subTest(notice_id=notice_id):
                    self.assertEqual(self.retry(chosen, notice_id=notice_id).status_code, 404)
        retry.assert_not_called()

    @override_settings(EMAIL_DELIVERY_ENABLED=False)
    def test_disabled_mail_has_explanation_and_no_retry_button(self):
        chosen = self.notice()
        response = self.client.get(self.url)
        self.assertContains(response, 'L’envoi des e-mails est désactivé pour le moment.')
        self.assertNotContains(response, 'name="action" value="retry_email"')
        self.assertEqual(self.retry(chosen).status_code, 409)

    def test_demo_never_sends_to_live_backend(self):
        self.order.status = 'demo'
        self.order.save(update_fields=['status'])
        chosen = self.notice()
        response = self.client.get(self.url)
        self.assertContains(response, 'Les messages de simulation sont réservés à la boîte e-mail de test.')
        self.assertFalse(response.context['notification_cards'][0]['can_retry'])
        self.assertEqual(self.retry(chosen).status_code, 409)
        self.assertEqual(len(getattr(mail, 'outbox', [])), 0)

    def test_stock_history_displays_frozen_totals_reserved_date_and_source(self):
        self.cancellation.stock_completed_at = timezone.now()
        self.cancellation.save(update_fields=['stock_completed_at'])
        movement = CancellationStockMovement.objects.create(
            cancellation=self.cancellation, variant=self.variant,
            product_name='Polaire au traitement', size='M', color='Vert',
            quantity_before=7, quantity_added=2, quantity_after=9, reserved=3,
            source='manual', actor=self.seller,
        )
        self.variant.quantity = 20
        self.variant.save(update_fields=['quantity'])
        self.product.name = 'Nouveau nom du catalogue'
        self.product.save(update_fields=['name'])
        response = self.client.get(self.url)
        self.assertContains(response, 'Polaire au traitement')
        self.assertContains(response, 'Stock total avant')
        self.assertContains(response, 'Stock total après')
        self.assertContains(response, '<td data-label="Stock total avant">7</td>', html=True)
        self.assertContains(response, '<td data-label="Remis en stock"><strong>+2</strong></td>', html=True)
        self.assertContains(response, '<td data-label="Stock total après">9</td>', html=True)
        self.assertContains(response, '<td data-label="Dont réservés">3</td>', html=True)
        self.assertContains(response, 'Reprise manuelle par cancel-ui-seller')
        self.assertEqual(list(response.context['stock_movements']), [movement])

    def test_automatic_stock_history_does_not_attribute_work_to_request_author(self):
        CancellationStockMovement.objects.create(
            cancellation=self.cancellation, variant=self.variant,
            product_name='Polaire automatique', size='M', color='Vert',
            quantity_before=7, quantity_added=2, quantity_after=9, reserved=0,
            source='automatic', actor=None,
        )
        response = self.client.get(self.url)
        self.assertContains(response, 'Traitement automatique')
        self.assertNotContains(response, 'Reprise manuelle par cancel-ui-seller')

    def test_deleted_actor_does_not_turn_manual_stock_history_into_automatic(self):
        CancellationStockMovement.objects.create(
            cancellation=self.cancellation, variant=self.variant,
            product_name='Polaire manuelle', size='M', color='Vert',
            quantity_before=7, quantity_added=2, quantity_after=9, reserved=0,
            source='manual', actor=self.customer,
        )
        self.customer.delete()
        response = self.client.get(self.url)
        self.assertContains(response, 'Reprise manuelle · compte supprimé')
        self.assertNotContains(response, 'Traitement automatique')

    def test_old_and_demo_records_do_not_invent_stock_history(self):
        self.cancellation.stock_completed_at = timezone.now()
        self.cancellation.save(update_fields=['stock_completed_at'])
        response = self.client.get(self.url)
        self.assertContains(response, 'les quantités avant et après le traitement n’ont pas été conservées.')
        self.assertNotContains(response, 'class="cancellation-stock-table"')
        self.order.status = 'demo'
        self.order.save(update_fields=['status'])
        response = self.client.get(self.url)
        self.assertContains(response, 'Aucun mouvement de stock réel n’a été enregistré.')
        self.assertNotContains(response, 'class="cancellation-stock-table"')
