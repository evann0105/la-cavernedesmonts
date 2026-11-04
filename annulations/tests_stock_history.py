"""Exact, immutable stock history is committed together with cancellation stock."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Product
from paiement.models import Order
from retours.models import ReturnRequest
from retours.refunds import apply_verified_event
from stocks.models import Reservation, Variant

from .models import Cancellation, CancellationStockMovement
from .services import finalize_refund, preview, request_cancellation, retry_stock


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True, PAYMENTS_ENABLED=True,
                   STRIPE_SECRET_KEY='sk_test_fake', PAYMENT_TEST_MODE=False,
                   EMAIL_DELIVERY_ENABLED=False,
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class CancellationStockHistoryTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.seller = users.objects.create_superuser(
            'stock-history-seller', email='seller@example.test', password='synthetic-test-password')
        self.customer = users.objects.create_user('stock-history-client', email='client@example.test')
        self.product = Product.objects.create(name='Polaire du catalogue',
            slug='cancel-stock-history-polaire', price=Decimal('40.00'))
        self.variant = Variant.objects.create(product=self.product, size='M', color='Vert',
            quantity=5, reserved=1)
        self.order = Order.objects.create(user=self.customer, session_key='synthetic-stock-history',
            customer_email=self.customer.email, status='paid', paid_at=timezone.now(),
            stripe_session_id='cs_stock_history', subtotal=8000, total_paid=6690,
            return_shipping_amount=690, paid_line_totals=[6000], items=[{
                'product': self.product.pk, 'variant': self.variant.pk,
                'name': 'Polaire lors de l’achat', 'size': 'M', 'color': 'Vert',
                'quantity': 2, 'unit_amount': 4000}])
        self.reservation = Reservation.objects.create(order=self.order, variant=self.variant,
            quantity=2, state='sold')

    def result(self, status='succeeded'):
        refund = ReturnRequest.objects.get(order=self.order)
        return {'id': 're_stock_history', 'amount': refund.amount, 'currency': 'eur',
                'payment_intent': 'pi_stock_history', 'metadata': {'return_id': str(refund.pk)},
                'status': status}

    def cancel(self, status='succeeded'):
        self.order.refresh_from_db()
        session = {'payment_status': 'paid', 'amount_total': self.order.total_paid,
                   'client_reference_id': str(self.order.pk), 'payment_intent': 'pi_stock_history'}
        with patch('retours.refunds.stripe.checkout.Session.retrieve', return_value=session), \
             patch('retours.refunds.stripe.Refund.list', return_value={'data': []}), \
             patch('retours.refunds.stripe.Refund.create', side_effect=lambda **kwargs: self.result(status)):
            return request_cancellation(self.order.pk, self.seller,
                'Annulation demandée par le client.', preview(self.order)['token'], True)

    def assert_stock(self, quantity):
        self.variant.refresh_from_db()
        self.reservation.refresh_from_db()
        self.assertEqual((self.variant.quantity, self.variant.reserved), (quantity, 1))
        self.assertEqual(self.reservation.state, 'sold')

    def test_automatic_success_records_exact_total_stock_and_preserved_reservations(self):
        cancellation = self.cancel()
        movement = cancellation.stock_movements.get()
        self.assertEqual(movement.variant_id, self.variant.pk)
        self.assertEqual((movement.product_name, movement.size, movement.color),
                         ('Polaire du catalogue', 'M', 'Vert'))
        self.assertEqual((movement.quantity_before, movement.quantity_added, movement.quantity_after,
                          movement.reserved), (5, 2, 7, 1))
        self.assertEqual(movement.source, 'automatic')
        self.assertIsNone(movement.actor_id)
        self.assertLessEqual(movement.created_at, cancellation.stock_completed_at)
        self.assert_stock(7)

    def test_repeated_variant_lines_create_one_aggregated_movement(self):
        line = self.order.items[0]
        self.order.items = [{**line, 'quantity': 1}, {**line, 'quantity': 1}]
        self.order.paid_line_totals = [3000, 3000]
        self.order.save(update_fields=['items', 'paid_line_totals'])
        cancellation = self.cancel()
        self.assertEqual(cancellation.refund.lines.count(), 2)
        self.assertEqual(cancellation.stock_movements.count(), 1)
        self.assertEqual(cancellation.stock_movements.get().quantity_added, 2)
        self.assert_stock(7)

    def test_retries_and_provider_replays_never_rewrite_history_or_restock_twice(self):
        cancellation = self.cancel()
        original = list(cancellation.stock_movements.values())
        self.variant.quantity = 12
        self.variant.save(update_fields=['quantity'])
        with patch('stripe.Refund.create') as create:
            finalize_refund(cancellation.refund_id)
            retry_stock(self.order.pk, self.seller)
            apply_verified_event(self.result())
            apply_verified_event(self.result())
        create.assert_not_called()
        self.assert_stock(12)
        self.assertEqual(original, list(cancellation.stock_movements.values()))

    def test_manual_retry_records_the_actual_stock_then_and_the_actor_who_retried(self):
        cancellation = self.cancel('pending')
        with patch('annulations.services.CancellationStockMovement.objects.create',
                   side_effect=RuntimeError('History unavailable')):
            apply_verified_event(self.result())
        cancellation.refresh_from_db()
        self.assertIsNone(cancellation.stock_completed_at)
        self.assertEqual(cancellation.refund.refund_state, 'succeeded')
        self.assertFalse(cancellation.stock_movements.exists())
        self.assert_stock(5)
        self.variant.quantity = 9
        self.variant.save(update_fields=['quantity'])
        operator = get_user_model().objects.create_superuser(
            'stock-history-second-seller', email='second@example.test', password='synthetic-test-password')
        with patch('stripe.Refund.create') as create:
            retry_stock(self.order.pk, operator)
        create.assert_not_called()
        movement = cancellation.stock_movements.get()
        self.assertEqual((movement.quantity_before, movement.quantity_added, movement.quantity_after), (9, 2, 11))
        self.assertEqual(movement.source, 'manual')
        self.assertEqual(movement.actor_id, operator.pk)
        self.assert_stock(11)

    def test_deleted_manual_actor_keeps_the_manual_source_and_snapshot(self):
        cancellation = self.cancel('pending')
        ReturnRequest.objects.filter(pk=cancellation.refund_id).update(
            refund_state='succeeded', refunded_at=timezone.now())
        operator = get_user_model().objects.create_superuser(
            'stock-history-removable-seller', email='removable@example.test', password='synthetic-test-password')
        retry_stock(self.order.pk, operator)
        movement = cancellation.stock_movements.get()
        operator.delete()
        movement.refresh_from_db()
        self.assertIsNone(movement.actor_id)
        self.assertEqual(movement.source, 'manual')
        self.assertEqual((movement.quantity_before, movement.quantity_added, movement.quantity_after), (5, 2, 7))

    def test_catalogue_edits_do_not_change_saved_product_size_or_color(self):
        cancellation = self.cancel()
        self.product.name = 'Nouvelle désignation'
        self.product.save(update_fields=['name'])
        self.variant.size = 'XL'
        self.variant.color = 'Bleu'
        self.variant.save(update_fields=['size', 'color'])
        movement = cancellation.stock_movements.get()
        self.assertEqual((movement.product_name, movement.size, movement.color),
                         ('Polaire du catalogue', 'M', 'Vert'))

    def test_history_is_immutable_through_instance_and_queryset_operations(self):
        movement = self.cancel().stock_movements.get()
        before = CancellationStockMovement.objects.values().get(pk=movement.pk)
        movement.quantity_before = 100
        operations = (
            lambda: movement.save(), lambda: movement.delete(),
            lambda: CancellationStockMovement.objects.filter(pk=movement.pk).update(quantity_before=100),
            lambda: CancellationStockMovement.objects.bulk_update([movement], ['quantity_before']),
            lambda: CancellationStockMovement.objects.filter(pk=movement.pk).delete(),
        )
        for operation in operations:
            with self.subTest(operation=operation), self.assertRaises(ValidationError):
                operation()
        self.assertEqual(before, CancellationStockMovement.objects.values().get(pk=movement.pk))

    def test_database_rejects_zero_addition_unbalanced_values_and_duplicate_variant(self):
        cancellation = self.cancel('pending')
        values = {'cancellation': cancellation, 'variant': self.variant,
                  'product_name': 'P' * 255, 'size': 'M', 'color': 'Vert',
                  'quantity_before': 5, 'quantity_added': 2, 'quantity_after': 7, 'reserved': 1}
        for invalid in ({'quantity_added': 0, 'quantity_after': 5}, {'quantity_after': 8}):
            with self.subTest(invalid=invalid), self.assertRaises(IntegrityError), transaction.atomic():
                CancellationStockMovement.objects.create(**{**values, **invalid})
        movement = CancellationStockMovement.objects.create(**values)
        self.assertEqual(len(movement.product_name), 255)
        with self.assertRaises(IntegrityError), transaction.atomic():
            CancellationStockMovement.objects.create(**values)

    def test_failure_on_second_history_line_rolls_back_all_stock_and_history_but_not_refund(self):
        second = Variant.objects.create(product=self.product, size='L', color='Rouge', quantity=9)
        self.order.items = [*self.order.items, {'product': self.product.pk, 'variant': second.pk,
            'name': 'Polaire rouge', 'size': 'L', 'color': 'Rouge', 'quantity': 1, 'unit_amount': 1000}]
        self.order.subtotal = 9000
        self.order.total_paid = 9690
        self.order.paid_line_totals = [8000, 1000]
        self.order.save(update_fields=['items', 'subtotal', 'total_paid', 'paid_line_totals'])
        Reservation.objects.create(order=self.order, variant=second, quantity=1, state='sold')
        cancellation = self.cancel('pending')
        create = CancellationStockMovement.objects.create

        def insert_or_fail(**kwargs):
            if kwargs['variant'].pk == second.pk:
                raise IntegrityError('Synthetic failure on the second movement')
            return create(**kwargs)

        with patch('annulations.services.CancellationStockMovement.objects.create', side_effect=insert_or_fail):
            apply_verified_event(self.result())
        cancellation.refresh_from_db()
        self.assertEqual(cancellation.refund.refund_state, 'succeeded')
        self.assertIsNone(cancellation.stock_completed_at)
        self.assertTrue(cancellation.stock_error)
        self.assertFalse(cancellation.stock_movements.exists())
        self.assert_stock(5)
        second.refresh_from_db()
        self.assertEqual(second.quantity, 9)
        retry_stock(self.order.pk, self.seller)
        self.assertEqual(cancellation.stock_movements.count(), 2)
        self.assert_stock(7)
        second.refresh_from_db()
        self.assertEqual(second.quantity, 10)

    def test_old_completed_cancellation_never_gets_an_invented_stock_history(self):
        cancellation = self.cancel('pending')
        now = timezone.now()
        Cancellation.objects.filter(pk=cancellation.pk).update(stock_completed_at=now)
        ReturnRequest.objects.filter(pk=cancellation.refund_id).update(
            refund_state='succeeded', refunded_at=now, state='completed', completed_at=now)
        finalize_refund(cancellation.refund_id)
        retry_stock(self.order.pk, self.seller)
        self.assertFalse(cancellation.stock_movements.exists())
        self.assert_stock(5)

    def test_demo_completion_never_creates_real_stock_movements(self):
        self.order.status = 'demo'
        self.order.save(update_fields=['status'])
        with patch('stripe.Refund.create') as create:
            cancellation = request_cancellation(self.order.pk, self.seller,
                'Simulation', preview(self.order)['token'], True)
        create.assert_not_called()
        self.assertIsNotNone(cancellation.stock_completed_at)
        self.assertFalse(cancellation.stock_movements.exists())
        self.assert_stock(5)

    def test_postgresql_stock_integer_overflow_keeps_refund_confirmed_and_no_history(self):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL integer storage limit')
        cancellation = self.cancel('pending')
        maximum = 2147483647
        self.variant.quantity = maximum - 1
        self.variant.save(update_fields=['quantity'])
        apply_verified_event(self.result())
        cancellation.refresh_from_db()
        self.assertEqual(cancellation.refund.refund_state, 'succeeded')
        self.assertIsNone(cancellation.stock_completed_at)
        self.assertTrue(cancellation.stock_error)
        self.assertFalse(cancellation.stock_movements.exists())
        self.assert_stock(maximum - 1)
