"""PostgreSQL races for cancellation, stock restoration and shipment boundaries."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from core.models import Product
from expeditions.models import Fulfillment, FulfillmentEvent
from expeditions.services import FulfillmentError, change_state
from paiement.models import Order
from retours.models import ReturnMail, ReturnRequest
from retours.services import ReturnError, submit
from stocks.models import Reservation, Variant

from .models import Cancellation
from .services import CancellationError, finalize_refund, preview, request_cancellation


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True,
                   EMAIL_DELIVERY_ENABLED=False, EMAIL_TEST_MODE=False,
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   PAYMENTS_ENABLED=False, STRIPE_SECRET_KEY='',
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class CancellationConcurrencyTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL order-lock test')
        self.seller = get_user_model().objects.create_superuser(
            'cancel-race-manager', email='seller@example.test', password='synthetic-test-password')
        self.customer = get_user_model().objects.create_user(
            'cancel-race-client', email='client@example.test')
        self.product = Product.objects.create(
            name='Polaire synthétique', slug='cancellation-race-polaire', price=Decimal('36.00'))
        self.variant = Variant.objects.create(
            product=self.product, size='M', color='Bleu', quantity=5, reserved=1)
        self.order = Order.objects.create(user=self.customer, session_key='synthetic-cancellation-race',
            status='paid', paid_at=timezone.now(), stripe_session_id='cs_synthetic_cancellation_race',
            customer_email=self.customer.email, subtotal=7200, total_paid=6601,
            paid_line_totals=[6001], return_shipping_amount=600,
            items=[{'product': self.product.pk, 'variant': self.variant.pk,
                    'name': self.product.name, 'size': 'M', 'color': 'Bleu',
                    'quantity': 2, 'unit_amount': 3600}],
            shipping_details={'name': 'Client synthétique', 'address': {
                'line1': '1 rue Exemple', 'postal_code': '75000', 'city': 'Ville Test', 'country': 'FR'}})
        Reservation.objects.create(order=self.order, variant=self.variant, quantity=2, state='sold')
        self.token = preview(self.order)['token']

    def actor(self):
        # Never share a connection-bound user object between worker threads.
        return get_user_model().objects.get(pk=self.seller.pk)

    def cancel(self):
        return request_cancellation(self.order.pk, self.actor(),
            'Demande du client avant expédition.', self.token, True)

    def race(self, *actions):
        barrier = Barrier(len(actions))

        def worker(action):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    # A broken lock order should fail, never hang the test suite.
                    cursor.execute("SET lock_timeout = '5s'")
                    cursor.execute("SET statement_timeout = '10s'")
                barrier.wait(timeout=10)
                return action()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=len(actions)) as pool:
            futures = [pool.submit(worker, action) for action in actions]
            return [future.result(timeout=20) for future in futures]

    def test_double_confirmation_creates_only_one_cancellation_and_refund_attempt(self):
        with patch('retours.refunds.initiate', side_effect=ReturnError('Paiements de test désactivés.')) as initiate:
            results = self.race(lambda: self.cancel().pk, lambda: self.cancel().pk)
        self.assertEqual(results[0], results[1])
        self.assertEqual(initiate.call_count, 1)
        self.assertEqual(Cancellation.objects.filter(order=self.order).count(), 1)
        refund = ReturnRequest.objects.get(order=self.order)
        self.assertEqual(refund.amount, 6601)
        self.assertEqual(refund.shipping_amount, 600)
        self.assertEqual(refund.kind, 'cancellation')
        self.assertEqual(ReturnMail.objects.filter(request=refund).count(), 1)
        self.assertEqual(FulfillmentEvent.objects.filter(fulfillment__order=self.order, action='cancel').count(), 1)
        self.variant.refresh_from_db()
        self.assertEqual((self.variant.quantity, self.variant.reserved), (5, 1))

    def test_double_finalization_restores_stock_once_and_preserves_other_reservations(self):
        cancellation = self.cancel()
        ReturnRequest.objects.filter(pk=cancellation.refund_id).update(
            refund_state='succeeded', refunded_at=timezone.now())
        self.race(lambda: finalize_refund(cancellation.refund_id),
                  lambda: finalize_refund(cancellation.refund_id))
        cancellation.refresh_from_db()
        self.variant.refresh_from_db()
        self.order.refresh_from_db()
        self.assertIsNotNone(cancellation.stock_completed_at)
        self.assertEqual(cancellation.stock_error, '')
        self.assertEqual((self.variant.quantity, self.variant.reserved), (7, 1))
        self.assertEqual(self.order.status, 'paid')
        self.assertEqual(self.order.reservations.get().state, 'sold')
        self.assertEqual(FulfillmentEvent.objects.filter(
            fulfillment__order=self.order, action='cancel_completed').count(), 1)
        self.assertEqual(ReturnRequest.objects.get(pk=cancellation.refund_id).state, 'completed')

    def test_cancellation_and_shipping_cannot_both_succeed(self):
        fulfillment = change_state(self.order.pk, self.seller, 0, 'ready')
        revision = fulfillment.revision
        self.token = preview(Order.objects.get(pk=self.order.pk))['token']

        def cancel():
            try:
                self.cancel()
                return True
            except CancellationError:
                return False

        def ship():
            try:
                change_state(self.order.pk, self.actor(), revision, 'ship',
                    carrier='colissimo', tracking_number='8X00000000001', notify_customer=True)
                return True
            except FulfillmentError:
                return False

        cancelled, shipped = self.race(cancel, ship)
        self.assertEqual(int(cancelled) + int(shipped), 1)
        fulfillment.refresh_from_db()
        self.assertEqual(fulfillment.state, 'cancelled' if cancelled else 'shipped')
        self.assertEqual(Cancellation.objects.filter(order=self.order).exists(), cancelled)
        self.assertEqual(FulfillmentEvent.objects.filter(
            fulfillment=fulfillment, action='ship').count(), int(shipped))
        self.assertEqual(ReturnRequest.objects.filter(order=self.order).count(), int(cancelled))
        self.variant.refresh_from_db()
        self.assertEqual((self.variant.quantity, self.variant.reserved), (5, 1))

    def test_cancellation_and_return_submission_cannot_claim_the_same_purchase(self):
        def cancel():
            try:
                self.cancel()
                return True
            except CancellationError:
                return False

        def declare_return():
            try:
                submit(self.order.pk, {0: 2}, uuid.uuid4(), reason='Demande de retour synthétique.')
                return True
            except ReturnError:
                return False

        cancelled, returned = self.race(cancel, declare_return)
        self.assertEqual(int(cancelled) + int(returned), 1)
        refund = ReturnRequest.objects.get(order=self.order)
        self.assertEqual(refund.kind, 'cancellation' if cancelled else 'withdrawal')
        self.assertEqual(refund.amount, self.order.total_paid)
        self.assertEqual(sum(refund.lines.values_list('quantity', flat=True)), 2)
        self.assertEqual(Cancellation.objects.filter(order=self.order).exists(), cancelled)
        self.assertEqual(ReturnMail.objects.filter(request=refund).count(), 1)
        self.variant.refresh_from_db()
        self.assertEqual((self.variant.quantity, self.variant.reserved), (5, 1))
