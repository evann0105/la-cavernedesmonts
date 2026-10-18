"""Delivery policy tests: server amounts, durable attempts and exact refunds."""
import uuid
from decimal import Decimal
from unittest.mock import patch

import stripe
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase, override_settings

from core.models import Product
from retours.services import submit
from stocks.models import Variant
from stocks.reservations import reserve
from .models import Order
from .stock_checkout import get_session, settle
from .tests import CONFIG, SimpleNamespace


@override_settings(FREE_SHIPPING_THRESHOLD_EUR='99.00', STRIPE_SHIPPING_RATE='shr_test')
class ShippingPolicyTests(SimpleTestCase):
    def test_threshold_is_inclusive_and_uses_exact_cents(self):
        from .shipping import shipping_option, shipping_summary
        for amount, remaining, free in (
            ('0.00', '99.00', False),
            ('98.99', '0.01', False),
            ('99.00', '0.00', True),
            ('99.01', '0.00', True),
        ):
            with self.subTest(amount=amount):
                self.assertEqual(shipping_summary(Decimal(amount)), {
                    'threshold': Decimal('99.00'),
                    'remaining': Decimal(remaining), 'is_free': free,
                })
                option = shipping_option(int(Decimal(amount) * 100))
                if free:
                    rate = option['shipping_rate_data']
                    self.assertEqual(rate['fixed_amount'], {'amount': 0, 'currency': 'eur'})
                    self.assertEqual(rate['type'], 'fixed_amount')
                    self.assertNotIn('shipping_rate', option)
                else:
                    self.assertEqual(option, {'shipping_rate': 'shr_test'})

    @override_settings(FREE_SHIPPING_THRESHOLD_EUR='149.90')
    def test_one_configured_threshold_drives_summary_and_stripe_rate(self):
        from .shipping import shipping_option, shipping_summary
        self.assertEqual(shipping_summary(Decimal('99.00'))['remaining'], Decimal('50.90'))
        self.assertFalse(shipping_summary(Decimal('149.89'))['is_free'])
        self.assertEqual(shipping_option(14989), {'shipping_rate': 'shr_test'})
        self.assertTrue(shipping_summary(Decimal('149.90'))['is_free'])
        self.assertEqual(shipping_option(14990)['shipping_rate_data']['fixed_amount']['amount'], 0)

    def test_invalid_configuration_cannot_silently_offer_wrong_shipping(self):
        from .shipping import shipping_option, shipping_summary
        for threshold in ('', 'invalid', 'NaN', 'Infinity', '-1', '0', '99.001', True):
            with self.subTest(threshold=threshold), override_settings(FREE_SHIPPING_THRESHOLD_EUR=threshold):
                with self.assertRaises(ImproperlyConfigured):
                    shipping_summary(Decimal('99.00'))
                with self.assertRaises(ImproperlyConfigured):
                    shipping_option(9900)

    def test_contract_uses_the_same_configured_threshold(self):
        from espace.legal import contract_snapshot
        text = contract_snapshot()['text']
        self.assertIn('99 € d’articles', text)
        self.assertIn('avant application d’un code promotionnel', text)
        with override_settings(FREE_SHIPPING_THRESHOLD_EUR='149.00'):
            self.assertIn('149 € d’articles', contract_snapshot()['text'])


