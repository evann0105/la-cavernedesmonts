from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
import json
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, transaction
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from core.models import Product
from stocks.models import Reservation, Variant
from stocks.reservations import StockUnavailable, finish_locked, reserve
from .anti_abuse import CheckoutLimited, WINDOW, purge_old_admissions
from .models import CheckoutAdmission, Order
from .tests import CONFIG, SimpleNamespace


class AbuseFixtures:
    def setUp(self):
        self.now = timezone.now()
        self.product = Product.objects.create(name='Polaire protection', slug='polaire-protection', price='10.00')
        self.variants = [Variant.objects.create(product=self.product, size=size, color='Bleu', quantity=100)
                         for size in ('S', 'M', 'L', 'XL')]

    def snapshot(self, index=0, quantity=1):
        variant = self.variants[index]
        return [{'variant': variant.pk, 'product': self.product.pk, 'name': self.product.name,
                 'size': variant.size, 'color': variant.color, 'unit_amount': 1000, 'quantity': quantity}]

    def hold(self, session, *, ip='203.0.113.8', user=None, index=0, quantity=1, at=None):
        with patch('paiement.anti_abuse.timezone.now', return_value=at or self.now):
            return reserve(session, user.pk if user else None, self.snapshot(index, quantity), client_ip=ip)

    def finish(self, order, *, sold=False):
        with transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=order.pk)
            finish_locked(locked, sold)
            locked.status = 'paid' if sold else 'expired'
            locked.save()


