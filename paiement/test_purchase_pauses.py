"""A holiday closes new purchases, never the reconciliation of an accepted payment."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
import hmac
from importlib import import_module
import json
from threading import Barrier, Event
import time
from unittest.mock import patch

import stripe
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from avoirs.models import CustomerBenefit
from avoirs.services import save_benefit
from connexion.test_helpers import verify_test_user
from core.models import Product
from stocks.models import Reservation, Variant
from stocks.reservations import StockUnavailable, reserve
from suspensions.models import PauseConfiguration, PauseEvent, PurchaseAdmission, PurchasePause
from suspensions.services import PauseConflict, _lock, save_pause
from .models import Order
from .stock_checkout import get_session, settle
from .tests import CONFIG, SimpleNamespace
from .views import ready


class PauseFixtures:
    def setUp(self):
        self.product = Product.objects.create(name='Polaire vacances', price='10.00')
        self.variant = Variant.objects.create(product=self.product, size='M', color='Bleu', quantity=10)

    def snapshot(self, quantity=1):
        return [{'product': self.product.pk, 'variant': self.variant.pk, 'name': self.product.name,
                 'size': 'M', 'color': 'Bleu', 'unit_amount': 1000, 'quantity': quantity}]

    def pause(self, **changes):
        now = timezone.now()
        fields = dict(name='Vacances', message='La boutique prend quelques jours de repos.',
                      starts_at=now - timedelta(minutes=1), ends_at=now + timedelta(days=3))
        fields.update(changes)
        return PurchasePause.objects.create(**fields)

    def order(self, session='buyer'):
        return reserve(session, None, self.snapshot())

    def start(self, order):
        with patch('paiement.stock_checkout.stripe.checkout.Session.create', return_value=SimpleNamespace(
                id=f'cs_{order.pk}', status='open', url='https://checkout.stripe.com/pause-test')):
            get_session(order.pk)
        order.refresh_from_db()
        return order

    def paid(self, order, **changes):
        value = SimpleNamespace(id=order.stripe_session_id or f'cs_{order.pk}',
            metadata={'order_id': str(order.pk)}, client_reference_id=str(order.pk),
            currency='eur', amount_subtotal=order.subtotal, amount_total=order.subtotal,
            status='complete', payment_status='paid')
        value.update(changes)
        return value

    def inventory(self):
        self.variant.refresh_from_db()
        return self.variant.quantity, self.variant.reserved


@override_settings(**CONFIG)
class PurchasePausePaymentTests(PauseFixtures, TestCase):
    def test_legacy_migration_preserves_only_engaged_attempts_and_existing_admissions(self):
        plain = self.order('plain')
        blank_session = self.order('blank-session')
        payload = self.order('payload')
        session = self.order('stripe-session')
        both = self.order('payload-and-session')
        existing = self.order('admitted')
        Order.objects.filter(pk=blank_session.pk).update(stripe_session_id='')
        Order.objects.filter(pk=payload.pk).update(checkout_payload={'mode': 'payment'})
        Order.objects.filter(pk=session.pk).update(stripe_session_id='cs_legacy_migration')
        Order.objects.filter(pk=both.pk).update(checkout_payload={'mode': 'payment'}, stripe_session_id='cs_both')
        Order.objects.filter(pk=existing.pk).update(checkout_payload={'mode': 'payment'})
        admission_time = timezone.now() - timedelta(minutes=1)
        preserved = PurchaseAdmission.objects.create(order=existing, admitted_at=admission_time)
        migrate = import_module('suspensions.migrations.0002_existing_payment_admissions').preserve_engaged_payments
        for _ in range(2):
            migrate(apps, SimpleNamespace(connection=connection))
        self.assertFalse(PurchaseAdmission.objects.filter(order__in=[plain, blank_session]).exists())
        self.assertEqual(PurchaseAdmission.objects.count(), 4)
        for order in (payload, session, both):
            migrated = PurchaseAdmission.objects.get(order=order)
            self.assertTrue(migrated.migrated)
            self.assertEqual(migrated.admitted_at, order.created_at)
        preserved.refresh_from_db()
        self.assertFalse(preserved.migrated)
        self.assertEqual(preserved.admitted_at, admission_time)

    def test_active_pause_refuses_every_reserve_before_any_order_or_stock_write(self):
        self.pause()
        with self.assertRaisesMessage(StockUnavailable, 'Votre panier est conservé'):
            self.order()
        self.assertEqual(self.inventory(), (10, 0))
        self.assertFalse(Order.objects.exists())
        self.assertFalse(Reservation.objects.exists())
        self.assertFalse(PurchaseAdmission.objects.exists())

    def test_reusing_a_pending_reservation_is_also_blocked_without_changing_it(self):
        order = self.order()
        self.pause()
        with self.assertRaises(StockUnavailable):
            self.order()
        order.refresh_from_db()
        self.assertEqual(order.status, 'pending')
        self.assertEqual(self.inventory(), (10, 1))
        self.assertEqual(Reservation.objects.get().state, 'held')

    def test_exact_start_blocks_and_exact_end_reopens_without_scheduled_job(self):
        start = timezone.now() + timedelta(hours=1)
        end = start + timedelta(days=3)
        self.pause(starts_at=start, ends_at=end)
        with patch('django.utils.timezone.now', return_value=start - timedelta(microseconds=1)):
            self.order('before')
        with patch('django.utils.timezone.now', return_value=start):
            with self.assertRaises(StockUnavailable):
                self.order('at-start')
        with patch('django.utils.timezone.now', return_value=end - timedelta(microseconds=1)):
            with self.assertRaises(StockUnavailable):
                self.order('just-before-end')
        with patch('django.utils.timezone.now', return_value=end):
            self.order('at-end')
        self.assertEqual(Order.objects.count(), 2)
        self.assertEqual(self.inventory(), (10, 2))

    def test_cancelled_pause_does_not_block_reservation(self):
        self.pause(stopped_at=timezone.now(), stop_kind='resume')
        self.order()
        self.assertEqual(self.inventory(), (10, 1))

    @patch('paiement.stock_checkout.stripe.checkout.Session.create')
    def test_checkout_opened_before_pause_rechecks_post_and_preserves_cart(self, create):
        self.client.post(f'/panier/ajouter/{self.product.slug}/', {'variant': self.variant.pk, 'quantity': 1})
        self.assertTrue(self.client.get('/paiement/').context['enabled'])
        cart = self.client.session['cart']
        self.pause()
        self.assertTrue(ready())
        response = self.client.post('/paiement/', follow=True)
        self.assertContains(response, 'Les commandes sont temporairement suspendues')
        self.assertContains(response, 'Reprise prévue le')
        self.assertContains(response, 'Votre panier est conservé')
        self.assertTrue(response.context['enabled'])
        self.assertEqual(self.client.session['cart'], cart)
        self.assertFalse(Order.objects.exists())
        create.assert_not_called()

    @patch('paiement.stock_checkout.stripe.checkout.Session.create')
    def test_pause_between_reserve_and_first_admission_keeps_hold_but_never_contacts_stripe(self, create):
        order = self.order()
        self.pause()
        with self.assertRaises(StockUnavailable):
            get_session(order.pk)
        create.assert_not_called()
        order.refresh_from_db()
        self.assertFalse(order.checkout_payload)
        self.assertFalse(order.stripe_session_id)
        self.assertEqual(order.status, 'pending')
        self.assertFalse(PurchaseAdmission.objects.exists())
        self.assertEqual(self.inventory(), (10, 1))

    @patch('paiement.stock_checkout.stripe.checkout.Session.create')
    def test_payload_without_prior_admission_is_not_a_bypass_during_pause(self, create):
        order = self.order()
        order.checkout_payload = {'mode': 'payment'}
        order.save(update_fields=['checkout_payload'])
        self.pause()
        with self.assertRaises(StockUnavailable):
            get_session(order.pk)
        self.assertFalse(PurchaseAdmission.objects.exists())
        self.assertEqual(self.inventory(), (10, 1))
        create.assert_not_called()

    def test_ambiguous_admitted_attempt_can_retry_identically_during_pause(self):
        order = self.order()
        with patch('paiement.stock_checkout.stripe.checkout.Session.create') as create:
            create.side_effect = [stripe.APIConnectionError('timeout'), SimpleNamespace(id='cs_recovered', status='open')]
            with self.assertRaises(stripe.APIConnectionError):
                get_session(order.pk)
            admission = PurchaseAdmission.objects.get(order=order)
            self.assertFalse(admission.migrated)
            order.refresh_from_db()
            self.assertTrue(order.checkout_payload)
            self.pause()
            get_session(order.pk)
            self.assertEqual(create.call_args_list[0], create.call_args_list[1])
        self.assertEqual(PurchaseAdmission.objects.filter(order=order).count(), 1)
        self.assertEqual(self.inventory(), (10, 1))
        order.refresh_from_db()
        self.assertEqual(order.stripe_session_id, 'cs_recovered')

    def test_existing_stripe_session_without_payload_or_admission_is_retrieved_never_expired_locally(self):
        order = self.order()
        order.stripe_session_id = 'cs_legacy'
        order.reservation_expires_at = timezone.now() - timedelta(hours=2)
        order.save(update_fields=['stripe_session_id', 'reservation_expires_at'])
        self.pause()
        with patch('paiement.stock_checkout.stripe.checkout.Session.retrieve', return_value=SimpleNamespace(
                id='cs_legacy', status='open')) as retrieve, \
             patch('paiement.stock_checkout.stripe.checkout.Session.create') as create:
            session = get_session(order.pk)
        self.assertEqual(session.id, 'cs_legacy')
        retrieve.assert_called_once()
        create.assert_not_called()
        order.refresh_from_db()
        self.assertEqual(order.status, 'pending')
        self.assertEqual(self.inventory(), (10, 1))
        self.assertFalse(PurchaseAdmission.objects.exists())

    def test_unsubmitted_expired_hold_can_expire_normally_during_pause(self):
        order = self.order()
        order.reservation_expires_at = timezone.now() - timedelta(seconds=1)
        order.save(update_fields=['reservation_expires_at'])
        self.pause()
        with patch('paiement.stock_checkout.stripe.checkout.Session.create') as create:
            self.assertIsNone(get_session(order.pk))
        create.assert_not_called()
        order.refresh_from_db()
        self.assertEqual(order.status, 'expired')
        self.assertEqual(self.inventory(), (10, 0))

    def test_payment_completed_during_pause_consumes_stock_once(self):
        order = self.start(self.order())
        self.pause()
        self.assertTrue(settle(self.paid(order)))
        self.assertTrue(settle(self.paid(order)))
        order.refresh_from_db()
        self.assertEqual(order.status, 'paid')
        self.assertEqual(order.total_paid, 1000)
        self.assertEqual(self.inventory(), (9, 0))
        self.assertEqual(Reservation.objects.get().state, 'sold')

    def test_signed_webhook_and_confirmation_remain_available_during_pause(self):
        self.client.post(f'/panier/ajouter/{self.product.slug}/', {'variant': self.variant.pk, 'quantity': 1})
        self.client.get('/paiement/')
        with patch('paiement.stock_checkout.stripe.checkout.Session.create', return_value=SimpleNamespace(
                id='cs_webhook_pause', status='open', url='https://checkout.stripe.com/test')):
            self.client.post('/paiement/')
        order = Order.objects.get()
        self.pause()
        event = json.dumps({'type': 'checkout.session.completed',
                           'data': {'object': {'id': order.stripe_session_id}}}).encode()
        stamp = str(int(time.time()))
        signature = hmac.new(b'whsec_test', stamp.encode() + b'.' + event, hashlib.sha256).hexdigest()
        with patch('paiement.views.stripe.checkout.Session.retrieve', return_value=self.paid(order)):
            for _ in range(2):
                response = self.client.post('/paiement/webhook/', event, content_type='application/json',
                                           HTTP_STRIPE_SIGNATURE=f't={stamp},v1={signature}')
                self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, 'paid')
        self.assertEqual(self.inventory(), (9, 0))
        self.assertEqual(self.client.get(f'/paiement/confirmation/{order.pk}/').status_code, 200)
        self.assertFalse(self.client.session.get('cart'))

    def test_authoritative_stripe_expiration_during_pause_releases_stock(self):
        order = self.start(self.order())
        self.pause()
        expired = self.paid(order, payment_status='unpaid', status='expired')
        self.assertTrue(settle(expired))
        self.assertTrue(settle(expired))
        order.refresh_from_db()
        self.assertEqual(order.status, 'expired')
        self.assertEqual(self.inventory(), (10, 0))
        self.assertEqual(Reservation.objects.get().state, 'released')

    def test_cart_change_can_reconcile_an_existing_session_during_pause(self):
        self.client.post(f'/panier/ajouter/{self.product.slug}/', {'variant': self.variant.pk, 'quantity': 1})
        self.client.get('/paiement/')
        with patch('paiement.stock_checkout.stripe.checkout.Session.create', return_value=SimpleNamespace(
                id='cs_change_pause', status='open', url='https://checkout.stripe.com/test')):
            self.client.post('/paiement/')
        order = Order.objects.get()
        self.pause()
        # A cart quantity change explicitly retires the existing payment before
        # updating its basket; a pause itself never performs this cancellation.
        with patch('paiement.stock_checkout.stripe.checkout.Session.retrieve', return_value=self.paid(
                order, status='open', payment_status='unpaid')), \
             patch('stripe.checkout.Session.expire', return_value=self.paid(
                 order, status='expired', payment_status='unpaid')):
            response = self.client.post('/panier/modifier/', {'key': f'variant:{self.variant.pk}', 'quantity': 2})
        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, 'expired')
        self.assertEqual(self.inventory(), (10, 0))
        self.assertEqual(self.client.session['cart'][f'variant:{self.variant.pk}']['quantity'], 2)

    def test_reserved_personal_code_is_consumed_once_after_payment_despite_pause(self):
        buyer = get_user_model().objects.create_user('vacances-buyer', email='buyer@example.test')
        verify_test_user(buyer)
        benefit = save_benefit(CustomerBenefit(recipient_email=buyer.email, label='Attention',
            kind='percent', value=15, minimum_subtotal=70), buyer, notify=False)
        order = reserve('benefit-buyer', buyer.pk, self.snapshot(7), benefit=benefit)
        with patch('paiement.stock_checkout.stripe.Coupon.create', side_effect=lambda **kw: SimpleNamespace(id=kw['id'])):
            self.start(order)
        self.pause()
        paid = self.paid(order, amount_total=5950, total_details={'amount_discount': 1050, 'amount_shipping': 0},
                         customer_details={'email': buyer.email})
        rows = {'has_more': False, 'data': [{'id': 'li_pause', 'currency': 'eur', 'quantity': 7,
            'price': {'unit_amount': 1000, 'product': {'metadata': {'order_id': str(order.pk), 'order_item': '0'}}},
            'amount_subtotal': 7000, 'amount_discount': 1050, 'amount_total': 5950}]}
        with patch('paiement.stock_checkout.stripe.checkout.Session.list_line_items', return_value=rows):
            self.assertTrue(settle(paid))
            self.assertTrue(settle(paid))
        order.refresh_from_db()
        self.assertEqual(order.benefit_redemption.state, 'used')
        self.assertEqual(order.total_paid, 5950)
        self.assertEqual(self.inventory(), (3, 0))


@override_settings(**CONFIG)
class ConcurrentPurchasePauseTests(PauseFixtures, TransactionTestCase):
    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL advisory and row locks required')
        super().setUp()

    @staticmethod
    def in_connection(action):
        close_old_connections()
        try:
            return action()
        finally:
            close_old_connections()

    def test_two_first_admin_saves_with_same_revision_have_one_winner_and_clear_conflict(self):
        actor = get_user_model().objects.create_superuser('pause-admin', email='pause-admin@example.test')
        verify_test_user(actor)
        barrier = Barrier(2)
        now = timezone.now()
        fields = dict(revision=0, name='Vacances', message='La boutique se repose.', mode='scheduled',
                      starts_at=now + timedelta(days=1), ends_at=now + timedelta(days=2))

        def attempt():
            barrier.wait(timeout=5)
            try:
                save_pause(fields, actor)
            except PauseConflict:
                return 'conflict'
            return 'saved'

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.in_connection, attempt) for _ in range(2)]
            self.assertEqual(sorted(f.result(timeout=10) for f in futures), ['conflict', 'saved'])
        self.assertEqual(PurchasePause.objects.count(), 1)
        self.assertEqual(PauseEvent.objects.count(), 1)
        self.assertEqual(PauseConfiguration.objects.get(pk=1).revision, 1)

    def test_order_holder_never_waits_behind_pause_writer_while_reserve_waits_for_same_order(self):
        """Regression: Order → shared gate → queued writer → reserve → Order."""
        order = self.order()
        order_locked, reserve_gate_locked, writer_started, admit_now = Event(), Event(), Event(), Event()
        writer_connection = {}
        real_lock = _lock

        def observed_lock(*, shared=False, **kwargs):
            result = real_lock(shared=shared, **kwargs)
            # Only reserve() performs a blocking shared lock. Admission must
            # use a nonblocking try-lock once it already owns the Order row.
            if shared and kwargs.get('wait', True):
                reserve_gate_locked.set()
            return result

        def admission():
            try:
                with transaction.atomic():
                    Order.objects.select_for_update().get(pk=order.pk)
                    order_locked.set()
                    if not admit_now.wait(10):
                        raise AssertionError('Admission was not released')
                    get_session(order.pk)
            except StockUnavailable as error:
                return str(error)
            return 'unexpected-admission'

        def writer():
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    writer_connection['pid'] = cursor.fetchone()[0]
                writer_started.set()
                real_lock()
                self.pause()

        with patch('suspensions.services._lock', side_effect=observed_lock), \
             patch('paiement.stock_checkout.stripe.checkout.Session.create') as create, \
             ThreadPoolExecutor(max_workers=3) as pool:
            admission_future = pool.submit(self.in_connection, admission)
            try:
                self.assertTrue(order_locked.wait(5))
                reserve_future = pool.submit(self.in_connection, self.order)
                self.assertTrue(reserve_gate_locked.wait(5))
                writer_future = pool.submit(self.in_connection, writer)
                self.assertTrue(writer_started.wait(5))
                # Observe the actual PostgreSQL wait queue, not a sleep-based
                # guess that could let the test accidentally miss the cycle.
                deadline = time.monotonic() + 5
                waiting = False
                while time.monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid = %s AND locktype = 'advisory' AND NOT granted)",
                                       [writer_connection['pid']])
                        waiting = cursor.fetchone()[0]
                    if waiting:
                        break
                    Event().wait(.01)
                self.assertTrue(waiting, 'Pause writer did not enter the advisory wait queue')
            finally:
                admit_now.set()
            self.assertIn('réessayez', admission_future.result(timeout=5).lower())
            self.assertEqual(reserve_future.result(timeout=5).pk, order.pk)
            writer_future.result(timeout=5)
        create.assert_not_called()
        self.assertEqual(self.inventory(), (10, 1))
        self.assertEqual(Order.objects.get(pk=order.pk).status, 'pending')
        self.assertFalse(PurchaseAdmission.objects.exists())

    def test_pause_committing_first_prevents_waiting_reservation(self):
        writer_locked, reader_started, release_writer = Event(), Event(), Event()
        real_lock = _lock

        def observed_lock(*, shared=False):
            if shared:
                reader_started.set()
            return real_lock(shared=shared)

        def writer():
            with transaction.atomic():
                real_lock()
                self.pause()
                writer_locked.set()
                if not release_writer.wait(10):
                    raise AssertionError('Writer was not released')

        def reader():
            try:
                self.order()
            except StockUnavailable:
                return 'paused'
            return 'reserved'

        with patch('suspensions.services._lock', side_effect=observed_lock), ThreadPoolExecutor(max_workers=2) as pool:
            writer_future = pool.submit(self.in_connection, writer)
            try:
                self.assertTrue(writer_locked.wait(5))
                reader_future = pool.submit(self.in_connection, reader)
                self.assertTrue(reader_started.wait(5))
                self.assertFalse(Order.objects.exists())
            finally:
                release_writer.set()
            writer_future.result(timeout=5)
            self.assertEqual(reader_future.result(timeout=5), 'paused')
        self.assertEqual(self.inventory(), (10, 0))
        self.assertFalse(Order.objects.exists())

    def test_existing_reservation_can_commit_before_pause_without_deadlock_or_cancellation(self):
        reader_locked, writer_started, release_reader = Event(), Event(), Event()
        real_lock = _lock

        def observed_lock(*, shared=False):
            result = real_lock(shared=shared)
            if shared:
                reader_locked.set()
                if not release_reader.wait(10):
                    raise AssertionError('Reader was not released')
            return result

        def writer():
            writer_started.set()
            with transaction.atomic():
                real_lock()
                self.pause()

        with patch('suspensions.services._lock', side_effect=observed_lock), ThreadPoolExecutor(max_workers=2) as pool:
            reader_future = pool.submit(self.in_connection, self.order)
            try:
                self.assertTrue(reader_locked.wait(5))
                writer_future = pool.submit(self.in_connection, writer)
                self.assertTrue(writer_started.wait(5))
            finally:
                release_reader.set()
            order = reader_future.result(timeout=5)
            writer_future.result(timeout=5)
        self.assertEqual(self.inventory(), (10, 1))
        self.assertEqual(Order.objects.get(pk=order.pk).status, 'pending')
        with patch('paiement.stock_checkout.stripe.checkout.Session.create') as create:
            with self.assertRaises(StockUnavailable):
                get_session(order.pk)
        create.assert_not_called()

    def test_pause_management_does_not_wait_for_an_already_admitted_stripe_network_call(self):
        order = self.order()
        network_started, release_network = Event(), Event()

        def stripe_create(**kwargs):
            network_started.set()
            if not release_network.wait(10):
                raise AssertionError('Network was not released')
            return SimpleNamespace(id='cs_before_pause', status='open')

        def writer():
            with transaction.atomic():
                _lock()
                self.pause()

        with patch('paiement.stock_checkout.stripe.checkout.Session.create', side_effect=stripe_create), \
             ThreadPoolExecutor(max_workers=2) as pool:
            payment_future = pool.submit(self.in_connection, lambda: get_session(order.pk))
            try:
                self.assertTrue(network_started.wait(5))
                self.assertEqual(PurchaseAdmission.objects.filter(order=order).count(), 1)
                writer_future = pool.submit(self.in_connection, writer)
                # The seller must be able to pause immediately, even if Stripe
                # is slow; the durable admission is already committed.
                writer_future.result(timeout=5)
                self.assertTrue(PurchasePause.objects.exists())
            finally:
                release_network.set()
            self.assertEqual(payment_future.result(timeout=5).id, 'cs_before_pause')
        self.assertEqual(self.inventory(), (10, 1))
        with self.assertRaises(StockUnavailable):
            self.order('later-buyer')