@override_settings(**{**CONFIG, 'FREE_SHIPPING_THRESHOLD_EUR': '99.00'})
class ShippingCheckoutTests(TestCase):
    def make_order(self, price='33.00', quantity=3):
        product = Product.objects.create(name='Produit livraison', price=price)
        variant = Variant.objects.create(product=product, size='M', color='Bleu', quantity=10)
        items = [{
            'product': product.pk, 'variant': variant.pk, 'name': product.name,
            'size': variant.size, 'color': variant.color, 'quantity': quantity,
            'unit_amount': int(Decimal(price) * 100),
        }]
        return reserve(str(uuid.uuid4()), None, items)

    @patch('paiement.stock_checkout.stripe.checkout.Session.create')
    def test_checkout_applies_server_subtotal_including_quantities(self, create):
        create.return_value = SimpleNamespace(id='cs_shipping', url='https://checkout.stripe.com/test')
        order = self.make_order()
        self.assertEqual(order.subtotal, 9900)
        get_session(order.pk)
        option = create.call_args.kwargs['shipping_options'][0]
        self.assertEqual(option['shipping_rate_data']['fixed_amount']['amount'], 0)
        order.refresh_from_db()
        self.assertEqual(order.checkout_payload['shipping_options'], [option])

    @patch('paiement.stock_checkout.stripe.checkout.Session.create')
    def test_checkout_below_threshold_keeps_configured_paid_rate(self, create):
        create.return_value = SimpleNamespace(id='cs_shipping', url='https://checkout.stripe.com/test')
        get_session(self.make_order('98.99', 1).pk)
        self.assertEqual(create.call_args.kwargs['shipping_options'], [{'shipping_rate': 'shr_test'}])

    @patch('paiement.stock_checkout.stripe.checkout.Session.create')
    def test_network_retry_keeps_original_shipping_when_policy_changes(self, create):
        order = self.make_order('98.99', 1)
        create.side_effect = stripe.APIConnectionError('timeout')
        with self.assertRaises(stripe.APIConnectionError):
            get_session(order.pk)
        original_payload = create.call_args.kwargs.copy()
        order.refresh_from_db()
        self.assertEqual(order.checkout_payload['shipping_options'], [{'shipping_rate': 'shr_test'}])
        create.side_effect = None
        create.return_value = SimpleNamespace(id='cs_shipping', url='https://checkout.stripe.com/test')
        with override_settings(FREE_SHIPPING_THRESHOLD_EUR='50.00'):
            get_session(order.pk)
        self.assertEqual(create.call_args.kwargs, original_payload)

    def test_cart_and_checkout_show_the_same_remaining_amount(self):
        order = self.make_order('98.99', 1)
        item = order.items[0]
        self.client.post(f"/panier/ajouter/{Product.objects.get(pk=item['product']).slug}/", {
            'variant': item['variant'], 'quantity': 1,
        })
        for url in ('/panier/', '/paiement/'):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context['shipping']['remaining'], Decimal('0.01'))
                self.assertFalse(response.context['shipping']['is_free'])

    @override_settings(PAYMENTS_ENABLED=False)
    @patch('paiement.views.stripe.checkout.Session.create')
    def test_free_shipping_does_not_enable_closed_sales(self, create):
        order = self.make_order()
        item = order.items[0]
        self.client.post(f"/panier/ajouter/{Product.objects.get(pk=item['product']).slug}/", {
            'variant': item['variant'], 'quantity': 3,
        })
        before = Order.objects.count()
        self.client.post('/paiement/')
        create.assert_not_called()
        self.assertEqual(Order.objects.count(), before)

    @patch('retours.mail.deliver_pending')
    @patch('retours.services.return_notice')
    @patch('retours.mail.order_confirmation')
    @patch('paiement.stock_checkout.stripe.checkout.Session.create')
    def test_free_delivery_stays_zero_for_payment_and_partial_returns(self, create, confirmation, notice, deliver):
        create.return_value = SimpleNamespace(id='cs_free', url='https://checkout.stripe.com/test')
        order = self.make_order()
        get_session(order.pk)
        self.assertTrue(settle({
            'id': 'cs_free', 'payment_status': 'paid', 'status': 'complete',
            'currency': 'eur', 'amount_subtotal': 9900, 'amount_total': 9900,
            'total_details': {'amount_shipping': 0, 'amount_discount': 0},
            'client_reference_id': str(order.pk), 'metadata': {'order_id': str(order.pk)},
            'customer_details': {'email': 'test@example.test'},
        }))
        order.refresh_from_db()
        self.assertEqual(order.status, 'paid')
        self.assertEqual(order.return_shipping_amount, 0)
        first = submit(order.pk, {0: 1}, uuid.uuid4())
        last = submit(order.pk, {0: 2}, uuid.uuid4())
        self.assertEqual((first.amount, last.amount), (3300, 6600))
        self.assertEqual((first.shipping_amount, last.shipping_amount), (0, 0))
        self.assertEqual(first.amount + last.amount, order.total_paid)

    @patch('retours.mail.deliver_pending')
    @patch('retours.services.return_notice')
    @patch('retours.mail.order_confirmation')
    @patch('paiement.stock_checkout.stripe.checkout.Session.list_line_items')
    @patch('paiement.stock_checkout.stripe.checkout.Session.create')
    def test_promo_keeps_free_shipping_and_refunds_only_discounted_amount(self, create, line_items, confirmation, notice, deliver):
        from facturation.services import build_preview
        create.return_value = SimpleNamespace(id='cs_free_discount', url='https://checkout.stripe.com/test')
        order = self.make_order()
        get_session(order.pk)
        self.assertTrue(create.call_args.kwargs['allow_promotion_codes'])
        self.assertEqual(create.call_args.kwargs['shipping_options'][0]['shipping_rate_data']['fixed_amount']['amount'], 0)
        line_items.return_value = {'data': [{
            'id': 'li_discount', 'currency': 'eur', 'quantity': 3,
            'amount_subtotal': 9900, 'amount_discount': 1980, 'amount_total': 7920,
            'price': {'unit_amount': 3300, 'product': {'metadata': {'order_id': str(order.pk), 'order_item': '0'}}},
        }], 'has_more': False}
        self.assertTrue(settle({
            'id': 'cs_free_discount', 'payment_status': 'paid', 'status': 'complete',
            'currency': 'eur', 'amount_subtotal': 9900, 'amount_total': 7920,
            'total_details': {'amount_shipping': 0, 'amount_discount': 1980},
            'client_reference_id': str(order.pk), 'metadata': {'order_id': str(order.pk)},
            'customer_details': {'email': 'test@example.test'},
        }))
        order.refresh_from_db()
        self.assertEqual(order.return_shipping_amount, 0)
        preview = build_preview(order)
        self.assertEqual(preview['discount'], Decimal('19.80'))
        self.assertEqual(preview['total'], Decimal('79.20'))
        record = submit(order.pk, {0: 3}, uuid.uuid4())
        self.assertEqual((record.amount, record.shipping_amount), (7920, 0))