class CheckoutAdmissionTests(AbuseFixtures, TestCase):
    def test_new_browsers_cannot_reserve_the_same_variant_again_from_one_ip(self):
        self.variants[0].quantity = 20
        self.variants[0].save()
        self.hold('first-browser', quantity=10)
        # Beyond the spacing window: the overlapping-stock guard must also work.
        with self.assertRaises(CheckoutLimited):
            self.hold('second-browser', quantity=10, at=self.now + timedelta(seconds=31))
        self.variants[0].refresh_from_db()
        self.assertEqual((self.variants[0].quantity, self.variants[0].reserved, self.variants[0].available), (20, 10, 10))
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(Reservation.objects.count(), 1)
        self.assertEqual(CheckoutAdmission.objects.count(), 1)
        self.hold('independent-buyer', ip='203.0.113.9', quantity=10)
        self.assertEqual(Order.objects.count(), 2)

    def test_shared_network_allows_three_different_baskets_then_bounds_pending_holds(self):
        for index in range(3):
            self.hold(f'family-{index}', index=index, at=self.now + timedelta(seconds=31 * index))
        with self.assertRaises(CheckoutLimited):
            self.hold('fourth-browser', index=3, at=self.now + timedelta(seconds=93))
        self.assertEqual(Order.objects.count(), 3)

    def test_spacing_limits_even_a_different_basket_and_returns_remaining_seconds(self):
        self.hold('first')
        with self.assertRaises(CheckoutLimited) as caught:
            self.hold('second', index=1, at=self.now + timedelta(seconds=10))
        self.assertEqual(caught.exception.retry_after, 20)
        self.assertEqual(CheckoutAdmission.objects.count(), 1)

    def test_same_account_cannot_bypass_pending_limit_by_changing_ip_and_session(self):
        user = get_user_model().objects.create_user('protected-buyer', email='buyer@example.test')
        self.hold('one', user=user)
        with self.assertRaises(CheckoutLimited):
            self.hold('two', user=user, ip='203.0.113.9', index=1)
        self.assertEqual(Order.objects.count(), 1)

    def test_same_session_cannot_bypass_pending_limit_by_changing_ip(self):
        self.hold('same-browser')
        with self.assertRaises(CheckoutLimited):
            self.hold('same-browser', ip='203.0.113.9', index=1)

    def test_identical_reservation_retry_is_not_throttled_or_duplicated(self):
        order = self.hold('retry-browser')
        for _ in range(3):
            self.assertEqual(self.hold('retry-browser').pk, order.pk)
        self.assertEqual(CheckoutAdmission.objects.count(), 1)
        self.assertEqual(Reservation.objects.get().quantity, 1)

    def test_same_browser_can_replace_a_safely_finished_checkout_without_waiting(self):
        first = self.hold('same-browser')
        self.finish(first)
        second = self.hold('same-browser')
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(CheckoutAdmission.objects.count(), 2)

    def test_ip_creation_budget_survives_new_sessions_and_completed_holds(self):
        for index in range(10):
            order = self.hold(f'new-browser-{index}', at=self.now + timedelta(seconds=31 * index))
            self.finish(order)
        with self.assertRaises(CheckoutLimited):
            self.hold('eleventh-browser', at=self.now + timedelta(seconds=310))
        self.assertEqual(Order.objects.count(), 10)
        self.assertEqual(CheckoutAdmission.objects.count(), 10)

    def test_session_creation_budget_survives_changing_ip_and_releasing_stock(self):
        for index in range(5):
            order = self.hold('same-browser', ip=f'203.0.113.{index + 1}')
            self.finish(order)
        with self.assertRaises(CheckoutLimited):
            self.hold('same-browser', ip='203.0.113.20')

    def test_account_creation_budget_survives_changing_ip_and_session(self):
        user = get_user_model().objects.create_user('buyer-budget', email='budget@example.test')
        for index in range(5):
            order = self.hold(f'new-session-{index}', user=user, ip=f'203.0.113.{index + 1}')
            self.finish(order, sold=True)
        with self.assertRaises(CheckoutLimited):
            self.hold('sixth-session', user=user, ip='203.0.113.20')

    def test_rolling_window_end_reopens_but_does_not_delete_old_evidence_on_request(self):
        for index in range(5):
            self.finish(self.hold('same-browser', ip=f'203.0.113.{index + 1}'))
        self.hold('same-browser', ip='203.0.113.20', at=self.now + WINDOW)
        self.assertEqual(CheckoutAdmission.objects.count(), 6)

    def test_clock_expiry_does_not_reset_an_unresolved_hold(self):
        order = self.hold('first')
        Order.objects.filter(pk=order.pk).update(reservation_expires_at=self.now - timedelta(seconds=1))
        with self.assertRaises(CheckoutLimited) as caught:
            self.hold('second', at=self.now + timedelta(hours=2))
        self.assertEqual(caught.exception.retry_after, 60)
        self.variants[0].refresh_from_db()
        self.assertEqual(self.variants[0].reserved, 1)

    def test_legacy_untracked_hold_still_counts_for_its_session_or_account(self):
        user = get_user_model().objects.create_user('legacy-buyer', email='legacy@example.test')
        reserve('legacy-session', user.pk, self.snapshot())
        with self.assertRaises(CheckoutLimited):
            self.hold('new-session', user=user, index=1)
        with self.assertRaises(CheckoutLimited):
            self.hold('legacy-session', index=1)
        self.assertEqual(CheckoutAdmission.objects.count(), 0)

    def test_invalid_stock_rolls_back_admission_and_order_together(self):
        with self.assertRaises(StockUnavailable):
            self.hold('invalid-quantity', quantity=11)
        self.assertFalse(Order.objects.exists())
        self.assertFalse(CheckoutAdmission.objects.exists())
        self.assertFalse(Reservation.objects.exists())

    def test_stored_keys_do_not_contain_raw_ip_session_or_account(self):
        user = get_user_model().objects.create_user('private-buyer', email='private@example.test')
        self.hold('private-session-secret', user=user)
        row = CheckoutAdmission.objects.values('ip_hash', 'session_hash', 'account_hash').get()
        for value in row.values():
            self.assertRegex(value, r'^[0-9a-f]{64}$')
            self.assertNotIn('203.0.113.8', value)
            self.assertNotIn('private-session-secret', value)
        self.assertEqual(len(set(row.values())), 3)

    def test_ipv6_spellings_share_one_bucket(self):
        self.hold('one', ip='2001:db8::1')
        with self.assertRaises(CheckoutLimited):
            self.hold('two', ip='2001:0db8:0000:0000:0000:0000:0000:0001', index=1)

    def test_rotating_ipv6_within_the_same_64_does_not_reset_the_budget(self):
        self.hold('one', ip='2001:db8:1234:5678::1')
        with self.assertRaises(CheckoutLimited):
            self.hold('two', ip='2001:db8:1234:5678:ffff:ffff:ffff:ffff', index=1)
        self.hold('independent-network', ip='2001:db8:1234:5679::1', index=1)
        self.assertEqual(CheckoutAdmission.objects.count(), 2)

    def test_ipv4_mapped_ipv6_and_ipv4_share_one_bucket(self):
        self.hold('one', ip='203.0.113.8')
        with self.assertRaises(CheckoutLimited):
            self.hold('two', ip='::ffff:203.0.113.8', index=1)

    def test_missing_or_invalid_remote_ip_is_not_an_exemption(self):
        self.hold('one', ip='')
        with self.assertRaises(CheckoutLimited):
            self.hold('two', ip='not-an-ip', index=1)

    def test_cleanup_is_bounded_keeps_pending_holds_and_recent_security_evidence(self):
        orders = [self.hold(f'cleanup-{index}', ip=f'203.0.113.{index + 1}') for index in range(4)]
        for order in orders[:3]:
            self.finish(order)
        CheckoutAdmission.objects.filter(order__in=orders).update(created_at=self.now - timedelta(days=2))
        recent = self.hold('recent', ip='203.0.113.20', index=1)
        self.assertEqual(purge_old_admissions(batch_size=2), 2)
        self.assertEqual(CheckoutAdmission.objects.count(), 3)
        self.assertEqual(purge_old_admissions(), 1)
        self.assertTrue(CheckoutAdmission.objects.filter(order=orders[-1]).exists())
        self.assertTrue(CheckoutAdmission.objects.filter(order=recent).exists())
        self.variants[0].refresh_from_db()
        self.assertEqual(self.variants[0].reserved, 1)


