"""Personal codes entered in the cart share checkout's verified server quote."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from avoirs.models import BenefitRedemption, CustomerBenefit
from avoirs.services import save_benefit
from connexion.models import EmailVerification
from connexion.test_helpers import verify_test_user
from paiement.benefits import SESSION_KEY
from paiement.models import Order
from paiement.tests import CONFIG
from stocks.models import Variant
from .models import Product


@override_settings(**CONFIG)
class CartCustomerBenefitTests(TestCase):
    def setUp(self):
        self.customer = get_user_model().objects.create_user('cart-benefit-buyer', email='cart-buyer@example.test')
        verify_test_user(self.customer)
        self.client.force_login(self.customer, backend='connexion.backends.EmailOrUsernameBackend')
        self.product = Product.objects.create(name='Polaire panier code', price='79.90', availability='available')
        self.variant = Variant.objects.create(product=self.product, size='M', color='Vert', quantity=20)
        self.cart = reverse('core:cart')
        self.checkout = reverse('paiement:checkout')
        self.client.post(reverse('core:cart_add', args=[self.product.slug]), {'variant': self.variant.pk, 'quantity': 1})
        # Any accidental attempt to start payment fails locally, never on a provider.
        for endpoint in ('stripe.Coupon.create', 'stripe.checkout.Session.create'):
            guarded = patch('paiement.stock_checkout.' + endpoint,
                            side_effect=AssertionError('Cart code actions must not start payment.'))
            guarded.start()
            self.addCleanup(guarded.stop)

    def grant(self, **changes):
        fields = {'recipient_email': self.customer.email, 'label': 'Votre avoir personnel',
                  'kind': 'credit', 'value': Decimal('50'), 'minimum_subtotal': Decimal('0')}
        fields.update(changes)
        return save_benefit(CustomerBenefit(**fields), self.customer, notify=False)

    def apply(self, benefit, client=None, **extra):
        browser = client or self.client
        response = browser.post(self.checkout, {
            'action': 'apply_benefit', 'benefit_code': benefit.code.lower(),
            'return_to': 'cart', **extra,
        })
        self.assertRedirects(response, self.cart, fetch_redirect_response=False)
        return browser.get(self.cart)

    def price(self, value):
        self.product.price = Decimal(value)
        self.product.save(update_fields=['price'])

    def amounts(self, response, subtotal, discount, net):
        self.assertEqual(response.context['total'], Decimal(subtotal))
        self.assertEqual(response.context['discount'], Decimal(discount))
        self.assertEqual(response.context['discounted_total'], Decimal(net))

    def assert_no_reservation(self):
        self.assertFalse(Order.objects.filter(status='pending').exists())
        self.assertFalse(BenefitRedemption.objects.filter(state='held').exists())
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.reserved, 0)
        self.assertEqual(self.variant.quantity, 20)

    def test_cart_exposes_code_form_and_private_server_quote(self):
        response = self.client.get(self.cart)
        self.assertContains(response, 'name="benefit_code"')
        self.assertContains(response, 'name="return_to" value="cart"')
        self.assertIn('no-store', response.headers.get('Cache-Control', ''))
        self.assertFalse(response.context['benefit_selected'])
        self.assertIsNone(response.context['benefit'])
        self.amounts(response, '79.90', '0', '79.90')

    def test_fifty_euro_credit_on_79_90_shows_29_90_remaining(self):
        benefit = self.grant()
        response = self.apply(benefit)
        self.assertEqual(self.client.session[SESSION_KEY], benefit.pk)
        self.assertEqual(response.context['benefit'].pk, benefit.pk)
        self.assertTrue(response.context['benefit_selected'])
        self.assertFalse(response.context['benefit_error'])
        self.amounts(response, '79.90', '50', '29.90')

    def test_fifteen_percent_on_eighty_shows_twelve_euro_discount(self):
        self.price('80')
        response = self.apply(self.grant(kind='percent', value=15))
        self.amounts(response, '80', '12', '68')

    def test_minimum_start_end_and_inactive_codes_are_rejected_in_cart(self):
        for changes in ({'minimum_subtotal': Decimal('80')},
                        {'starts_at': timezone.now() + timedelta(days=1)},
                        {'ends_at': timezone.now() - timedelta(seconds=1)}, {'active': False}):
            with self.subTest(changes=changes):
                response = self.apply(self.grant(**changes))
                self.assertNotIn(SESSION_KEY, self.client.session)
                self.amounts(response, '79.90', '0', '79.90')
                self.assert_no_reservation()

    def test_used_code_cannot_reduce_cart_again(self):
        benefit = self.grant()
        order = Order.objects.create(user=self.customer, session_key='older-paid-order', items=[],
                                     subtotal=7990, total_paid=2990, status='paid')
        redemption = BenefitRedemption.objects.create(benefit=benefit, order=order, state='used',
            discount_amount=5000, used_at=timezone.now())
        response = self.apply(benefit)
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.amounts(response, '79.90', '0', '79.90')
        redemption.refresh_from_db()
        self.assertEqual(redemption.state, 'used')
        self.assert_no_reservation()

    def test_code_for_another_customer_cannot_reduce_cart(self):
        other = get_user_model().objects.create_user('cart-other-customer', email='cart-other@example.test')
        verify_test_user(other)
        response = self.apply(self.grant(recipient_email=other.email))
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.amounts(response, '79.90', '0', '79.90')
        self.assert_no_reservation()

    @override_settings(DEBUG=False, LOCAL_SKIP_EMAIL_VERIFICATION=True)
    def test_production_requires_real_email_verification_and_rechecks_selected_code(self):
        benefit = self.grant()
        EmailVerification.objects.filter(user=self.customer).delete()
        response = self.apply(benefit)
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.amounts(response, '79.90', '0', '79.90')
        verify_test_user(self.customer)
        self.apply(benefit)
        EmailVerification.objects.filter(user=self.customer).delete()
        response = self.client.get(self.cart)
        self.assertTrue(response.context['benefit_error'])
        self.amounts(response, '79.90', '0', '79.90')
        self.assert_no_reservation()

    @override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True)
    def test_local_seller_assigned_code_applies_in_cart_and_survives_checkout_without_confirmation(self):
        benefit = self.grant()
        self.assertEqual((benefit.user_id, benefit.bound_user_id), (self.customer.pk, self.customer.pk))
        EmailVerification.objects.filter(user=self.customer).delete()
        response = self.apply(benefit)
        self.assertEqual(self.client.session[SESSION_KEY], benefit.pk)
        self.amounts(response, '79.90', '50', '29.90')
        self.assertNotContains(response, 'Confirmez votre adresse e-mail')
        self.assertNotContains(response, 'Confirmer mon adresse')
        checkout = self.client.get(self.checkout)
        self.assertEqual(checkout.context['benefit'].pk, benefit.pk)
        self.amounts(checkout, '79.90', '50', '29.90')
        self.assertNotContains(checkout, 'Confirmez votre adresse e-mail')
        self.assertFalse(EmailVerification.objects.filter(user=self.customer).exists())
        self.assert_no_reservation()

    def test_guest_cannot_apply_personal_credit(self):
        benefit = self.grant()
        guest = Client()
        guest.post(reverse('core:cart_add', args=[self.product.slug]), {'variant': self.variant.pk, 'quantity': 1})
        response = self.apply(benefit, client=guest)
        self.assertNotIn(SESSION_KEY, guest.session)
        self.amounts(response, '79.90', '0', '79.90')
        self.assert_no_reservation()

    def test_cart_code_and_discount_are_preserved_when_opening_checkout(self):
        benefit = self.grant()
        self.apply(benefit)
        response = self.client.get(self.checkout)
        self.assertEqual(self.client.session[SESSION_KEY], benefit.pk)
        self.assertEqual(response.context['benefit'].pk, benefit.pk)
        self.amounts(response, '79.90', '50', '29.90')
        self.assert_no_reservation()

    def test_quantity_changes_recalculate_percent_discount_on_server(self):
        self.price('80')
        benefit = self.grant(kind='percent', value=15)
        self.apply(benefit)
        response = self.client.post(reverse('core:cart_update'), {
            'key': f'variant:{self.variant.pk}', 'quantity': 2,
        }, follow=True)
        self.assertEqual(self.client.session[SESSION_KEY], benefit.pk)
        self.amounts(response, '160', '24', '136')
        self.assert_no_reservation()

    def test_remove_action_returns_to_cart_and_restores_gross_total(self):
        self.apply(self.grant())
        response = self.client.post(self.checkout, {'action': 'remove_benefit', 'return_to': 'cart'})
        self.assertRedirects(response, self.cart, fetch_redirect_response=False)
        self.assertNotIn(SESSION_KEY, self.client.session)
        response = self.client.get(self.cart)
        self.assertFalse(response.context['benefit_selected'])
        self.amounts(response, '79.90', '0', '79.90')
        self.assert_no_reservation()

    def test_empty_cart_has_no_active_credit_or_negative_remaining_total(self):
        self.apply(self.grant())
        response = self.client.post(reverse('core:cart_update'), {
            'key': f'variant:{self.variant.pk}', 'quantity': 0,
        }, follow=True)
        self.assertFalse(response.context['cart_items'])
        self.assertFalse(response.context['benefit_selected'])
        self.amounts(response, '0', '0', '0')
        self.assert_no_reservation()

    def test_cart_code_actions_require_csrf(self):
        benefit = self.grant()
        strict = Client(enforce_csrf_checks=True)
        strict.cookies = self.client.cookies.copy()
        self.assertEqual(strict.post(self.checkout, {
            'action': 'apply_benefit', 'benefit_code': benefit.code, 'return_to': 'cart',
        }).status_code, 403)
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.apply(benefit)
        self.assertEqual(strict.post(self.checkout, {'action': 'remove_benefit', 'return_to': 'cart'}).status_code, 403)
        self.assertEqual(self.client.session[SESSION_KEY], benefit.pk)

    def test_forged_discount_fields_are_ignored_without_consuming_or_reserving(self):
        self.price('80')
        benefit = self.grant(kind='percent', value=15)
        response = self.apply(benefit, discount='80', discount_amount=8000, discounted_total='0', percent=100)
        self.amounts(response, '80', '12', '68')
        self.assertFalse(BenefitRedemption.objects.exists())
        self.assert_no_reservation()

    def test_external_return_target_is_ignored(self):
        benefit = self.grant()
        response = self.client.post(self.checkout, {
            'action': 'apply_benefit', 'benefit_code': benefit.code,
            'return_to': 'https://outside.example.test/steal-code',
        })
        self.assertRedirects(response, self.checkout, fetch_redirect_response=False)
        self.assertEqual(self.client.session[SESSION_KEY], benefit.pk)
        self.assert_no_reservation()

