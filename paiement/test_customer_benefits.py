"""Checkout endpoints and payment reconciliation; all Stripe calls are simulated."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import stripe
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from avoirs.models import BenefitRedemption, CustomerBenefit
from avoirs.services import BenefitError, save_benefit
from connexion.models import EmailVerification
from connexion.test_helpers import verify_test_user
from core.models import Product
from stocks.models import Variant
from stocks.reservations import StockUnavailable, reserve
from .models import Order
from .stock_checkout import get_session, settle
from .tests import CONFIG, SimpleNamespace


@override_settings(**CONFIG)
class CustomerBenefitCheckoutTests(TestCase):
    def setUp(self):
        self.customer = get_user_model().objects.create_user('discount-buyer', email='beneficiary@example.test')
        verify_test_user(self.customer)
        self.client.force_login(self.customer, backend='connexion.backends.EmailOrUsernameBackend')
        self.product = Product.objects.create(name='Polaire test remise', price='35', availability='available')
        self.variant = Variant.objects.create(product=self.product, size='M', color='Vert', quantity=10)
        self.client.post(reverse('core:cart_add', args=[self.product.slug]), {'variant': self.variant.pk, 'quantity': 2})
        self.benefit = self.grant()
        self.checkout = reverse('paiement:checkout')

    def grant(self, **changes):
        fields = dict(recipient_email=self.customer.email, label='Une attention de la boutique', kind='percent', value=15, minimum_subtotal=70)
        fields.update(changes)
        return save_benefit(CustomerBenefit(**fields), self.customer, notify=False)

    def apply(self, benefit=None, client=None):
        return (client or self.client).post(self.checkout, {'action': 'apply_benefit', 'benefit_code': (benefit or self.benefit).code.lower()}, follow=True)

    def start(self):
        with patch('paiement.stock_checkout.stripe.Coupon.create', side_effect=lambda **params: SimpleNamespace(id=params['id'])) as coupon, \
             patch('paiement.stock_checkout.stripe.checkout.Session.create', return_value=SimpleNamespace(
                 id=f'cs_benefit_{Order.objects.count() + 1}', status='open', url='https://checkout.stripe.com/benefit-test')) as create:
            response = self.client.post(self.checkout)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, 'https://checkout.stripe.com/benefit-test')
        order = Order.objects.get(pk=self.client.session['checkout_order'])
        return order, coupon, create

    def paid(self, order, discount=None, shipping=600, **changes):
        if discount is None:
            discount = order.benefit_redemption.discount_amount
        data = SimpleNamespace(id=order.stripe_session_id, client_reference_id=str(order.pk), metadata={'order_id': str(order.pk)},
            currency='eur', status='complete', payment_status='paid', amount_subtotal=order.subtotal,
            amount_total=order.subtotal - discount + shipping, total_details={'amount_shipping': shipping, 'amount_discount': discount},
            consent={'terms_of_service': 'accepted'}, customer_details={'email': self.customer.email})
        data.update(changes)
        return data

    def rows(self, order, discount=None):
        discount = order.benefit_redemption.discount_amount if discount is None else discount
        return {'has_more': False, 'data': [{'id': 'li_benefit_test', 'currency': 'eur', 'quantity': 2,
            'price': {'unit_amount': 3500, 'product': {'metadata': {'order_id': str(order.pk), 'order_item': '0'}}},
            'amount_subtotal': 7000, 'amount_discount': discount, 'amount_total': 7000 - discount}]}

    def expired(self, order):
        return SimpleNamespace(id=order.stripe_session_id, status='expired', payment_status='unpaid',
            client_reference_id=str(order.pk), metadata={'order_id': str(order.pk)})

    def test_fifteen_percent_at_exact_threshold_is_displayed_and_sent_as_frozen_discount(self):
        response = self.client.post(self.checkout, {
            'action': 'apply_benefit', 'benefit_code': self.benefit.code,
            'return_to': 'cart',
        }, follow=True)
        self.assertTemplateUsed(response, 'core/cart.html')
        self.assertContains(response, '10,50 €')
        self.assertContains(response, '59,50 €')
        self.assertFalse(BenefitRedemption.objects.exists())
        self.assertContains(self.client.get(self.checkout), '59,50 €')
        order, coupon, create = self.start()
        self.assertEqual(coupon.call_args.kwargs['amount_off'], 1050)
        self.assertEqual(coupon.call_args.kwargs['max_redemptions'], 1)
        params = create.call_args.kwargs
        self.assertEqual(params['discounts'], [{'coupon': f'customer-benefit-{order.pk.hex}'}])
        self.assertNotIn('allow_promotion_codes', params)
        self.assertEqual(params['customer_email'], self.customer.email)
        self.assertEqual(params['line_items'][0]['price_data']['unit_amount'], 3500)
        self.assertNotIn('customer_email', order.checkout_payload)
        self.assertNotIn(self.customer.email, str(order.benefit_redemption.snapshot))
        self.assertEqual(order.benefit_redemption.state, 'held')
        self.variant.refresh_from_db()
        self.assertEqual((self.variant.quantity, self.variant.reserved), (10, 2))

    def test_invalid_recipient_guest_and_unverified_account_cannot_apply(self):
        other = get_user_model().objects.create_user('other-discount-buyer', email='other@example.test')
        verify_test_user(other)
        foreign = self.grant(recipient_email=other.email)
        response = self.apply(foreign)
        self.assertContains(response, 'réservé à son destinataire')
        self.assertNotIn('checkout_customer_benefit', self.client.session)
        EmailVerification.objects.filter(user=self.customer).delete()
        with override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True):
            self.assertContains(self.apply(), 'Confirmez votre adresse')
        self.assertNotIn('checkout_customer_benefit', self.client.session)
        self.client.logout()
        self.client.post(reverse('core:cart_add', args=[self.product.slug]), {'variant': self.variant.pk, 'quantity': 2})
        self.assertContains(self.apply(), 'Connectez-vous')
        self.assertFalse(BenefitRedemption.objects.exists())

    def test_insufficient_basket_and_dates_reject_without_reserving_stock(self):
        for fields in ({'minimum_subtotal': 71}, {'starts_at': timezone.now() + timedelta(days=1)},
                       {'ends_at': timezone.now() - timedelta(seconds=1)}, {'active': False}):
            with self.subTest(fields=fields):
                self.apply(self.grant(**fields))
                self.assertNotIn('checkout_customer_benefit', self.client.session)
                self.assertFalse(Order.objects.exists())

    def test_seller_edit_after_preview_requires_customer_to_review_new_quote(self):
        self.apply()
        self.benefit.value = 20
        save_benefit(self.benefit, self.customer, notify=False)
        with patch('paiement.stock_checkout.stripe.checkout.Session.create') as create:
            response = self.client.post(self.checkout, follow=True)
        self.assertContains(response, 'conditions de votre code ont changé')
        self.assertContains(response, '14,00 €')
        create.assert_not_called()
        self.assertFalse(Order.objects.exists())

    def test_race_between_quote_and_locked_reservation_rolls_back_everything(self):
        self.apply()
        original_reserve = reserve
        def changed_conditions(*args, **kwargs):
            benefit = CustomerBenefit.objects.get(pk=self.benefit.pk)
            benefit.value = 10
            save_benefit(benefit, self.customer, notify=False)
            return original_reserve(*args, **kwargs)
        with patch('stocks.reservations.reserve', side_effect=changed_conditions), \
             patch('paiement.stock_checkout.stripe.checkout.Session.create') as create:
            self.client.post(self.checkout)
        self.assertFalse(Order.objects.exists())
        self.assertFalse(BenefitRedemption.objects.exists())
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.reserved, 0)
        create.assert_not_called()

    def test_paid_callback_consumes_stock_and_code_once_with_net_invoice_totals(self):
        self.apply()
        order, _, _ = self.start()
        with patch('paiement.stock_checkout.stripe.checkout.Session.list_line_items', return_value=self.rows(order)) as listed:
            self.assertTrue(settle(self.paid(order)))
            self.assertTrue(settle(self.paid(order)))
            self.assertEqual(listed.call_count, 1)
        order.refresh_from_db()
        self.assertEqual(order.status, 'paid')
        self.assertEqual(order.total_paid, 6550)
        self.assertEqual(order.paid_line_totals, [5950])
        self.assertEqual(order.benefit_redemption.state, 'used')
        self.variant.refresh_from_db()
        self.assertEqual((self.variant.quantity, self.variant.reserved), (8, 0))
        from facturation.services import build_preview
        preview = build_preview(order)
        self.assertEqual(preview['lines'][0]['net'], Decimal('59.50'))
        self.assertEqual(preview['discount'], Decimal('10.50'))
        self.client.get(reverse('paiement:confirmation', args=[order.pk]))
        self.assertNotIn('checkout_customer_benefit', self.client.session)
        self.assertFalse(self.client.session.get('cart'))
        self.assertContains(self.client.get(reverse('espace:credits')), 'Utilisé')

    def test_unpaid_session_keeps_stock_and_code_reserved(self):
        self.apply()
        order, _, _ = self.start()
        self.assertTrue(settle(self.paid(order, payment_status='unpaid', status='open')))
        order.refresh_from_db()
        self.assertEqual(order.status, 'pending')
        self.assertEqual(order.benefit_redemption.state, 'held')
        self.variant.refresh_from_db()
        self.assertEqual((self.variant.quantity, self.variant.reserved), (10, 2))

    def test_forged_discount_rolls_back_stock_and_keeps_code(self):
        self.apply()
        order, _, _ = self.start()
        with patch('paiement.stock_checkout.stripe.checkout.Session.list_line_items', return_value=self.rows(order, discount=1000)):
            with self.assertRaises(BenefitError):
                settle(self.paid(order, discount=1000))
        order.refresh_from_db()
        self.assertEqual(order.status, 'pending')
        self.assertEqual(order.benefit_redemption.state, 'held')
        self.variant.refresh_from_db()
        self.assertEqual((self.variant.quantity, self.variant.reserved), (10, 2))

    def test_confirmed_expiration_releases_both_and_allows_fresh_order(self):
        self.apply()
        order, _, _ = self.start()
        self.assertTrue(settle(self.expired(order)))
        self.assertEqual(BenefitRedemption.objects.get(order=order).state, 'released')
        self.variant.refresh_from_db()
        self.assertEqual((self.variant.quantity, self.variant.reserved), (10, 0))
        self.client.get(self.checkout)
        second, _, _ = self.start()
        self.assertNotEqual(order.pk, second.pk)
        self.assertEqual(second.benefit_redemption.state, 'held')

    def test_remove_cancels_provider_session_before_releasing_code(self):
        self.apply()
        order, _, _ = self.start()
        with patch('paiement.stock_checkout.stripe.checkout.Session.retrieve', return_value=SimpleNamespace(
                id=order.stripe_session_id, status='open')), \
             patch('paiement.stock_checkout.stripe.checkout.Session.expire', return_value=self.expired(order)) as expire:
            response = self.client.post(self.checkout, {'action': 'remove_benefit'}, follow=True)
        self.assertContains(response, 'Le code a été retiré')
        expire.assert_called_once()
        self.assertNotIn('checkout_customer_benefit', self.client.session)
        self.assertEqual(BenefitRedemption.objects.get(order=order).state, 'released')
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.reserved, 0)
        full_price, coupon, create = self.start()
        coupon.assert_not_called()
        self.assertNotIn('discounts', create.call_args.kwargs)
        self.assertEqual(full_price.subtotal, 7000)

    def test_ambiguous_cancellation_keeps_existing_code_and_stock(self):
        self.apply()
        order, _, _ = self.start()
        with patch('paiement.stock_checkout.stripe.checkout.Session.retrieve', side_effect=stripe.APIConnectionError('test')):
            response = self.client.post(self.checkout, {'action': 'remove_benefit'}, follow=True)
        self.assertContains(response, 'doit être vérifié avant de changer de code')
        self.assertEqual(self.client.session['checkout_customer_benefit'], self.benefit.pk)
        self.assertEqual(BenefitRedemption.objects.get(order=order).state, 'held')
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.reserved, 2)

    def test_payment_winning_race_with_removal_keeps_paid_order(self):
        self.apply()
        order, _, _ = self.start()
        with patch('paiement.stock_checkout.stripe.checkout.Session.retrieve', return_value=SimpleNamespace(id=order.stripe_session_id, status='open')), \
             patch('paiement.stock_checkout.stripe.checkout.Session.expire', return_value=self.paid(order)), \
             patch('paiement.stock_checkout.stripe.checkout.Session.list_line_items', return_value=self.rows(order)):
            response = self.client.post(self.checkout, {'action': 'remove_benefit'})
        self.assertRedirects(response, reverse('paiement:confirmation', args=[order.pk]))
        self.assertEqual(BenefitRedemption.objects.get(order=order).state, 'used')
        self.assertEqual(Order.objects.count(), 1)
        self.assertFalse(self.client.session.get('cart'))

    def test_coupon_creation_timeout_preserves_durable_attempt_and_reuses_key(self):
        self.apply()
        with patch('paiement.stock_checkout.stripe.Coupon.create', side_effect=stripe.APIConnectionError('test')) as first, \
             patch('paiement.stock_checkout.stripe.checkout.Session.create') as create:
            self.client.post(self.checkout)
        create.assert_not_called()
        order = Order.objects.get()
        self.assertEqual(order.benefit_redemption.state, 'held')
        second, retry, _ = self.start()
        self.assertEqual(second.pk, order.pk)
        self.assertEqual(first.call_args.kwargs, retry.call_args.kwargs)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.reserved, 2)

    def test_second_browser_same_account_cannot_reserve_same_code(self):
        self.apply()
        order, _, _ = self.start()
        second = Client()
        second.force_login(self.customer, backend='connexion.backends.EmailOrUsernameBackend')
        second.post(reverse('core:cart_add', args=[self.product.slug]), {'variant': self.variant.pk, 'quantity': 2})
        self.assertContains(self.apply(client=second), 'réservé à un paiement en cours')
        self.assertNotIn('checkout_customer_benefit', second.session)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(BenefitRedemption.objects.get(order=order).state, 'held')

    def test_resaving_unchanged_offer_does_not_break_existing_payment_retry(self):
        self.apply()
        order, _, _ = self.start()
        self.benefit.refresh_from_db()
        save_benefit(self.benefit, self.customer, notify=False)
        self.client.get(self.checkout)
        with patch('paiement.stock_checkout.stripe.checkout.Session.retrieve', return_value=SimpleNamespace(
                id=order.stripe_session_id, status='open', url='https://checkout.stripe.com/benefit-test')), \
             patch('paiement.stock_checkout.stripe.checkout.Session.create') as create:
            response = self.client.post(self.checkout)
        self.assertEqual(response.url, 'https://checkout.stripe.com/benefit-test')
        create.assert_not_called()
        self.assertEqual(Order.objects.count(), 1)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.reserved, 2)

    def test_fully_discounted_order_confirms_without_payment_and_consumes_code(self):
        self.benefit = self.grant(kind='credit', value=70, minimum_subtotal=0)
        self.apply()
        order, _, _ = self.start()
        with patch('paiement.stock_checkout.stripe.checkout.Session.list_line_items', return_value=self.rows(order)):
            self.assertTrue(settle(self.paid(order, shipping=0, payment_status='no_payment_required')))
        order.refresh_from_db()
        self.assertEqual(order.total_paid, 0)
        self.assertEqual(order.status, 'paid')
        self.assertEqual(order.benefit_redemption.state, 'used')

    def test_elapsed_reservation_before_any_provider_payload_releases_code(self):
        self.apply()
        snapshot = [{'product': self.product.pk, 'variant': self.variant.pk, 'name': self.product.name, 'size': 'M', 'color': 'Vert', 'quantity': 2, 'unit_amount': 3500}]
        order = reserve(self.client.session.session_key, self.customer.pk, snapshot, benefit=self.benefit)
        Order.objects.filter(pk=order.pk).update(reservation_expires_at=timezone.now() - timedelta(seconds=1))
        with patch('paiement.stock_checkout.stripe.Coupon.create') as create:
            self.assertIsNone(get_session(order.pk))
        create.assert_not_called()
        self.assertEqual(BenefitRedemption.objects.get(order=order).state, 'released')
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.reserved, 0)


from django.db import close_old_connections, connection
from django.test import TransactionTestCase
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier


class ConcurrentCustomerBenefitTests(TransactionTestCase):
    def test_two_checkout_attempts_cannot_reserve_one_customer_code(self):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL row locks required')
        customer = get_user_model().objects.create_user('concurrent-beneficiary', email='concurrent@example.test')
        verify_test_user(customer)
        benefit = save_benefit(CustomerBenefit(recipient_email=customer.email, label='Concurrent test', kind='amount', value=5), customer, notify=False)
        product = Product.objects.create(name='Polaire concurrence code', price=20)
        variant = Variant.objects.create(product=product, size='M', color='Bleu', quantity=10)
        snapshot = [{'product': product.pk, 'variant': variant.pk, 'name': product.name, 'size': 'M', 'color': 'Bleu', 'quantity': 1, 'unit_amount': 2000}]
        barrier = Barrier(2)
        def attempt(session):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                reserve(session, customer.pk, snapshot, benefit=benefit)
                return True
            except BenefitError:
                return False
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(attempt, ['code-checkout-one', 'code-checkout-two']))
        self.assertEqual(sorted(result), [False, True])
        self.assertEqual(BenefitRedemption.objects.filter(state='held').count(), 1)
        self.assertEqual(Order.objects.count(), 1)
        variant.refresh_from_db()
        self.assertEqual(variant.reserved, 1)
