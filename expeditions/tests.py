"""Shipping operations preserve purchase history and use the transactional outbox."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.core.exceptions import PermissionDenied
from django.db import close_old_connections, connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from connexion.models import EmailVerification
from connexion.test_helpers import verify_test_user
from core.models import Product
from paiement.models import Order
from retours.access import order_token
from retours.mail import deliver_pending
from retours.models import ReturnMail, ReturnRequest
from stocks.models import Reservation, Variant

from .models import Fulfillment, FulfillmentEvent
from .selectors import summary
from .services import FulfillmentError, RevisionConflict, change_state


# Deliberately invented test identifiers, unrelated to any customer or parcel.
TRACKING = '8X00000000001'
CORRECTED_TRACKING = '8X00000000002'
LOGIN_BACKEND = 'connexion.backends.EmailOrUsernameBackend'
TEST_SETTINGS = {
    'DEBUG': True,
    'LOCAL_SKIP_EMAIL_VERIFICATION': False,
    'EMAIL_DELIVERY_ENABLED': False,
    'EMAIL_TEST_MODE': False,
    'EMAIL_BACKEND': 'django.core.mail.backends.locmem.EmailBackend',
    'SITE_URL': 'http://testserver',
}


class FulfillmentFixture:
    def setUp(self):
        super().setUp()
        users = get_user_model()
        self.seller = users.objects.create_user(
            'shipping-manager', email='seller@example.test', is_staff=True,
        )
        self.seller.user_permissions.add(Permission.objects.get(
            content_type__app_label='retours', codename='manage_returns',
        ))
        self.customer = users.objects.create_user(
            'shipping-customer', email='account@example.test',
        )
        self.other = users.objects.create_user(
            'shipping-other', email='other@example.test',
        )
        for user in (self.seller, self.customer, self.other):
            verify_test_user(user)
        self.product = Product.objects.create(
            name='Polaire du catalogue', slug='shipping-fixture-polaire',
            price=Decimal('36.00'),
        )
        self.variant = Variant.objects.create(
            product=self.product, size='M', color='Bleu', quantity=5,
        )
        self.order = Order.objects.create(
            user=self.customer, session_key='shipping-fixture', status='paid',
            paid_at=timezone.now(), customer_email='purchase@example.test',
            subtotal=7200, total_paid=7800, paid_line_totals=[7200],
            return_shipping_amount=600,
            items=[{
                'product': self.product.pk, 'variant': self.variant.pk,
                'name': 'Polaire au moment de l’achat', 'size': 'M', 'color': 'Bleu',
                'quantity': 2, 'unit_amount': 3600,
            }],
            shipping_details={
                'name': 'Destinataire fictif',
                'address': {'line1': '123 rue Exemple', 'postal_code': '75000',
                            'city': 'Ville Test', 'country': 'FR'},
            },
            terms_snapshot={'text': 'Conditions acceptées lors de l’achat.'},
            invoice_profile={'seller_name': 'Entreprise au moment de l’achat'},
        )
        Reservation.objects.create(
            order=self.order, variant=self.variant, quantity=2, state='sold',
        )
        self.client.force_login(self.seller, backend=LOGIN_BACKEND)

    def info(self):
        return summary(Order.objects.get(pk=self.order.pk))

    def act(self, action, revision=None, **kwargs):
        if revision is None:
            revision = summary(Order.objects.get(pk=self.order.pk))['revision']
        return change_state(self.order.pk, self.seller, revision, action, **kwargs)

    def ready(self, **kwargs):
        return self.act('ready', **kwargs)

    def ship(self, **kwargs):
        data = {'carrier': 'colissimo', 'tracking_number': TRACKING,
                'notify_customer': True}
        data.update(kwargs)
        return self.act('ship', **data)

    def detail_url(self, order=None):
        return reverse('expeditions:detail', args=[(order or self.order).pk])

    def posted(self, action, revision=None, **kwargs):
        if revision is None:
            revision = summary(Order.objects.get(pk=self.order.pk))['revision']
        data = {'action': action, 'revision': revision}
        data.update(kwargs)
        return self.client.post(self.detail_url(), data)

    def ready_data(self, **kwargs):
        return {'articles_checked': 'on', 'address_checked': 'on',
                'packing_checked': 'on', **kwargs}

    def notices(self, kind=None):
        rows = ReturnMail.objects.filter(order=self.order).order_by('pk')
        return [row for row in rows if kind is None or row.payload['kind'] == kind]

    def shipping_backlog(self, *, count, status='paid', changed_recipient=False):
        from .mail import enqueue_notice
        notices = []
        for index in range(count):
            original_email = f'backlog-{index}@example.test'
            order = Order.objects.create(
                session_key=f'shipping-backlog-{index}', status=status,
                customer_email=original_email, items=deepcopy(self.order.items),
                subtotal=self.order.subtotal, total_paid=self.order.total_paid,
                shipping_details=deepcopy(self.order.shipping_details),
            )
            now = timezone.now()
            fulfillment = Fulfillment.objects.create(
                order=order, state='shipped', revision=2, ready_at=now, shipped_at=now,
                carrier='colissimo', tracking_number=TRACKING,
            )
            notice, created = enqueue_notice(fulfillment, 'shipping')
            self.assertTrue(created)
            notices.append(notice)
            if changed_recipient:
                order.customer_email = f'changed-backlog-{index}@example.test'
                order.save(update_fields=['customer_email'])
        return notices

    def assert_untouched_purchase_and_stock(self, expected):
        self.order.refresh_from_db()
        for field, value in expected.items():
            self.assertEqual(getattr(self.order, field), value, field)
        self.variant.refresh_from_db()
        self.assertEqual((self.variant.quantity, self.variant.reserved), (5, 0))
        reservation = Reservation.objects.get(order=self.order)
        self.assertEqual((reservation.quantity, reservation.state), (2, 'sold'))


@override_settings(**TEST_SETTINGS)
class FulfillmentWorkflowTests(FulfillmentFixture, TestCase):
    def test_get_is_virtual_and_never_creates_fulfillment_events_or_notifications(self):
        before_mail = ReturnMail.objects.count()
        for _ in range(2):
            result = summary(Order.objects.get(pk=self.order.pk))
            self.assertEqual((result['state'], result['revision']), ('preparing', 0))
            for url in (reverse('expeditions:overview'), self.detail_url()):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertFalse(Fulfillment.objects.exists())
        self.assertEqual(ReturnMail.objects.count(), before_mail)

    def test_ready_reopen_and_ship_require_explicit_ordered_transitions(self):
        with self.assertRaises(FulfillmentError):
            self.ship()
        self.assertFalse(Fulfillment.objects.exists())
        self.ready()
        self.assertEqual((self.info()['state'], self.info()['revision']), ('ready', 1))
        self.act('reopen')
        self.assertEqual((self.info()['state'], self.info()['revision']), ('preparing', 2))
        self.ready()
        self.ship()
        result = self.info()
        self.assertEqual((result['state'], result['revision']), ('shipped', 4))
        with self.assertRaises(FulfillmentError):
            self.act('reopen')
        self.assertEqual(len(self.notices('shipping')), 1)

    def test_stale_revision_and_repeated_ship_post_do_not_duplicate_operations(self):
        self.ready()
        self.ship(revision=1)
        before = (Fulfillment.objects.count(), len(self.info()['events']), ReturnMail.objects.count())
        with self.assertRaises(RevisionConflict):
            self.ship(revision=1)
        response = self.posted('ship', revision=1, carrier='colissimo',
                               tracking_number=TRACKING, notify_customer='on', handover_confirmed='on')
        self.assertEqual(response.status_code, 409)
        self.assertEqual((Fulfillment.objects.count(), len(self.info()['events']), ReturnMail.objects.count()), before)
        self.assertEqual(self.info()['revision'], 2)

    def test_pending_expired_orders_and_production_demos_cannot_be_prepared(self):
        for status in ('pending', 'expired'):
            with self.subTest(status=status):
                self.order.status = status
                self.order.save(update_fields=['status'])
                with self.assertRaises(FulfillmentError):
                    self.ready()
                self.assertEqual(self.client.get(self.detail_url()).status_code, 404)
        self.order.status = 'demo'
        self.order.save(update_fields=['status'])
        with override_settings(DEBUG=False):
            with self.assertRaises(FulfillmentError):
                self.ready()
            self.assertEqual(self.client.get(self.detail_url()).status_code, 404)
        self.assertFalse(Fulfillment.objects.exists())

    def test_open_return_blocks_preparation_and_does_not_create_state(self):
        ReturnRequest.objects.create(order=self.order, submission_id=uuid.uuid4(), amount=3600)
        self.assertTrue(self.info()['blocked_reason'])
        with self.assertRaises(FulfillmentError):
            self.ready()
        self.assertFalse(Fulfillment.objects.exists())
        self.assertFalse(ReturnMail.objects.exists())

    def test_return_created_after_preparation_blocks_ship_without_consuming_state(self):
        self.ready()
        ReturnRequest.objects.create(order=self.order, submission_id=uuid.uuid4(), amount=3600)
        with self.assertRaises(FulfillmentError):
            self.ship()
        self.assertEqual((self.info()['state'], self.info()['revision']), ('ready', 1))
        self.assertFalse(self.notices('shipping'))

    def test_any_confirmed_refund_is_conservatively_blocked_even_after_return_closed(self):
        ReturnRequest.objects.create(order=self.order, submission_id=uuid.uuid4(),
                                     amount=3600, state='completed', refund_state='succeeded')
        with self.assertRaises(FulfillmentError):
            self.ready()
        self.assertTrue(self.info()['blocked_reason'])
        self.assertFalse(Fulfillment.objects.exists())

    def test_completed_return_without_confirmed_refund_does_not_block_preparation(self):
        ReturnRequest.objects.create(order=self.order, submission_id=uuid.uuid4(),
                                     amount=3600, state='completed', refund_state='failed')
        self.ready()
        self.assertEqual(self.info()['state'], 'ready')

    def test_ship_requires_valid_fixed_purchase_email_and_mandatory_notification(self):
        self.ready()
        for address in ('', 'not-an-address'):
            with self.subTest(address=address):
                self.order.customer_email = address
                self.order.save(update_fields=['customer_email'])
                with self.assertRaises(FulfillmentError):
                    self.ship()
        self.order.customer_email = 'purchase@example.test'
        self.order.save(update_fields=['customer_email'])
        with self.assertRaises(FulfillmentError):
            self.ship(notify_customer=False)
        self.assertEqual(self.info()['state'], 'ready')
        self.assertFalse(ReturnMail.objects.exists())

    def test_colissimo_identifier_is_normalized_and_invalid_input_does_not_ship(self):
        self.ready()
        for invalid in ('', '8X0000000001', 'http://example.test/parcel',
                        'javascript:alert(1)', '<script>bad</script>'):
            with self.subTest(tracking=invalid):
                with self.assertRaises(FulfillmentError):
                    self.ship(tracking_number=invalid)
        self.ship(tracking_number='8x 000-000-000-01')
        fulfillment = Fulfillment.objects.get(order=self.order)
        self.assertEqual(fulfillment.tracking_number, TRACKING)
        tracking_url = self.info()['tracking_url']
        self.assertEqual(urlsplit(tracking_url).scheme, 'https')
        self.assertIn(TRACKING, tracking_url)
        self.assertFalse('javascript:' in tracking_url)

    def test_fulfillment_preserves_stock_amounts_address_terms_and_invoice_snapshots(self):
        fields = ('items', 'subtotal', 'total_paid', 'paid_line_totals', 'return_shipping_amount',
                  'shipping_details', 'billing_details', 'terms_snapshot', 'invoice_profile',
                  'shipping_snapshot', 'status', 'customer_email')
        original = {field: deepcopy(getattr(self.order, field)) for field in fields}
        self.product.price = Decimal('97.00')
        self.product.name = 'Nouveau nom du catalogue'
        self.product.save()
        self.variant.size = 'XL'
        self.variant.color = 'Vert'
        self.variant.save()
        self.ready()
        self.ship()
        self.act('update_tracking', carrier='colissimo', tracking_number=CORRECTED_TRACKING,
                 notify_customer=True, note='Correction de saisie fictive')
        self.act('resend_notice', notify_customer=True)
        self.assert_untouched_purchase_and_stock(original)
        response = self.client.get(self.detail_url())
        self.assertContains(response, original['items'][0]['name'])
        self.assertNotContains(response, 'Nouveau nom du catalogue')

    def test_shipping_queues_to_order_email_ignoring_account_email_and_posted_recipient(self):
        self.ready()
        with patch('courriels.messages.EmailMultiAlternatives.send') as smtp:
            response = self.posted('ship', carrier='colissimo', tracking_number=TRACKING,
                                   notify_customer='on', handover_confirmed='on', recipient=self.other.email,
                                   customer_email=self.other.email)
            self.assertEqual(response.status_code, 302)
            smtp.assert_not_called()
        notice = self.notices('shipping')[0]
        self.assertEqual(notice.payload['recipient'], 'purchase@example.test')
        self.assertNotEqual(notice.payload['recipient'], self.customer.email)
        self.assertIsNone(notice.sent_at)
        self.assertEqual(notice.attempts, 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_ready_notification_is_optional_and_resend_is_explicit(self):
        self.ready()
        self.assertFalse(ReturnMail.objects.exists())
        self.ship()
        self.assertEqual(len(self.notices()), 1)
        self.client.get(self.detail_url())
        self.assertEqual(len(self.notices()), 1)
        self.act('resend_notice', notify_customer=True)
        self.assertEqual(len(self.notices()), 1)  # Existing delivery is still queued.
        original_notice = self.notices()[0]
        original_notice.sent_at = timezone.now()
        original_notice.save(update_fields=['sent_at'])
        self.act('resend_notice', notify_customer=True)
        self.assertEqual(len(self.notices()), 2)
        first_keys = [notice.key for notice in self.notices()]
        self.act('resend_notice', notify_customer=True)
        self.assertEqual([notice.key for notice in self.notices()], first_keys)
        self.assertEqual(self.info()['revision'], 2)

    def test_ready_can_queue_optional_notice_without_claiming_shipment(self):
        self.ready(notify_customer=True)
        self.assertEqual(self.info()['state'], 'ready')
        self.assertEqual(len(self.notices('shipping_ready')), 1)
        self.assertFalse(self.notices('shipping'))
        self.assertEqual(self.notices()[0].payload['recipient'], self.order.customer_email)

    def test_tracking_correction_requires_reason_and_notifies_without_reopening_shipment(self):
        self.ready()
        self.ship()
        for overrides in ({'note': ''}, {'notify_customer': False}):
            data = {'carrier': 'colissimo', 'tracking_number': CORRECTED_TRACKING,
                    'note': 'Correction fictive', 'notify_customer': True, **overrides}
            with self.assertRaises(FulfillmentError):
                self.act('update_tracking', **data)
        self.act('update_tracking', carrier='colissimo', tracking_number=CORRECTED_TRACKING,
                 note='Correction fictive', notify_customer=True)
        result = self.info()
        self.assertEqual((result['state'], result['revision']), ('shipped', 3))
        self.assertIn(CORRECTED_TRACKING, result['tracking_url'])
        self.assertEqual(len(self.notices('shipping_update')), 1)
        self.assertEqual(self.notices('shipping_update')[0].payload['recipient'], self.order.customer_email)
        self.assertEqual(len(self.notices('shipping')), 1)

    def test_return_after_shipping_does_not_prevent_correcting_customer_tracking(self):
        self.ready()
        self.ship()
        ReturnRequest.objects.create(order=self.order, submission_id=uuid.uuid4(), amount=3600)
        self.act('update_tracking', carrier='colissimo', tracking_number=CORRECTED_TRACKING,
                 note='Correction après déclaration fictive', notify_customer=True)
        self.assertEqual(self.info()['state'], 'shipped')
        self.assertEqual(len(self.notices('shipping_update')), 1)

    @override_settings(EMAIL_DELIVERY_ENABLED=True)
    def test_changed_purchase_email_never_redirects_already_queued_shipping_mail(self):
        self.ready()
        self.ship()
        self.order.customer_email = 'changed@example.test'
        self.order.save(update_fields=['customer_email'])
        with patch('courriels.messages.EmailMultiAlternatives.send') as smtp:
            self.assertEqual(deliver_pending(order_id=self.order.pk), 0)
            smtp.assert_not_called()
        notice = self.notices('shipping')[0]
        self.assertEqual(notice.payload['recipient'], 'purchase@example.test')
        self.assertIsNone(notice.sent_at)

    @override_settings(EMAIL_DELIVERY_ENABLED=True)
    def test_outbox_worker_delivers_once_with_fixed_recipient_and_https_tracking(self):
        self.ready()
        self.ship()
        self.assertEqual(deliver_pending(order_id=self.order.pk), 1)
        self.assertEqual(deliver_pending(order_id=self.order.pk), 0)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ['purchase@example.test'])
        self.assertIn(TRACKING, message.body)
        self.assertIn(self.info()['tracking_url'], message.body)

    @override_settings(EMAIL_DELIVERY_ENABLED=True)
    def test_tracking_correction_supersedes_older_pending_notices(self):
        self.ready(notify_customer=True)
        self.ship()
        self.act('update_tracking', carrier='colissimo', tracking_number=CORRECTED_TRACKING,
                 note='Correction fictive avant traitement des messages', notify_customer=True)
        self.assertEqual(deliver_pending(order_id=self.order.pk), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(CORRECTED_TRACKING, mail.outbox[0].body)
        self.assertNotIn(TRACKING, mail.outbox[0].body)
        self.assertEqual(deliver_pending(order_id=self.order.pk), 0)

    @override_settings(EMAIL_DELIVERY_ENABLED=True)
    def test_thirty_superseded_notices_cannot_starve_latest_tracking_message(self):
        self.ready()
        fulfillment = Fulfillment.objects.get(order=self.order)
        for index in range(30):
            ReturnMail.objects.create(
                order=self.order, key=f'fulfillment-{self.order.pk}-obsolete-{index}',
                superseded_at=timezone.now(),
                payload={'kind': 'shipping', 'recipient': self.order.customer_email,
                         'details': 'OBSOLETE_TRACKING_NOTICE', 'url': 'https://example.test/',
                         'fulfillment_id': fulfillment.pk, 'fulfillment_revision': 0,
                         'fulfillment_superseded': True},
            )
        self.ship()
        self.assertEqual(deliver_pending(limit=30, order_id=self.order.pk), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(TRACKING, mail.outbox[0].body)
        self.assertNotIn('OBSOLETE_TRACKING_NOTICE', mail.outbox[0].body)
        obsolete = ReturnMail.objects.filter(order=self.order, superseded_at__isnull=False)
        self.assertEqual(obsolete.count(), 30)
        self.assertFalse(obsolete.exclude(attempts=0, sent_at__isnull=True).exists())

    def test_privacy_export_contains_only_owned_latest_tracking_and_no_internal_history(self):
        from espace.privacy import subject_export
        self.ready()
        self.ship()
        self.act('update_tracking', carrier='colissimo', tracking_number=CORRECTED_TRACKING,
                 note='PRIVATE_SHIPPING_EXPORT_NOTE', notify_customer=True)
        foreign = Order.objects.create(
            user=self.other, session_key='private-foreign-shipment', status='paid',
            customer_email=self.order.customer_email, subtotal=7200, total_paid=7800,
            items=deepcopy(self.order.items), shipping_details=deepcopy(self.order.shipping_details),
        )
        change_state(foreign.pk, self.seller, 0, 'ready')
        change_state(foreign.pk, self.seller, 1, 'ship', carrier='colissimo',
                     tracking_number='9Z00000000003', notify_customer=True)
        guest = Order.objects.create(
            session_key='private-guest-shipment', status='paid',
            customer_email=self.order.customer_email, subtotal=7200, items=deepcopy(self.order.items),
        )
        exported = subject_export(self.customer)
        self.assertEqual([item['reference'] for item in exported['commandes']], [str(self.order.pk)])
        shipment = exported['commandes'][0]['expedition']
        self.assertEqual(shipment['state'], 'shipped')
        self.assertEqual(shipment['tracking_number'], CORRECTED_TRACKING)
        text = str(exported)
        for private in ('PRIVATE_SHIPPING_EXPORT_NOTE', self.seller.username,
                        str(foreign.pk), str(guest.pk), '9Z00000000003'):
            self.assertNotIn(private, text)
        self.assertNotIn('events', shipment)
        self.assertNotIn('notice', shipment)

    @override_settings(EMAIL_DELIVERY_ENABLED=True, EMAIL_TEST_MODE=False)
    def test_thirty_blocked_demo_notices_do_not_block_real_order_delivery(self):
        backlog = self.shipping_backlog(count=30, status='demo')
        self.ready()
        self.ship()
        self.assertEqual(deliver_pending(limit=30), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.order.customer_email])
        for notice in backlog:
            notice.refresh_from_db()
            self.assertIsNone(notice.sent_at)
            self.assertIsNone(notice.last_attempt_at)
            self.assertEqual(notice.attempts, 0)
        self.assertIsNotNone(self.notices('shipping')[0].sent_at)

    @override_settings(EMAIL_DELIVERY_ENABLED=True)
    def test_thirty_changed_recipients_are_deferred_without_blocking_valid_notice(self):
        backlog = self.shipping_backlog(count=30, changed_recipient=True)
        self.ready()
        self.ship()
        self.assertEqual(deliver_pending(limit=30), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.order.customer_email])
        first_reviews = []
        for index, notice in enumerate(backlog):
            notice.refresh_from_db()
            self.assertIsNone(notice.sent_at)
            self.assertIsNone(notice.superseded_at)
            self.assertEqual(notice.attempts, 0)
            self.assertIsNotNone(notice.last_attempt_at)
            self.assertEqual(notice.payload['recipient'], f'backlog-{index}@example.test')
            first_reviews.append(notice.last_attempt_at)
        self.assertEqual(deliver_pending(limit=30), 0)
        self.assertEqual(len(mail.outbox), 1)
        for notice, reviewed_at in zip(backlog, first_reviews):
            notice.refresh_from_db()
            self.assertEqual(notice.last_attempt_at, reviewed_at)

    def test_tracking_and_internal_correction_reason_are_encrypted_at_rest(self):
        self.ready()
        self.ship()
        reason = 'PRIVATE_CORRECTION_EXAMPLE'
        self.act('update_tracking', carrier='colissimo', tracking_number=CORRECTED_TRACKING,
                 note=reason, notify_customer=True)
        fulfillment = Fulfillment.objects.get(order=self.order)
        event = FulfillmentEvent.objects.get(fulfillment=fulfillment, action='update_tracking')
        self.assertEqual(fulfillment.tracking_number, CORRECTED_TRACKING)
        self.assertIn('Ancien suivi : Colissimo / La Poste · ' + TRACKING, event.note)
        self.assertIn('Nouveau suivi : Colissimo / La Poste · ' + CORRECTED_TRACKING, event.note)
        self.assertIn('Motif interne : ' + reason, event.note)
        quote_name = connection.ops.quote_name
        with connection.cursor() as cursor:
            cursor.execute(
                f'SELECT tracking_number FROM {quote_name(Fulfillment._meta.db_table)} WHERE id = %s',
                [fulfillment.pk],
            )
            self.assertNotIn(CORRECTED_TRACKING, str(cursor.fetchone()[0]))
            cursor.execute(
                f'SELECT note FROM {quote_name(FulfillmentEvent._meta.db_table)} WHERE id = %s',
                [event.pk],
            )
            self.assertNotIn(reason, str(cursor.fetchone()[0]))

    @override_settings(EMAIL_DELIVERY_ENABLED=True, EMAIL_TEST_MODE=False)
    def test_demo_shipping_never_leaves_via_real_delivery_backend(self):
        self.order.status = 'demo'
        self.order.save(update_fields=['status'])
        self.ready()
        self.ship()
        for debug, test_mode, backend in (
            (False, False, 'django.core.mail.backends.smtp.EmailBackend'),
            (False, True, 'courriels.backend.LocalMailboxBackend'),
            (True, False, 'django.core.mail.backends.locmem.EmailBackend'),
            (True, True, 'django.core.mail.backends.locmem.EmailBackend'),
        ):
            with self.subTest(debug=debug, test_mode=test_mode, backend=backend):
                with override_settings(DEBUG=debug, EMAIL_TEST_MODE=test_mode, EMAIL_BACKEND=backend):
                    with patch('courriels.messages.EmailMultiAlternatives.send') as smtp:
                        self.assertEqual(deliver_pending(order_id=self.order.pk), 0)
                        smtp.assert_not_called()
        self.assertIsNone(self.notices('shipping')[0].sent_at)
        self.assert_untouched_purchase_and_stock({'status': 'demo'})

    def test_other_carrier_is_plain_text_and_cannot_inject_tracking_html_or_url(self):
        self.ready()
        carrier_name = '<img src=x onerror=alert(1)>'
        self.ship(carrier='other', carrier_name=carrier_name, tracking_number='TEST-PARCEL-00001')
        notice = self.notices('shipping')[0]
        from courriels.messages import build_message
        message = build_message(notice.payload['kind'], notice.payload['recipient'],
                                notice.payload.get('url') or 'https://example.test/order',
                                notice.payload['details'])
        html = message.alternatives[0][0]
        self.assertNotIn(carrier_name, html)
        self.assertIn('&lt;img', html)
        tracking_url = self.info()['tracking_url']
        self.assertFalse(tracking_url and urlsplit(tracking_url).scheme not in ('https', 'http'))


@override_settings(**TEST_SETTINGS)
class FulfillmentAccessAndUITests(FulfillmentFixture, TestCase):
    def test_manager_permission_is_required_for_read_and_write_and_anonymous_redirects(self):
        overview = reverse('expeditions:overview')
        self.assertEqual(Client().get(overview).status_code, 302)
        staff = get_user_model().objects.create_user('shipping-no-permission', email='staff@example.test', is_staff=True)
        customer_with_permission = get_user_model().objects.create_user('shipping-not-staff', email='nonstaff@example.test')
        customer_with_permission.user_permissions.add(Permission.objects.get(
            content_type__app_label='retours', codename='manage_returns'))
        for user in (staff, customer_with_permission, self.customer):
            verify_test_user(user)
            with self.subTest(user=user.username):
                self.client.force_login(user, backend=LOGIN_BACKEND)
                self.assertEqual(self.client.get(overview).status_code, 403)
                self.assertEqual(self.client.get(self.detail_url()).status_code, 403)
                self.assertEqual(self.posted('ready', **self.ready_data()).status_code, 403)
                with self.assertRaises(PermissionDenied):
                    change_state(self.order.pk, user, 0, 'ready')
        self.assertFalse(Fulfillment.objects.exists())

    def test_unverified_manager_is_redirected_and_cannot_bypass_service_guard(self):
        EmailVerification.objects.filter(user=self.seller).delete()
        self.assertRedirects(self.client.get(self.detail_url()), reverse('connexion:verify_email'), fetch_redirect_response=False)
        self.assertRedirects(self.posted('ready', **self.ready_data()), reverse('connexion:verify_email'), fetch_redirect_response=False)
        with self.assertRaises(PermissionDenied):
            self.ready()
        self.assertFalse(Fulfillment.objects.exists())

    def test_csrf_required_for_preparation_shipping_correction_and_resend(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.seller, backend=LOGIN_BACKEND)
        for action in ('ready', 'reopen', 'ship', 'update_tracking', 'resend_notice'):
            with self.subTest(action=action):
                response = strict.post(self.detail_url(), {'action': action, 'revision': 0})
                self.assertEqual(response.status_code, 403)
        self.assertFalse(Fulfillment.objects.exists())

    def test_ready_requires_three_checks_and_ignores_injected_state_actor_and_price(self):
        response = self.posted('ready')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Fulfillment.objects.exists())
        response = self.posted('ready', **self.ready_data(
            state='shipped', actor=self.other.pk, customer_email=self.other.email, total_paid=1))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.info()['state'], 'ready')
        self.order.refresh_from_db()
        self.assertEqual((self.order.customer_email, self.order.total_paid), ('purchase@example.test', 7800))
        self.assertFalse(ReturnMail.objects.exists())

    def test_shipping_requires_confirming_actual_handover(self):
        self.ready()
        response = self.posted('ship', carrier='colissimo', tracking_number=TRACKING,
                               notify_customer='on')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.info()['state'], 'ready')
        self.assertFalse(self.notices('shipping'))

    def test_unknown_action_and_missing_revision_do_not_write(self):
        self.assertEqual(self.posted('made-up-action').status_code, 400)
        response = self.client.post(self.detail_url(), {'action': 'ready', **self.ready_data()})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Fulfillment.objects.exists())

    def test_overview_separates_paid_and_demo_and_filters_by_virtual_or_actual_state(self):
        demo = Order.objects.create(user=self.other, session_key='demo-shipping', status='demo',
                                    items=[], subtotal=0, customer_email=self.other.email)
        pending = Order.objects.create(session_key='pending-shipping', status='pending', items=[], subtotal=0)
        url = reverse('expeditions:overview')
        response = self.client.get(url)
        self.assertContains(response, str(self.order.pk))
        self.assertNotContains(response, str(demo.pk))
        self.assertNotContains(response, str(pending.pk))
        response = self.client.get(url, {'scope': 'demo', 'state': 'all'})
        self.assertContains(response, str(demo.pk))
        self.assertNotContains(response, str(self.order.pk))
        self.ready()
        self.assertNotContains(self.client.get(url), str(self.order.pk))
        self.assertContains(self.client.get(url, {'state': 'ready'}), str(self.order.pk))

    def test_owner_sees_shipment_only_on_own_private_order_and_signed_return_page(self):
        self.ready()
        self.ship()
        self.act('update_tracking', carrier='colissimo', tracking_number=CORRECTED_TRACKING,
                 note='PRIVATE_SHIPMENT_NOTE', notify_customer=True)
        self.client.force_login(self.customer, backend=LOGIN_BACKEND)
        url = reverse('espace:order', args=[self.order.pk])
        response = self.client.get(url)
        self.assertContains(response, CORRECTED_TRACKING)
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertNotContains(response, 'PRIVATE_SHIPMENT_NOTE')
        self.client.force_login(self.other, backend=LOGIN_BACKEND)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(self.detail_url()).status_code, 403)
        self.client.logout()
        return_url = reverse('retours:order', args=[self.order.pk])
        self.assertEqual(self.client.get(return_url).status_code, 403)
        response = self.client.get(return_url, {'token': order_token(self.order)})
        self.assertContains(response, CORRECTED_TRACKING)
        self.assertNotContains(response, 'PRIVATE_SHIPMENT_NOTE')
        self.assertEqual(response.headers['Referrer-Policy'], 'no-referrer')

    def test_same_origin_forms_preserve_csrf_and_reject_foreign_origin(self):
        strict = Client(enforce_csrf_checks=True)
        strict.cookies = self.client.cookies.copy()
        response = strict.get(self.detail_url())
        self.assertEqual(response.headers['Referrer-Policy'], 'same-origin')
        data = {'action': 'ready', 'revision': '0', **self.ready_data(),
                'csrfmiddlewaretoken': strict.cookies['csrftoken'].value}
        self.assertEqual(strict.post(self.detail_url(), data,
            HTTP_ORIGIN='https://outside.example.test').status_code, 403)
        self.assertFalse(Fulfillment.objects.exists())
        self.assertEqual(strict.post(self.detail_url(), data,
            HTTP_ORIGIN='http://testserver').status_code, 302)
        self.assertEqual(Fulfillment.objects.get(order=self.order).state, 'ready')


@override_settings(**TEST_SETTINGS)
class FulfillmentConcurrencyTests(FulfillmentFixture, TransactionTestCase):
    def test_simultaneous_shipments_lock_order_and_queue_exactly_once(self):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL order-lock test')
        self.ready()
        order_id, actor_id = self.order.pk, self.seller.pk

        def ship_in_connection(_):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=actor_id)
                try:
                    change_state(order_id, actor, 1, 'ship', carrier='colissimo',
                                 tracking_number=TRACKING, notify_customer=True)
                    return True
                except FulfillmentError:
                    return False
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(ship_in_connection, range(2)))
        self.assertEqual(result.count(True), 1)
        self.assertEqual(summary(Order.objects.get(pk=order_id))['revision'], 2)
        self.assertEqual(len(self.notices('shipping')), 1)
        self.assertEqual(Fulfillment.objects.count(), 1)


@override_settings(**TEST_SETTINGS)
class FulfillmentPrioritiesTests(FulfillmentFixture, TestCase):
    def setUp(self):
        super().setUp()
        # The shared fixture exercises domain operations elsewhere. Keep it out
        # of this class's dated worklists without deleting purchase history.
        self.order.status = 'expired'
        self.order.save(update_fields=['status'])
        self.anchor = timezone.now().replace(microsecond=0)

    def dated_order(self, *, created_at, paid_at=None, status='paid',
                    state='preparing', ready_at=None, shipped_at=None, identifier=None):
        values = {
            'session_key': 'priority-' + uuid.uuid4().hex,
            'status': status, 'paid_at': paid_at,
            'customer_email': 'priority-customer@example.test',
            'items': deepcopy(self.order.items), 'subtotal': self.order.subtotal,
            'total_paid': self.order.total_paid,
            'shipping_details': deepcopy(self.order.shipping_details),
        }
        if identifier is not None:
            values['pk'] = uuid.UUID(int=identifier)
        order = Order.objects.create(**values)
        Order.objects.filter(pk=order.pk).update(created_at=created_at)
        order.created_at = created_at
        if state != 'preparing':
            Fulfillment.objects.create(
                order=order, state=state, revision=1 if state == 'ready' else 2,
                ready_at=ready_at or paid_at or created_at,
                shipped_at=shipped_at, carrier='colissimo',
                tracking_number=TRACKING if state == 'shipped' else '',
            )
        return order

    def worklist(self, *, state='preparing', scope='paid', page=1):
        response = self.client.get(reverse('expeditions:overview'),
                                   {'state': state, 'scope': scope, 'page': page})
        self.assertEqual(response.status_code, 200)
        return response

    def row_ids(self, response):
        return [row['order'].pk for row in response.context['rows']]

    def test_preparing_oldest_payment_first_even_when_newer_purchase_was_created_earlier(self):
        recent = self.dated_order(created_at=self.anchor - timedelta(days=8),
                                  paid_at=self.anchor - timedelta(hours=1))
        older = self.dated_order(created_at=self.anchor - timedelta(days=3),
                                 paid_at=self.anchor - timedelta(days=2))
        response = self.worklist()
        self.assertEqual(self.row_ids(response), [older.pk, recent.pk])
        self.assertFalse(Fulfillment.objects.exists())
        self.assertFalse(ReturnMail.objects.exists())

    def test_equal_payment_dates_use_creation_then_stable_uuid_order(self):
        paid = self.anchor - timedelta(days=1)
        same_creation = self.anchor - timedelta(days=2)
        later_uuid = self.dated_order(created_at=same_creation, paid_at=paid, identifier=20)
        oldest_creation = self.dated_order(created_at=self.anchor - timedelta(days=3),
                                          paid_at=paid, identifier=30)
        earlier_uuid = self.dated_order(created_at=same_creation, paid_at=paid, identifier=10)
        self.assertEqual(self.row_ids(self.worklist()),
                         [oldest_creation.pk, earlier_uuid.pk, later_uuid.pk])

    def test_ready_worklist_uses_purchase_age_instead_of_last_preparation_time(self):
        older_purchase = self.dated_order(
            created_at=self.anchor - timedelta(days=5),
            paid_at=self.anchor - timedelta(days=4), state='ready',
            ready_at=self.anchor - timedelta(hours=1))
        recent_purchase = self.dated_order(
            created_at=self.anchor - timedelta(days=3),
            paid_at=self.anchor - timedelta(days=2), state='ready',
            ready_at=self.anchor - timedelta(days=1))
        response = self.worklist(state='ready')
        self.assertEqual(self.row_ids(response), [older_purchase.pk, recent_purchase.pk])
        self.assertEqual(response.context['rows'][0]['waiting']['since'], older_purchase.paid_at)

    def test_demo_orders_without_payment_date_fall_back_to_oldest_creation(self):
        newer = self.dated_order(created_at=self.anchor - timedelta(minutes=15), status='demo')
        older = self.dated_order(created_at=self.anchor - timedelta(days=3), status='demo')
        self.dated_order(created_at=self.anchor - timedelta(days=10),
                         paid_at=self.anchor - timedelta(days=9))
        response = self.worklist(scope='demo')
        self.assertEqual(self.row_ids(response), [older.pk, newer.pk])
        self.assertEqual(response.context['rows'][0]['waiting']['since'], older.created_at)

    def test_legacy_paid_orders_without_payment_date_are_sorted_by_creation_among_other_orders(self):
        recent_legacy = self.dated_order(created_at=self.anchor - timedelta(hours=1))
        paid = self.dated_order(created_at=self.anchor - timedelta(days=3),
                                paid_at=self.anchor - timedelta(days=2))
        old_legacy = self.dated_order(created_at=self.anchor - timedelta(days=4))
        response = self.worklist()
        self.assertEqual(self.row_ids(response), [old_legacy.pk, paid.pk, recent_legacy.pk])
        self.assertEqual(response.context['rows'][0]['waiting']['since'], old_legacy.created_at)

    def test_shipped_worklist_uses_latest_departure_and_does_not_show_waiting(self):
        old_purchase_recent_departure = self.dated_order(
            created_at=self.anchor - timedelta(days=8), paid_at=self.anchor - timedelta(days=7),
            state='shipped', shipped_at=self.anchor - timedelta(hours=1))
        newer_purchase_older_departure = self.dated_order(
            created_at=self.anchor - timedelta(days=4), paid_at=self.anchor - timedelta(days=3),
            state='shipped', shipped_at=self.anchor - timedelta(days=2))
        latest_departure = self.dated_order(
            created_at=self.anchor - timedelta(days=6), paid_at=self.anchor - timedelta(days=5),
            state='shipped', shipped_at=self.anchor - timedelta(minutes=5))
        response = self.worklist(state='shipped')
        self.assertEqual(self.row_ids(response), [latest_departure.pk,
                         old_purchase_recent_departure.pk, newer_purchase_older_departure.pk])
        self.assertTrue(all(row['waiting'] is None for row in response.context['rows']))
        self.assertNotContains(response, 'En attente d’expédition depuis')

    def test_all_states_preserves_newest_purchase_order_with_null_payment_fallback(self):
        older_paid = self.dated_order(
            created_at=self.anchor - timedelta(days=4), paid_at=self.anchor - timedelta(days=2),
            state='ready', ready_at=self.anchor - timedelta(days=1))
        recent_legacy = self.dated_order(created_at=self.anchor - timedelta(hours=1))
        newest_paid = self.dated_order(created_at=self.anchor - timedelta(days=5),
                                       paid_at=self.anchor - timedelta(minutes=30))
        response = self.worklist(state='all')
        self.assertEqual(self.row_ids(response), [newest_paid.pk, recent_legacy.pk, older_paid.pk])

    def test_pagination_keeps_the_twenty_five_oldest_on_first_page(self):
        oldest_first = []
        for index in range(26):
            paid_at = self.anchor - timedelta(hours=26 - index)
            order = self.dated_order(created_at=paid_at - timedelta(minutes=1), paid_at=paid_at)
            oldest_first.append(order.pk)
        first = self.worklist()
        self.assertEqual(first.context['page_obj'].paginator.count, 26)
        self.assertEqual(self.row_ids(first), oldest_first[:25])
        self.assertNotIn(oldest_first[-1], self.row_ids(first))
        self.assertEqual(self.row_ids(self.worklist(page=2)), oldest_first[25:])

    def test_waiting_label_uses_payment_instead_of_creation_and_clamps_future_dates(self):
        older_paid = self.dated_order(created_at=self.anchor - timedelta(days=10),
                                      paid_at=self.anchor - timedelta(hours=2))
        future_paid = self.dated_order(created_at=self.anchor - timedelta(minutes=5),
                                       paid_at=self.anchor + timedelta(minutes=10))
        with patch('expeditions.views.timezone.now', return_value=self.anchor):
            response = self.worklist()
        rows = response.context['rows']
        self.assertEqual([row['order'].pk for row in rows], [older_paid.pk, future_paid.pk])
        self.assertEqual(rows[0]['waiting'], {'since': older_paid.paid_at,
                         'label': 'En attente d’expédition depuis 2 heures'})
        self.assertEqual(rows[1]['waiting'], {'since': future_paid.paid_at,
                         'label': 'En attente d’expédition depuis moins d’une minute'})
        self.assertContains(response, 'En attente d’expédition depuis 2 heures')
        self.assertNotContains(response, 'En retard')
        self.assertNotContains(response, 'Temps d’attente négatif')

    def test_waiting_labels_share_one_reference_time_for_the_worklist(self):
        from .priorities import waiting_time
        for hours in (2, 3):
            self.dated_order(created_at=self.anchor - timedelta(days=5),
                             paid_at=self.anchor - timedelta(hours=hours))
        with patch('expeditions.views.waiting_time', wraps=waiting_time) as duration:
            self.worklist()
        self.assertEqual(duration.call_count, 2)
        reference_times = [call.args[1] for call in duration.call_args_list]
        self.assertIs(reference_times[0], reference_times[1])

    def test_waiting_time_boundaries_use_complete_minutes_hours_and_days(self):
        from .priorities import waiting_time
        cases = [
            (timedelta(seconds=0), 'moins d’une minute'),
            (timedelta(seconds=59, microseconds=999999), 'moins d’une minute'),
            (timedelta(minutes=1), '1 minute'),
            (timedelta(minutes=2), '2 minutes'),
            (timedelta(minutes=59, seconds=59), '59 minutes'),
            (timedelta(hours=1), '1 heure'),
            (timedelta(hours=2), '2 heures'),
            (timedelta(hours=23, minutes=59, seconds=59), '23 heures'),
            (timedelta(days=1), '1 jour'),
            (timedelta(days=2), '2 jours'),
        ]
        for elapsed, expected in cases:
            with self.subTest(elapsed=elapsed):
                self.assertEqual(waiting_time(self.anchor - elapsed, self.anchor), expected)
        self.assertEqual(waiting_time(self.anchor + timedelta(days=1), self.anchor),
                         'moins d’une minute')