class CheckoutBasketBoundsTests(AbuseFixtures, TestCase):
    def large_snapshot(self, count=21):
        rows = []
        for index in range(count):
            variant = Variant.objects.create(product=self.product, size=f'Audit {index}', color='Vert', quantity=10)
            rows.append({'variant': variant.pk, 'product': self.product.pk, 'name': self.product.name,
                         'size': variant.size, 'color': variant.color, 'unit_amount': 1000, 'quantity': 1})
        return rows

    def test_more_than_twenty_lines_is_rejected_before_creating_an_order(self):
        rows = self.large_snapshot()
        with patch('stocks.reservations.Order.objects.get_or_create') as create:
            with self.assertRaisesMessage(StockUnavailable, '20 tailles et couleurs différentes'):
                reserve('large-cart', None, rows, client_ip='203.0.113.8')
        create.assert_not_called()
        self.assertFalse(Order.objects.exists())
        self.assertFalse(CheckoutAdmission.objects.exists())
        self.assertFalse(Reservation.objects.exists())
        self.assertFalse(Variant.objects.exclude(reserved=0).exists())

    def test_more_than_thirty_units_is_rejected_before_creating_an_order(self):
        rows = self.snapshot(0, 10) + self.snapshot(1, 10) + self.snapshot(2, 10) + self.snapshot(3, 1)
        with patch('stocks.reservations.Order.objects.get_or_create') as create:
            with self.assertRaisesMessage(StockUnavailable, '30 articles au total'):
                reserve('large-cart', None, rows, client_ip='203.0.113.8')
        create.assert_not_called()
        self.assertFalse(Order.objects.exists())
        self.assertFalse(CheckoutAdmission.objects.exists())
        self.assertFalse(Reservation.objects.exists())

    def test_exact_twenty_lines_thirty_units_are_admitted(self):
        rows = self.large_snapshot(20)
        for row in rows[:10]:
            row['quantity'] = 2
        order = reserve('bounded-family-cart', None, rows, client_ip='203.0.113.8')
        self.assertEqual(order.reservations.count(), 20)
        self.assertEqual(sum(order.reservations.values_list('quantity', flat=True)), 30)

    def test_historical_identical_large_reservation_remains_resumable(self):
        rows = self.large_snapshot()
        fingerprint = hashlib.sha256(json.dumps(['historical-browser', None, rows], sort_keys=True).encode()).hexdigest()
        historical = Order.objects.create(session_key='historical-browser', items=rows,
                                          subtotal=21000, checkout_fingerprint=fingerprint)
        self.assertEqual(reserve('historical-browser', None, rows, client_ip='203.0.113.8').pk, historical.pk)
        self.assertEqual(Order.objects.count(), 1)
        self.assertFalse(CheckoutAdmission.objects.exists())


