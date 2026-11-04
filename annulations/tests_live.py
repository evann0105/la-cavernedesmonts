"""Polling reads saved outcomes; it never performs financial or stock work."""
import re
import uuid
from contextlib import ExitStack
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from connexion.models import EmailVerification
from paiement.models import Order
from retours.models import ReturnMail, ReturnRequest

from . import tests_views
from .live import tracking_state
from .models import Cancellation, CancellationStockMovement
from .notifications import notification_cards


@override_settings(
    DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=False,
    EMAIL_DELIVERY_ENABLED=True, EMAIL_TEST_MODE=False,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    SITE_URL='http://testserver',
)
class CancellationLiveTests(TestCase):
    record = tests_views.CancellationViewTests.record

    def setUp(self):
        tests_views.CancellationViewTests.setUp(self)
        self.cancellation = self.record()
        self.status_url = reverse('annulations:status', args=[self.order.pk])

    def notice(self, kind='cancellation_requested', **kwargs):
        values = {
            'order': self.order, 'request': self.cancellation.refund,
            'key': f'{kind}-{self.cancellation.pk}',
            'payload': {'kind': kind, 'recipient': 'customer@example.test',
                        'recipient_source': 'order', 'queued_at': timezone.now().isoformat(),
                        'details': 'Annulation de la commande.', 'url': 'https://example.test/order/'},
        }
        values.update(kwargs)
        return ReturnMail.objects.create(**values)

    def snapshot(self, version=None):
        response = self.client.get(self.status_url, {'version': version} if version else {})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_route_private_headers_and_get_only(self):
        self.assertEqual(self.status_url, f'/gestion/expeditions/{self.order.pk}/annuler/suivi/')
        response = self.client.get(self.status_url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('private', response['Cache-Control'])
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('Cookie', response['Vary'])
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')
        for method in ('post', 'put', 'delete'):
            with self.subTest(method=method):
                self.assertEqual(getattr(self.client, method)(self.status_url).status_code, 405)

    def test_saved_progress_and_safe_action_forms(self):
        data = self.snapshot()
        self.assertTrue(data['active'])
        self.assertTrue(data['changed'])
        self.assertEqual(data['total_label'], 'Total à rembourser')
        self.assertEqual(set(data['fragments']), {'progress', 'stock', 'notifications'})
        progress = data['fragments']['progress']['html']
        self.assertIn('Remboursement en cours', progress)
        self.assertIn('value="reconcile"', progress)
        self.assertIn('name="csrfmiddlewaretoken"', progress)
        self.assertNotIn('value="confirm"', progress)
        self.assertNotIn('internal_note', progress)
        self.assertIn('Aucun mouvement de stock', data['fragments']['stock']['html'])
        self.assertIn('Pas encore préparé', data['fragments']['notifications']['html'])

    def test_hash_is_stable_despite_masked_csrf_and_unchanged_response_is_small(self):
        first = self.snapshot()
        second = self.snapshot()
        self.assertEqual(first['version'], second['version'])
        for region in first['fragments']:
            self.assertEqual(first['fragments'][region]['version'], second['fragments'][region]['version'])
        first_token = re.search('name="csrfmiddlewaretoken" value="([^"]+)"', first['fragments']['progress']['html']).group(1)
        second_token = re.search('name="csrfmiddlewaretoken" value="([^"]+)"', second['fragments']['progress']['html']).group(1)
        self.assertNotEqual(first_token, second_token)
        with patch('annulations.live.render_to_string') as render:
            unchanged = self.snapshot(first['version'])
        self.assertEqual(unchanged, {'version': first['version'], 'active': True,
                                   'changed': False, 'total_label': 'Total à rembourser'})
        render.assert_not_called()

    def test_refund_stock_and_notifications_each_advance_without_claiming_early_completion(self):
        pending = self.snapshot()
        ReturnRequest.objects.filter(pk=self.cancellation.refund_id).update(refund_state='succeeded')
        paid = self.snapshot(pending['version'])
        self.assertTrue(paid['active'])
        self.assertEqual(paid['total_label'], 'Total remboursé')
        self.assertNotEqual(pending['fragments']['progress']['version'], paid['fragments']['progress']['version'])
        self.assertEqual(pending['fragments']['stock']['version'], paid['fragments']['stock']['version'])
        self.assertIn('value="restock"', paid['fragments']['progress']['html'])
        self.assertNotIn('value="reconcile"', paid['fragments']['progress']['html'])
        CancellationStockMovement.objects.create(
            cancellation=self.cancellation, variant=self.variant, product_name='Polaire commandée',
            size='M', color='Vert', quantity_before=3, quantity_added=2, quantity_after=5,
            reserved=1, source='manual', actor=self.seller,
        )
        Cancellation.objects.filter(pk=self.cancellation.pk).update(stock_completed_at=timezone.now())
        stocked = self.snapshot(paid['version'])
        self.assertTrue(stocked['active'])
        self.assertIn('Articles remis en stock', stocked['fragments']['progress']['html'])
        self.assertIn('Polaire commandée', stocked['fragments']['stock']['html'])
        self.assertIn('Reprise manuelle', stocked['fragments']['stock']['html'])
        first = self.notice(sent_at=timezone.now(), payload={'kind': 'cancellation_requested',
            'recipient': 'customer@example.test', 'delivery_mode': 'local'})
        second = self.notice('cancellation_refund')
        notices = self.snapshot(stocked['version'])
        self.assertTrue(notices['active'])
        ReturnMail.objects.filter(pk=second.pk).update(sent_at=timezone.now(), payload={**second.payload, 'delivery_mode': 'email'})
        complete = self.snapshot(notices['version'])
        self.assertFalse(complete['active'])
        self.assertIn('Dans la boîte de test', complete['fragments']['notifications']['html'])
        self.assertIn('Pris en charge par le service d’envoi', complete['fragments']['notifications']['html'])
        self.assertEqual(complete['fragments']['stock']['version'], stocked['fragments']['stock']['version'])
        first.refresh_from_db()
        self.assertEqual(first.attempts, 0)

    def test_demo_success_label_and_visibility(self):
        Order.objects.filter(pk=self.order.pk).update(status='demo')
        ReturnRequest.objects.filter(pk=self.cancellation.refund_id).update(refund_state='succeeded')
        Cancellation.objects.filter(pk=self.cancellation.pk).update(stock_completed_at=timezone.now())
        data = self.snapshot()
        self.assertEqual(data['total_label'], 'Total simulé')
        self.assertIn('Stock simulé', data['fragments']['progress']['html'])
        self.assertIn('Aucun mouvement de stock réel', data['fragments']['stock']['html'])
        with override_settings(DEBUG=False):
            self.assertEqual(self.client.get(self.status_url).status_code, 404)

    def test_get_has_no_database_writes_stripe_calls_workers_or_configuration_initialization(self):
        paths = (
            'annulations.services.request_cancellation', 'annulations.services.reconcile_cancellation',
            'annulations.services.retry_stock', 'annulations.notifications.retry_mail',
            'retours.mail.deliver_pending', 'stripe.Refund.create', 'stripe.Refund.retrieve',
            'boutique.configuration.configuration',
        )
        with ExitStack() as stack:
            mocks = [stack.enter_context(patch(path)) for path in paths]
            with CaptureQueriesContext(connection) as queries:
                self.snapshot()
        for mock in mocks:
            mock.assert_not_called()
        self.assertFalse([q['sql'] for q in queries if re.match(r'^\s*(INSERT|UPDATE|DELETE|ALTER|CREATE)\b', q['sql'], re.I)])
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.quantity, 3)
        self.cancellation.refund.refresh_from_db()
        self.assertEqual(self.cancellation.refund.refund_state, 'pending')
        self.assertFalse(CancellationStockMovement.objects.exists())
        self.assertFalse(ReturnMail.objects.exists())
        self.preview_mock.assert_not_called()

    def test_permissions_rechecked_on_every_poll(self):
        data = self.snapshot()
        self.seller.user_permissions.clear()
        self.assertEqual(self.client.get(self.status_url, {'version': data['version']}).status_code, 403)
        self.client.force_login(self.customer, backend=tests_views.LOGIN_BACKEND)
        self.assertEqual(self.client.get(self.status_url).status_code, 403)
        self.customer.is_staff = True
        self.customer.save(update_fields=['is_staff'])
        self.assertEqual(self.client.get(self.status_url).status_code, 403)
        self.client.logout()
        response = self.client.get(self.status_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('next=', response.url)

    def test_unverified_seller_cannot_poll(self):
        EmailVerification.objects.filter(user=self.seller).delete()
        response = self.client.get(self.status_url)
        self.assertRedirects(response, reverse('connexion:verify_email'), fetch_redirect_response=False)

    def test_unknown_cancelled_and_nonpaid_orders_do_not_expose_information(self):
        other = Order.objects.create(session_key='live-not-cancelled', status='paid', subtotal=100, items=[])
        self.assertEqual(self.client.get(reverse('annulations:status', args=[other.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('annulations:status', args=[uuid.uuid4()])).status_code, 404)
        Order.objects.filter(pk=self.order.pk).update(status='pending')
        self.assertEqual(self.client.get(self.status_url).status_code, 404)

    def test_private_notes_payment_secrets_and_mail_bodies_are_excluded_and_do_not_change_version(self):
        notice = self.notice(payload={
            'kind': 'cancellation_requested', 'recipient': 'customer@example.test',
            'recipient_source': 'order', 'details': 'PRIVATE_BODY_SENTINEL',
            'url': 'https://example.test/PRIVATE_LINK_SENTINEL',
            'smtp_password': 'PRIVATE_SMTP_SENTINEL',
        })
        first = self.snapshot()
        Cancellation.objects.filter(pk=self.cancellation.pk).update(
            internal_note='PRIVATE_NOTE_SENTINEL', internal_note_revision=6,
            internal_note_updated_at=timezone.now(), internal_note_updated_by=self.seller,
        )
        ReturnRequest.objects.filter(pk=self.cancellation.refund_id).update(payment_intent='PRIVATE_PAYMENT_SENTINEL')
        ReturnMail.objects.filter(pk=notice.pk).update(payload={**notice.payload, 'details': 'PRIVATE_BODY_CHANGED'})
        second = self.snapshot()
        self.assertEqual(first['version'], second['version'])
        text = str(second)
        for secret in ('PRIVATE_NOTE_SENTINEL', 'PRIVATE_BODY_SENTINEL', 'PRIVATE_BODY_CHANGED',
                       'PRIVATE_LINK_SENTINEL', 'PRIVATE_SMTP_SENTINEL', 'PRIVATE_PAYMENT_SENTINEL'):
            self.assertNotIn(secret, text)
        self.assertNotIn('internal_note', text)

    def test_notification_attempt_change_updates_only_its_region(self):
        notice = self.notice()
        first = self.snapshot()
        ReturnMail.objects.filter(pk=notice.pk).update(attempts=1, last_attempt_at=timezone.now())
        second = self.snapshot(first['version'])
        self.assertNotEqual(first['fragments']['notifications']['version'], second['fragments']['notifications']['version'])
        self.assertIn('Échec de l’envoi', second['fragments']['notifications']['html'])
        self.assertIn('name="expected_attempts" value="1"', second['fragments']['notifications']['html'])
        for region in ('progress', 'stock'):
            self.assertEqual(first['fragments'][region]['version'], second['fragments'][region]['version'])

    def test_error_details_are_escaped_and_new_errors_are_visible(self):
        first = self.snapshot()
        Cancellation.objects.filter(pk=self.cancellation.pk).update(stock_error='<script>alert(1)</script>')
        second = self.snapshot(first['version'])
        self.assertIn('&lt;script&gt;', second['fragments']['progress']['html'])
        self.assertNotIn('<script>', second['fragments']['progress']['html'])
        self.assertNotEqual(first['fragments']['progress']['version'], second['fragments']['progress']['version'])

    def test_snapshot_does_not_include_messages_with_wrong_order_or_request(self):
        other = Order.objects.create(session_key='live-other', status='paid', subtotal=100, items=[])
        self.notice(order=other, payload={'kind': 'cancellation_requested', 'recipient': 'wrong-order@example.test'})
        response = self.client.get(self.status_url)
        self.assertNotContains(response, 'wrong-order@example.test')
        self.assertIn('Pas encore préparé', response.json()['fragments']['notifications']['html'])

    def test_ssr_uses_the_same_versions_and_leaves_note_form_outside_live_regions(self):
        page = self.client.get(self.url)
        data = self.snapshot()
        self.assertEqual(page.context['live_state']['version'], data['version'])
        self.assertContains(page, 'data-status-url="' + self.status_url + '"')
        for key, region in data['fragments'].items():
            self.assertContains(page, 'data-live-region="' + key + '"')
            self.assertContains(page, region['version'])
        self.assertContains(page, 'annulations/live-followup.js')
        for fragment in data['fragments'].values():
            self.assertNotIn('value="save_note"', fragment['html'])
            self.assertNotIn('id="note-interne"', fragment['html'])

    def test_terminal_messages_stop_polling_but_failed_and_missing_messages_do_not(self):
        self.cancellation.refund.refund_state = 'succeeded'
        self.cancellation.stock_completed_at = timezone.now()
        cards = notification_cards(self.cancellation)
        for first, second, expected in (
            ('sent_local', 'sent_provider', False), ('unknown', 'cancelled', False),
            ('failed', 'sent_local', True), ('blocked', 'sent_local', True),
            ('pending', 'sent_provider', True), ('not_prepared', 'sent_local', True),
        ):
            with self.subTest(first=first, second=second):
                cards[0]['status'], cards[1]['status'] = first, second
                self.assertEqual(tracking_state(self.cancellation, cards, [])['active'], expected)
