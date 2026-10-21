"""Managed delivery is quoted before payment and frozen across payment retries."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
import uuid

import stripe
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from avoirs.models import BenefitRedemption, CustomerBenefit
from avoirs.services import BenefitError, save_benefit
from boutique.models import ShippingCountry
from boutique.shipping import quote
from boutique.test_shipping_settings import create_managed_shipping
from connexion.test_helpers import verify_test_user
from core.models import Product
from stocks.models import Variant
from stocks.reservations import StockUnavailable, reserve
from .models import Order
from .stock_checkout import get_session, settle
from .tests import CONFIG, SimpleNamespace


@override_settings(**{**CONFIG, 'STRIPE_SHIPPING_RATE': ''})
class ManagedShippingCheckoutTests(TestCase):
    def setUp(self):
        self.policy, self.france, self.belgium = create_managed_shipping()
        self.customer = get_user_model().objects.create_user('managed-shipping-buyer', email='delivery-buyer@example.test')
        verify_test_user(self.customer)
        self.client.force_login(self.customer, backend='connexion.backends.EmailOrUsernameBackend')
        self.product = Product.objects.create(name='Polaire livraison par pays', price='80', availability='available')
        self.variant = Variant.objects.create(product=self.product, size='M', color='Vert', quantity=20)
        self.checkout = reverse('paiement:checkout')
        self.cart = reverse('core:cart')
        self.client.post(reverse('core:cart_add', args=[self.product.slug]), {'variant': self.variant.pk, 'quantity': 1})

    def items(self):
        return [{'product': self.product.pk, 'variant': self.variant.pk, 'name': self.product.name,
                 'size': self.variant.size, 'color': self.variant.color, 'quantity': 1,
                 'unit_amount': int(Decimal(str(self.product.price)) * 100)}]

    def destination(self, country, **changes):
        return self.client.post(self.checkout, {
            'action': 'set_shipping_destination', 'shipping_country': country, **changes}, follow=True)

    def start(self):
        self.client.get(self.checkout)
        with patch('paiement.stock_checkout.stripe.Coupon.create', side_effect=lambda **params: SimpleNamespace(id=params['id'])) as coupon, \
             patch('paiement.stock_checkout.stripe.checkout.Session.create', return_value=SimpleNamespace(
                 id='cs_managed_' + uuid.uuid4().hex, status='open', url='https://checkout.stripe.com/managed-test')) as create:
            response = self.client.post(self.checkout)
        self.assertRedirects(response, 'https://checkout.stripe.com/managed-test', fetch_redirect_response=False)
        order = Order.objects.get(pk=self.client.session['checkout_order'])
        return order, create, coupon

    def grant(self, amount):
        return save_benefit(CustomerBenefit(recipient_email=self.customer.email,
            label='Avoir pour test livraison', kind='credit', value=Decimal(str(amount))), self.customer, notify=False)

    def apply(self, benefit):
        return self.client.post(self.checkout, {'action': 'apply_benefit', 'benefit_code': benefit.code}, follow=True)

    def paid(self, order, country=None, shipping=None, discount=0):
        country = order.shipping_snapshot['country'] if country is None else country
        shipping = order.shipping_snapshot['amount'] if shipping is None else shipping
        return SimpleNamespace(id=order.stripe_session_id, client_reference_id=str(order.pk),
            metadata={'order_id': str(order.pk)}, currency='eur', status='complete', payment_status='paid',
            amount_subtotal=order.subtotal, amount_total=order.subtotal - discount + shipping,
            total_details={'amount_shipping': shipping, 'amount_discount': discount},
            customer_details={'email': self.customer.email, 'name': 'Client Livraison',
                              'address': {'line1': '2 rue Client', 'postal_code': '01410', 'city': 'Lélex', 'country': country}},
            collected_information={'shipping_details': {'name': 'Client Livraison',
                'address': {'line1': '2 rue Client', 'postal_code': '01410', 'city': 'Lélex', 'country': country}}},
            consent={'terms_of_service': 'accepted'})

    def assert_no_holds(self):
        self.assertFalse(Order.objects.exists())
        self.assertFalse(BenefitRedemption.objects.exists())
        self.variant.refresh_from_db()
        self.assertEqual((self.variant.quantity, self.variant.reserved), (20, 0))

    def test_ambiguous_destination_or_nonserved_country_cannot_start_payment(self):
        # France may be the explicit default. Two other destinations require
        # a choice instead of guessing which international rate to charge.
        ShippingCountry.objects.filter(zone=self.france).update(code='DE')
        with patch('paiement.stock_checkout.stripe.checkout.Session.create') as create:
            self.client.get(self.checkout)
            self.client.post(self.checkout)
            self.destination('CH')
            self.client.post(self.checkout)
        create.assert_not_called()
        self.assert_no_holds()
        self.assertNotEqual(self.client.session.get('checkout_shipping_country'), 'CH')

    def test_selecting_country_is_passive_and_shows_exact_rate_before_payment(self):
        with patch('paiement.stock_checkout.stripe.checkout.Session.create') as create:
            response = self.destination('FR', return_to='cart', shipping_amount='0')
        self.assertTemplateUsed(response, 'core/cart.html')
        self.assertEqual(self.client.session['checkout_shipping_country'], 'FR')
        self.assertContains(response, '6,00')
        checkout = self.client.get(self.checkout)
        self.assertContains(checkout, '6,00')
        displayed = self.client.session['checkout_displayed_shipping']
        self.assertEqual(displayed['country'], 'FR')
        self.assertEqual(displayed['amount'], 600)
        create.assert_not_called()
        self.assert_no_holds()

    def test_country_action_requires_csrf_and_external_return_target_is_ignored(self):
        strict = Client(enforce_csrf_checks=True)
        strict.cookies = self.client.cookies.copy()
        self.assertEqual(strict.post(self.checkout, {
            'action': 'set_shipping_destination', 'shipping_country': 'FR'}).status_code, 403)
        self.assertNotIn('checkout_shipping_country', self.client.session)
        response = self.client.post(self.checkout, {'action': 'set_shipping_destination',
            'shipping_country': 'FR', 'return_to': 'https://outside.example.test/'})
        self.assertRedirects(response, self.checkout, fetch_redirect_response=False)
        self.assert_no_holds()

    def test_one_exact_inline_rate_and_selected_country_are_frozen_on_order(self):
        self.destination('BE')
        order, create, _ = self.start()
        params = create.call_args.kwargs
        self.assertEqual(params['shipping_address_collection'], {'allowed_countries': ['BE']})
        self.assertEqual(len(params['shipping_options']), 1)
        self.assertEqual(params['shipping_options'][0]['shipping_rate_data']['fixed_amount'],
                         {'amount': 1200, 'currency': 'eur'})
        self.assertNotIn('shipping_rate', params['shipping_options'][0])
        self.assertEqual(order.shipping_snapshot['country'], 'BE')
        self.assertEqual(order.shipping_snapshot['amount'], 1200)
        self.assertEqual(order.shipping_snapshot['delivery_min_days'], 4)
        self.assertEqual(order.shipping_snapshot['delivery_max_days'], 7)
        self.assertNotIn(self.customer.email, str(order.shipping_snapshot))

    def test_country_and_revision_are_part_of_reservation_identity(self):
        first = reserve('same-cart-shipping', self.customer.pk, self.items(), shipping_quote=quote(8000, 'FR'))
        self.assertEqual(reserve('same-cart-shipping', self.customer.pk, self.items(),
                                 shipping_quote=quote(8000, 'FR')).pk, first.pk)
        second = reserve('same-cart-shipping', self.customer.pk, self.items(), shipping_quote=quote(8000, 'BE'))
        self.assertNotEqual(first.pk, second.pk)
        self.france.price = Decimal('7')
        self.france.revision += 1
        self.france.save(update_fields=['price', 'revision'])
        third = reserve('same-cart-shipping', self.customer.pk, self.items(), shipping_quote=quote(8000, 'FR'))
        self.assertNotIn(third.pk, (first.pk, second.pk))
        self.assertEqual(Order.objects.count(), 3)

    def test_forged_or_stale_shipping_quote_rolls_back_order_and_inventory(self):
        initial = quote(8000, 'FR')
        cases = [{**initial, 'amount': 0}, {**initial, 'country': 'BE'},
                 {**initial, 'config_revision': initial['config_revision'] + 1}]
        for forged in cases:
            with self.subTest(forged=forged), self.assertRaises(StockUnavailable):
                reserve('forged-shipping', self.customer.pk, self.items(), shipping_quote=forged)
            self.assert_no_holds()
        self.france.price = Decimal('7')
        self.france.revision += 1
        self.france.save(update_fields=['price', 'revision'])
        with self.assertRaises(StockUnavailable):
            reserve('stale-shipping', self.customer.pk, self.items(), shipping_quote=initial)
        self.assert_no_holds()

    def test_new_order_requires_shipping_quote_when_managed_policy_is_present(self):
        with self.assertRaises(StockUnavailable):
            reserve('missing-shipping', self.customer.pk, self.items())
        self.assert_no_holds()

    def test_price_change_after_checkout_preview_requires_new_review_before_reservation(self):
        self.destination('FR')
        self.client.get(self.checkout)
        self.france.price = Decimal('7')
        self.france.revision += 1
        self.france.save(update_fields=['price', 'revision'])
        with patch('paiement.stock_checkout.stripe.checkout.Session.create') as create:
            self.client.post(self.checkout)
        create.assert_not_called()
        self.assert_no_holds()

    def test_network_retry_keeps_original_quote_and_payload_after_policy_changes(self):
        self.policy.public_notice = 'Expéditions du lundi au vendredi.'
        self.policy.save(update_fields=['public_notice'])
        order = reserve('retry-shipping', self.customer.pk, self.items(), shipping_quote=quote(8000, 'FR'))
        with patch('paiement.stock_checkout.stripe.checkout.Session.create',
                   side_effect=stripe.APIConnectionError('test timeout')) as create:
            with self.assertRaises(stripe.APIConnectionError):
                get_session(order.pk)
            original = deepcopy(create.call_args.kwargs)
        order.refresh_from_db()
        original_snapshot = deepcopy(order.shipping_snapshot)
        self.france.price = Decimal('15')
        self.france.revision += 1
        self.france.save(update_fields=['price', 'revision'])
        self.policy.free_shipping_threshold = Decimal('50')
        self.policy.public_notice = 'Expéditions uniquement le mercredi.'
        self.policy.revision += 1
        self.policy.save(update_fields=['free_shipping_threshold', 'public_notice', 'revision'])
        with patch('paiement.stock_checkout.stripe.checkout.Session.create', return_value=SimpleNamespace(
            id='cs_retry_managed', status='open', url='https://checkout.stripe.com/retry')) as create:
            get_session(order.pk)
        self.assertEqual(create.call_args.kwargs, original)
        order.refresh_from_db()
        self.assertEqual(order.shipping_snapshot, original_snapshot)
        self.assertIn('Expéditions du lundi au vendredi.', order.terms_snapshot['text'])
        self.assertNotIn('uniquement le mercredi', order.terms_snapshot['text'])

    def test_legacy_checkout_payload_is_preserved_after_managed_policy_is_created(self):
        payload = {'mode': 'payment', 'shipping_options': [{'shipping_rate': 'shr_legacy'}],
                   'shipping_address_collection': {'allowed_countries': ['FR']}}
        order = Order.objects.create(user=self.customer, session_key='legacy-shipping', items=self.items(),
            subtotal=8000, checkout_payload=payload, reservation_expires_at=timezone.now() + timedelta(minutes=30))
        with patch('paiement.stock_checkout.stripe.checkout.Session.create', return_value=SimpleNamespace(
            id='cs_legacy_managed', status='open', url='https://checkout.stripe.com/legacy')) as create:
            get_session(order.pk)
        for key, value in payload.items():
            self.assertEqual(create.call_args.kwargs[key], value)
        order.refresh_from_db()
        self.assertEqual(order.checkout_payload, payload)
        self.assertFalse(order.shipping_snapshot)

    def test_paid_country_or_shipping_amount_mismatch_does_not_consume_stock(self):
        self.destination('FR')
        order, _, _ = self.start()
        for changes in ({'country': 'BE'}, {'shipping': 500}):
            with self.subTest(changes=changes):
                self.assertFalse(settle(self.paid(order, **changes)))
                order.refresh_from_db()
                self.variant.refresh_from_db()
                self.assertEqual(order.status, 'pending')
                self.assertEqual((self.variant.quantity, self.variant.reserved), (20, 1))
        self.assertTrue(settle(self.paid(order)))
        self.assertTrue(settle(self.paid(order)))
        self.variant.refresh_from_db()
        self.assertEqual((self.variant.quantity, self.variant.reserved), (19, 0))

    def test_refund_and_purchase_documents_keep_original_delivery_after_admin_rate_change(self):
        from facturation.services import build_preview
        from retours.services import submit
        self.destination('FR')
        order, _, _ = self.start()
        original_terms = deepcopy(order.terms_snapshot)
        original_shipping = deepcopy(order.shipping_snapshot)
        self.assertTrue(settle(self.paid(order)))
        self.france.price = Decimal('20')
        self.france.revision += 1
        self.france.save(update_fields=['price', 'revision'])
        order.refresh_from_db()
        self.assertEqual(order.shipping_snapshot, original_shipping)
        self.assertEqual(order.terms_snapshot, original_terms)
        self.assertEqual(order.return_shipping_amount, 600)
        self.assertEqual(build_preview(order)['shipping_amount'], Decimal('6.00'))
        returned = submit(order.pk, {0: 1}, uuid.uuid4())
        self.assertEqual((returned.amount, returned.shipping_amount), (8600, 600))

    def test_country_change_expires_old_payment_before_recording_new_destination(self):
        self.destination('FR')
        order, _, _ = self.start()
        opened = SimpleNamespace(id=order.stripe_session_id, status='open', payment_status='unpaid')
        expired = SimpleNamespace(id=order.stripe_session_id, status='expired', payment_status='unpaid',
            client_reference_id=str(order.pk), metadata={'order_id': str(order.pk)})
        with patch('paiement.stock_checkout.stripe.checkout.Session.retrieve', return_value=opened), \
             patch('paiement.stock_checkout.stripe.checkout.Session.expire', return_value=expired):
            self.destination('BE')
        order.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(order.status, 'expired')
        self.assertEqual(self.variant.reserved, 0)
        self.assertEqual(self.client.session['checkout_shipping_country'], 'BE')

    def test_ambiguous_country_change_keeps_old_order_destination_and_reservations(self):
        self.destination('FR')
        order, _, _ = self.start()
        with patch('paiement.stock_checkout.stripe.checkout.Session.retrieve', side_effect=stripe.APIConnectionError('test timeout')):
            self.destination('BE')
        order.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(order.status, 'pending')
        self.assertEqual(self.client.session['checkout_shipping_country'], 'FR')
        self.assertEqual(self.variant.reserved, 1)
        self.assertEqual(Order.objects.count(), 1)

    def test_free_delivery_uses_merchandise_total_before_personal_discount(self):
        self.product.price = Decimal('100')
        self.product.save(update_fields=['price'])
        self.destination('FR')
        self.apply(self.grant('20'))
        order, create, coupon = self.start()
        self.assertEqual(order.subtotal, 10000)
        self.assertEqual(order.shipping_snapshot['amount'], 0)
        self.assertEqual(create.call_args.kwargs['shipping_options'][0]['shipping_rate_data']['fixed_amount']['amount'], 0)
        self.assertEqual(coupon.call_args.kwargs['amount_off'], 2000)

    def test_known_free_shipping_below_global_threshold_blocks_residual_under_fifty_cents(self):
        self.product.price = Decimal('10')
        self.product.save(update_fields=['price'])
        self.france.price = Decimal('0')
        self.france.free_shipping_eligible = False
        self.france.save(update_fields=['price', 'free_shipping_eligible'])
        self.destination('FR')
        benefit = self.grant('9.75')
        self.apply(benefit)
        session = self.client.session
        session['checkout_customer_benefit'] = benefit.pk
        session.save()
        with patch('paiement.stock_checkout.stripe.checkout.Session.create') as create:
            self.client.get(self.checkout)
            self.client.post(self.checkout)
        create.assert_not_called()
        self.assert_no_holds()

    def test_subfifty_cent_product_with_free_delivery_cannot_hold_inventory_without_a_code(self):
        self.product.price = Decimal('0.25')
        self.product.save(update_fields=['price'])
        self.france.price = Decimal('0')
        self.france.free_shipping_eligible = False
        self.france.save(update_fields=['price', 'free_shipping_eligible'])
        self.destination('FR')
        with patch('paiement.stock_checkout.stripe.checkout.Session.create') as create, \
             patch('paiement.stock_checkout.stripe.Coupon.create') as coupon:
            self.client.get(self.checkout)
            self.client.post(self.checkout)
        create.assert_not_called()
        coupon.assert_not_called()
        self.assert_no_holds()
        with self.assertRaises((StockUnavailable, BenefitError)):
            reserve('minimum-without-code', self.customer.pk, self.items(), shipping_quote=quote(25, 'FR'))
        self.assert_no_holds()

    def test_unknown_managed_destination_does_not_assume_free_shipping_when_applying_code(self):
        self.product.price = Decimal('100')
        self.product.save(update_fields=['price'])
        ShippingCountry.objects.filter(zone=self.france).update(code='DE')
        self.france.price = Decimal('12')
        self.france.free_shipping_eligible = False
        self.france.save(update_fields=['price', 'free_shipping_eligible'])
        benefit = self.grant('99.75')
        response = self.apply(benefit)
        self.assertEqual(self.client.session['checkout_customer_benefit'], benefit.pk)
        self.assertNotContains(response, 'un montant trop faible')
        self.assert_no_holds()
        self.destination('BE')
        order, _, coupon = self.start()
        self.assertEqual(order.shipping_snapshot['amount'], 1200)
        self.assertEqual(coupon.call_args.kwargs['amount_off'], 9975)
        self.assertEqual(order.subtotal - order.benefit_redemption.discount_amount + 1200, 1225)

    def test_paid_delivery_above_global_threshold_allows_small_merchandise_residual(self):
        self.product.price = Decimal('100')
        self.product.save(update_fields=['price'])
        self.destination('BE')
        self.apply(self.grant('99.75'))
        order, create, coupon = self.start()
        self.assertEqual(order.shipping_snapshot['amount'], 1200)
        self.assertEqual(coupon.call_args.kwargs['amount_off'], 9975)
        self.assertEqual(order.subtotal - order.benefit_redemption.discount_amount + order.shipping_snapshot['amount'], 1225)

    def test_exactly_fifty_cents_and_zero_total_are_allowed_with_known_free_delivery(self):
        self.product.price = Decimal('10')
        self.product.save(update_fields=['price'])
        self.france.price = Decimal('0')
        self.france.free_shipping_eligible = False
        self.france.save(update_fields=['price', 'free_shipping_eligible'])
        self.destination('FR')
        self.apply(self.grant('9.50'))
        order, _, _ = self.start()
        self.assertEqual(order.subtotal - order.benefit_redemption.discount_amount + order.shipping_snapshot['amount'], 50)
        # The domain check also explicitly accepts a genuinely zero total.
        from .benefits import validate_discounted_total
        validate_discounted_total(1000, 1000, shipping_cents=0)