@override_settings(**CONFIG)
class CheckoutAbuseHttpTests(AbuseFixtures, TestCase):
    def buyer(self, *, ip='203.0.113.8', index=0, quantity=1, forwarded='198.51.100.1'):
        buyer = Client(enforce_csrf_checks=True, REMOTE_ADDR=ip, HTTP_X_FORWARDED_FOR=forwarded)
        response = buyer.get(f'/produits/{self.product.slug}/')
        self.assertEqual(response.status_code, 200)
        token = buyer.cookies['csrftoken'].value
        self.assertEqual(buyer.post(f'/panier/ajouter/{self.product.slug}/',
            {'variant': self.variants[index].pk, 'quantity': quantity}, HTTP_X_CSRFTOKEN=token).status_code, 302)
        self.assertEqual(buyer.get('/paiement/').status_code, 200)
        return buyer, token

    def test_403_without_csrf_does_not_write_admission(self):
        buyer, _ = self.buyer()
        self.assertEqual(buyer.post('/paiement/').status_code, 403)
        self.assertFalse(CheckoutAdmission.objects.exists())

    def test_browsers_and_forged_forwarded_ip_do_not_bypass_429_without_side_effects(self):
        first, first_token = self.buyer(quantity=10)
        second, second_token = self.buyer(quantity=10, forwarded='198.51.100.99')
        original_cart = second.session['cart']
        with patch('paiement.stock_checkout.stripe.checkout.Session.create', return_value=SimpleNamespace(
                id='cs_abuse_first', status='open', url='https://checkout.stripe.com/test')) as create:
            self.assertEqual(first.post('/paiement/', HTTP_X_CSRFTOKEN=first_token).status_code, 302)
            response = second.post('/paiement/', HTTP_X_CSRFTOKEN=second_token)
        self.assertEqual(response.status_code, 429)
        self.assertTrue(1 <= int(response['Retry-After']) <= 30)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')
        self.assertContains(response, 'Votre panier est conservé', status_code=429)
        self.assertNotContains(response, '203.0.113.8', status_code=429)
        self.assertNotContains(response, 'checkout-pay"', status_code=429)
        self.assertEqual(create.call_count, 1)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(CheckoutAdmission.objects.count(), 1)
        self.assertEqual(second.session['cart'], original_cart)
        self.variants[0].refresh_from_db()
        self.assertEqual(self.variants[0].reserved, 10)

    def test_explicit_none_remote_address_uses_the_strict_unknown_bucket(self):
        first, token = self.buyer(ip=None)
        second, second_token = self.buyer(ip='')
        with patch('paiement.stock_checkout.stripe.checkout.Session.create', return_value=SimpleNamespace(
                id='cs_unknown_ip', status='open', url='https://checkout.stripe.com/test')) as create:
            self.assertEqual(first.post('/paiement/', HTTP_X_CSRFTOKEN=token).status_code, 302)
            response = second.post('/paiement/', HTTP_X_CSRFTOKEN=second_token)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(CheckoutAdmission.objects.count(), 1)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(create.call_count, 1)

    def test_open_payment_is_resumed_during_rate_limit_without_a_second_stripe_creation(self):
        buyer, token = self.buyer()
        session = SimpleNamespace(id='cs_existing', status='open', url='https://checkout.stripe.com/test')
        with patch('paiement.stock_checkout.stripe.checkout.Session.create', return_value=session) as create:
            self.assertEqual(buyer.post('/paiement/', HTTP_X_CSRFTOKEN=token).status_code, 302)
            with patch('paiement.stock_checkout.stripe.checkout.Session.retrieve', return_value=session) as retrieve:
                self.assertEqual(buyer.post('/paiement/', HTTP_X_CSRFTOKEN=token).status_code, 302)
        self.assertEqual(create.call_count, 1)
        self.assertEqual(retrieve.call_count, 1)
        self.assertEqual(CheckoutAdmission.objects.count(), 1)

    def test_paid_order_cart_reconciliation_is_not_subject_to_admission_quota(self):
        buyer, token = self.buyer()
        with patch('paiement.stock_checkout.stripe.checkout.Session.create', return_value=SimpleNamespace(
                id='cs_paid', status='open', url='https://checkout.stripe.com/test')):
            buyer.post('/paiement/', HTTP_X_CSRFTOKEN=token)
        order = Order.objects.get()
        self.finish(order, sold=True)
        response = buyer.post('/paiement/', HTTP_X_CSRFTOKEN=token)
        self.assertRedirects(response, f'/paiement/confirmation/{order.pk}/', fetch_redirect_response=False)
        self.assertFalse(buyer.session['cart'])
        self.assertEqual(CheckoutAdmission.objects.count(), 1)

    def test_large_basket_is_a_clear_structural_error_not_a_temporary_429(self):
        buyer, token = self.buyer(quantity=10)
        for index, quantity in ((1, 10), (2, 10), (3, 1)):
            buyer.post(f'/panier/ajouter/{self.product.slug}/',
                       {'variant': self.variants[index].pk, 'quantity': quantity}, HTTP_X_CSRFTOKEN=token)
        buyer.get('/paiement/')
        with patch('paiement.stock_checkout.stripe.checkout.Session.create') as create:
            response = buyer.post('/paiement/', HTTP_X_CSRFTOKEN=token, follow=True)
        self.assertContains(response, '30 articles au total')
        self.assertContains(response, 'contactez la boutique')
        self.assertNotIn('Retry-After', response)
        create.assert_not_called()
        self.assertFalse(Order.objects.exists())
        self.assertFalse(CheckoutAdmission.objects.exists())
        self.assertFalse(Reservation.objects.exists())


