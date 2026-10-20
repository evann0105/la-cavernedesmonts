"""Privacy, CSRF and provider minimum-charge checks for personal codes."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from avoirs.models import BenefitRedemption, CustomerBenefit
from avoirs.services import save_benefit
from connexion.test_helpers import verify_test_user
from core.models import Product
from stocks.models import Variant
from .models import Order
from .tests import CONFIG, SimpleNamespace


@override_settings(**CONFIG)
class PersonalBenefitSecurityTests(TestCase):
    def setUp(self):
        self.customer = get_user_model().objects.create_user('private-code-buyer', email='private-code@example.test')
        verify_test_user(self.customer)
        self.client.force_login(self.customer, backend='connexion.backends.EmailOrUsernameBackend')
        self.product = Product.objects.create(name='Veste test code privé', price=100, availability='available')
        self.variant = Variant.objects.create(product=self.product, size='M', color='Bleu', quantity=4)
        self.client.post(reverse('core:cart_add', args=[self.product.slug]), {'variant': self.variant.pk, 'quantity': 1})
        self.checkout = reverse('paiement:checkout')

    def grant(self, amount):
        return save_benefit(CustomerBenefit(recipient_email=self.customer.email,
            label='Avoir personnel', kind='credit', value=amount), self.customer, notify=False)

    def apply(self, benefit):
        return self.client.post(self.checkout,
            {'action': 'apply_benefit', 'benefit_code': benefit.code}, follow=True)

    def test_checkout_code_and_confirmation_are_not_stored_in_browser_cache(self):
        benefit = self.grant(5)
        response = self.apply(benefit)
        self.assertContains(response, benefit.code)
        self.assertIn('no-store', response.headers.get('Cache-Control', ''))
        order = Order.objects.create(user=self.customer, session_key=self.client.session.session_key,
                                     items=[], subtotal=0, status='paid', total_paid=0)
        confirmation = self.client.get(reverse('paiement:confirmation', args=[order.pk]))
        self.assertEqual(confirmation.status_code, 200)
        self.assertIn('no-store', confirmation.headers.get('Cache-Control', ''))

    def test_applying_personal_code_requires_csrf(self):
        benefit = self.grant(5)
        strict = Client(enforce_csrf_checks=True)
        strict.cookies = self.client.cookies.copy()
        response = strict.post(self.checkout, {'action': 'apply_benefit', 'benefit_code': benefit.code})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('checkout_customer_benefit', self.client.session)
        self.assertFalse(BenefitRedemption.objects.exists())

    def test_positive_total_below_fifty_cents_with_free_shipping_never_holds_code_or_stock(self):
        benefit = self.grant('99.75')
        with patch('paiement.stock_checkout.stripe.Coupon.create') as coupon, \
             patch('paiement.stock_checkout.stripe.checkout.Session.create') as checkout:
            applied = self.apply(benefit)
            # A selection can also predate a price or shipping-threshold change.
            # An existing server-side session hint must never bypass validation.
            session = self.client.session
            session['checkout_customer_benefit'] = benefit.pk
            session.save()
            self.client.get(self.checkout)
            response = self.client.post(self.checkout, follow=True)
        self.assertTrue('0,50' in applied.content.decode() or '0,50' in response.content.decode())
        self.assertFalse(Order.objects.exists())
        self.assertFalse(BenefitRedemption.objects.exists())
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.reserved, 0)
        self.assertEqual(self.variant.quantity, 4)
        coupon.assert_not_called()
        checkout.assert_not_called()

    def test_exactly_fifty_cents_with_free_shipping_can_start_checkout(self):
        self.apply(self.grant('99.50'))
        with patch('paiement.stock_checkout.stripe.Coupon.create',
                   side_effect=lambda **params: SimpleNamespace(id=params['id'])) as coupon, \
             patch('paiement.stock_checkout.stripe.checkout.Session.create', return_value=SimpleNamespace(
                 id='cs_fifty_cents', status='open', url='https://checkout.stripe.com/minimum-test')) as checkout:
            response = self.client.post(self.checkout)
        self.assertRedirects(response, 'https://checkout.stripe.com/minimum-test', fetch_redirect_response=False)
        coupon.assert_called_once()
        checkout.assert_called_once()
        self.assertEqual(Order.objects.get().benefit_redemption.discount_amount, 9950)

    def test_invalid_percent_values_show_form_errors_without_creating_code(self):
        seller = get_user_model().objects.create_superuser('invalid-value-seller',
            email='invalid-value-seller@example.test', password='TestCompte2026!')
        verify_test_user(seller)
        self.client.force_login(seller, backend='connexion.backends.EmailOrUsernameBackend')
        for value in ('', 'abc', 'NaN', 'Infinity'):
            with self.subTest(value=value):
                response = self.client.post(reverse('avoirs:create'), {
                    'recipient_email': self.customer.email, 'label': 'Formulaire invalide',
                    'kind': 'percent', 'value': value, 'minimum_subtotal': '0',
                    'starts_at': '', 'ends_at': '', 'active': 'on',
                })
                self.assertEqual(response.status_code, 200)
                self.assertIn('value', response.context['form'].errors)
        self.assertFalse(CustomerBenefit.objects.exists())