class CheckoutAbuseConcurrencyTests(AbuseFixtures, TransactionTestCase):
    def concurrent(self, attempts):
        if connection.vendor != 'postgresql':
            self.skipTest('Verrous de transactions PostgreSQL nécessaires.')
        barrier = Barrier(len(attempts))
        def run(attempt):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                reserve(attempt['session'], attempt.get('user_id'), self.snapshot(attempt.get('index', 0)),
                        client_ip=attempt.get('ip', '203.0.113.8'))
                return 'admitted'
            except CheckoutLimited:
                return 'limited'
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(attempts)) as pool:
            return list(pool.map(run, attempts))

    def test_parallel_new_sessions_on_same_ip_admit_only_one_and_roll_back_the_other(self):
        self.assertEqual(sorted(self.concurrent([{'session': 'one'}, {'session': 'two'}])), ['admitted', 'limited'])
        self.assertEqual(CheckoutAdmission.objects.count(), 1)
        self.assertEqual(Order.objects.count(), 1)
        self.variants[0].refresh_from_db()
        self.assertEqual(self.variants[0].reserved, 1)

    def test_parallel_identical_retry_returns_the_one_existing_reservation(self):
        self.assertEqual(self.concurrent([{'session': 'same'}, {'session': 'same'}]), ['admitted', 'admitted'])
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(CheckoutAdmission.objects.count(), 1)
        self.assertEqual(Reservation.objects.count(), 1)

    def test_parallel_ips_of_same_account_share_the_pending_budget(self):
        user = get_user_model().objects.create_user('parallel-buyer', email='parallel@example.test')
        result = self.concurrent([{'session': 'one', 'ip': '203.0.113.1', 'user_id': user.pk},
                                  {'session': 'two', 'ip': '203.0.113.2', 'user_id': user.pk, 'index': 1}])
        self.assertEqual(sorted(result), ['admitted', 'limited'])
        self.assertEqual(CheckoutAdmission.objects.count(), 1)
        self.assertEqual(Order.objects.count(), 1)
