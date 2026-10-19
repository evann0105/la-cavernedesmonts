"""Variant-specific money remains server-owned through cart, payment and returns."""
from copy import deepcopy
from decimal import Decimal
from unittest.mock import patch
import uuid

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.models import Product
from facturation.services import build_preview
from retours.services import submit
from stocks.models import Reservation, Variant
from stocks.reservations import StockUnavailable, reserve
from .models import Order
from .stock_checkout import get_session, settle
from .tests import CONFIG, SimpleNamespace


@override_settings(**{**CONFIG, 'FREE_SHIPPING_THRESHOLD_EUR': '99.00'})
class VariantPriceCheckoutTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(name='Polaire junior et ado', price='50.00', availability='available')
        self.junior = Variant.objects.create(product=self.product, size='Junior', color='Vert', quantity=5, price='29.90')
        self.ado = Variant.objects.create(product=self.product, size='Ado', color='Vert', quantity=5, price='39.90')
        self.red = Variant.objects.create(product=self.product, size='Ado', color='Rouge', quantity=5)

    def add(self, variant, quantity=1, client=None):
        buyer = client or self.client
        return buyer.post(reverse('core:cart_add', args=[self.product.slug]), {
            'variant': variant.pk, 'quantity': quantity,
        })

    def snapshot(self, variant, amount, quantity=1):
        return [{
            'product': self.product.pk, 'variant': variant.pk, 'name': self.product.name,
            'size': variant.size, 'color': variant.color, 'quantity': quantity, 'unit_amount': amount,
        }]

    def test_cart_keeps_size_and_color_prices_separate_with_product_fallback(self):
        for variant in (self.junior, self.ado, self.red):
            self.add(variant)
        response = self.client.get(reverse('core:cart'))
        rows = {row['variant'].pk: row for row in response.context['cart_items']}
        self.assertEqual({pk: row['unit_price'] for pk, row in rows.items()}, {
            self.junior.pk: Decimal('29.90'), self.ado.pk: Decimal('39.90'), self.red.pk: Decimal('50.00'),
        })
        self.assertEqual(response.context['total'], Decimal('119.80'))
        self.assertEqual(rows[self.junior.pk]['subtotal'], Decimal('29.90'))
        self.assertContains(response, 'data-unit-price="29.90"')
        self.assertContains(response, 'data-unit-price="39.90"')
        self.assertContains(response, 'data-unit-price="50.00"')

    def test_cart_reprices_only_fallback_variants_after_product_price_changes(self):
        for variant in (self.junior, self.red):
            self.add(variant)
        Product.objects.filter(pk=self.product.pk).update(price=Decimal('65.00'))
        response = self.client.get(reverse('core:cart'))
        amounts = {row['variant'].pk: row['unit_price'] for row in response.context['cart_items']}
        self.assertEqual(amounts, {self.junior.pk: Decimal('29.90'), self.red.pk: Decimal('65.00')})
        self.assertEqual(response.context['total'], Decimal('94.90'))

    @patch('paiement.stock_checkout.stripe.checkout.Session.create')
    def test_checkout_snapshots_selected_prices_and_ignores_posted_money(self, create):
        for variant in (self.junior, self.ado, self.red):
            self.add(variant)
        create.return_value = SimpleNamespace(id='cs_variant_prices', url='https://checkout.stripe.com/variant-prices')
        response = self.client.post(reverse('paiement:checkout'), {
            'price': '0.01', 'unit_price': '0.01', 'unit_amount': '1', 'subtotal': '3',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, 'https://checkout.stripe.com/variant-prices')
        order = Order.objects.get()
        self.assertEqual(order.subtotal, 11980)
        self.assertEqual({item['variant']: item['unit_amount'] for item in order.items}, {
            self.junior.pk: 2990, self.ado.pk: 3990, self.red.pk: 5000,
        })
        self.assertEqual([item['price_data']['unit_amount'] for item in create.call_args.kwargs['line_items']],
                         [item['unit_amount'] for item in order.items])
        self.assertEqual(create.call_args.kwargs['shipping_options'][0]['shipping_rate_data']['fixed_amount']['amount'], 0)

    @patch('paiement.stock_checkout.stripe.checkout.Session.create')
    def test_shipping_threshold_follows_real_variant_totals_in_summary_and_stripe(self, create):
        for variant, quantity, free, total in ((self.junior, 3, False, '89.70'), (self.red, 2, True, '100.00')):
            with self.subTest(variant=variant.pk):
                buyer = Client()
                self.add(variant, quantity, buyer)
                for route in ('core:cart', 'paiement:checkout'):
                    page = buyer.get(reverse(route))
                    self.assertEqual(page.context['total'], Decimal(total))
                    self.assertEqual(page.context['shipping']['is_free'], free)
                create.return_value = SimpleNamespace(id=f'cs_price_shipping_{variant.pk}', url='https://checkout.stripe.com/variant-shipping')
                buyer.post(reverse('paiement:checkout'))
                option = create.call_args.kwargs['shipping_options'][0]
                if free:
                    self.assertEqual(option['shipping_rate_data']['fixed_amount']['amount'], 0)
                else:
                    self.assertEqual(option, {'shipping_rate': 'shr_test'})

    def test_reservation_rejects_forged_variant_price_and_rolls_back(self):
        for amount in (1, 5000):
            with self.subTest(amount=amount), self.assertRaises(StockUnavailable):
                reserve(f'forged-{amount}', None, self.snapshot(self.junior, amount))
        self.junior.refresh_from_db()
        self.assertEqual(self.junior.reserved, 0)
        self.assertFalse(Order.objects.exists())
        self.assertFalse(Reservation.objects.exists())

    def test_reservation_uses_current_locked_product_price_for_fallback(self):
        old_snapshot = self.snapshot(self.red, 5000)
        Product.objects.filter(pk=self.product.pk).update(price=Decimal('65.00'))
        with self.assertRaises(StockUnavailable):
            reserve('stale-fallback', None, old_snapshot)
        self.assertFalse(Order.objects.exists())
        order = reserve('current-fallback', None, self.snapshot(self.red, 6500))
        self.assertEqual(order.subtotal, 6500)

    @patch('paiement.stock_checkout.stripe.checkout.Session.create')
    def test_variant_price_changed_between_cart_read_and_reservation_cannot_be_charged(self, create):
        self.add(self.junior)
        cart_rows = self.client.get(reverse('core:cart')).context['cart_items']
        Variant.objects.filter(pk=self.junior.pk).update(price=Decimal('34.90'))
        with patch('paiement.views.lines', return_value=cart_rows):
            self.client.post(reverse('paiement:checkout'))
        create.assert_not_called()
        self.assertFalse(Order.objects.exists())
        self.junior.refresh_from_db()
        self.assertEqual(self.junior.reserved, 0)

    @patch('paiement.stock_checkout.stripe.checkout.Session.create')
    def test_discounted_price_never_allows_zero_stock_purchase(self, create):
        Variant.objects.filter(pk=self.junior.pk).update(quantity=0)
        self.add(self.junior)
        self.assertFalse(self.client.session.get('cart'))
        basket = self.client.session
        basket['cart'] = {f'variant:{self.junior.pk}': {
            'product': self.product.pk, 'variant': self.junior.pk, 'size': 'Junior',
            'color': 'Vert', 'quantity': 1,
        }}
        basket.save()
        self.client.post(reverse('paiement:checkout'))
        create.assert_not_called()
        self.assertFalse(Order.objects.exists())
        self.junior.refresh_from_db()
        self.assertEqual((self.junior.quantity, self.junior.reserved), (0, 0))

    @patch('retours.services.return_notice')
    @patch('retours.mail.order_confirmation')
    @patch('paiement.stock_checkout.stripe.checkout.Session.create')
    def test_later_price_changes_preserve_paid_order_invoice_and_partial_refund(self, create, confirmation, notice):
        self.add(self.junior, 2)
        self.add(self.red)
        create.return_value = SimpleNamespace(id='cs_prices_historical', url='https://checkout.stripe.com/variant-history')
        self.client.post(reverse('paiement:checkout'))
        order = Order.objects.get()
        original_items = deepcopy(order.items)
        original_payload = deepcopy(order.checkout_payload)
        self.assertTrue(settle({
            'id': 'cs_prices_historical', 'metadata': {'order_id': str(order.pk)},
            'client_reference_id': str(order.pk), 'currency': 'eur', 'status': 'complete',
            'payment_status': 'paid', 'amount_subtotal': 10980, 'amount_total': 10980,
            'total_details': {'amount_shipping': 0, 'amount_discount': 0},
            'customer_details': {'email': 'prices@example.test'},
        }))
        Variant.objects.filter(pk=self.junior.pk).update(price=Decimal('99.00'))
        Product.objects.filter(pk=self.product.pk).update(price=Decimal('75.00'))
        order.refresh_from_db()
        self.assertEqual(order.items, original_items)
        self.assertEqual(order.checkout_payload, original_payload)
        self.assertEqual((order.subtotal, order.total_paid), (10980, 10980))
        preview = build_preview(order)
        self.assertEqual(preview['total'], Decimal('109.80'))
        self.assertEqual([row['unit'] for row in preview['lines']], [Decimal('29.90'), Decimal('50.00')])
        junior_index = next(n for n, item in enumerate(order.items) if item['variant'] == self.junior.pk)
        returned = submit(order.pk, {junior_index: 1}, uuid.uuid4())
        self.assertEqual((returned.amount, returned.shipping_amount), (2990, 0))
        self.assertEqual(returned.lines.get().unit_amount, 2990)

    @patch('paiement.stock_checkout.stripe.checkout.Session.create')
    @patch('paiement.stock_checkout.stripe.checkout.Session.retrieve')
    def test_resumed_payment_retains_original_variant_amount_after_catalog_change(self, retrieve, create):
        order = reserve('pending-variant-price', None, self.snapshot(self.junior, 2990))
        create.return_value = SimpleNamespace(id='cs_prices_resume', url='https://checkout.stripe.com/variant-resume')
        get_session(order.pk)
        Variant.objects.filter(pk=self.junior.pk).update(price=Decimal('34.90'))
        retrieve.return_value = SimpleNamespace(id='cs_prices_resume', status='open', url='https://checkout.stripe.com/variant-resume')
        get_session(order.pk)
        create.assert_called_once()
        self.assertEqual(create.call_args.kwargs['line_items'][0]['price_data']['unit_amount'], 2990)
        order.refresh_from_db()
        self.assertEqual(order.items[0]['unit_amount'], 2990)
        self.assertEqual(order.checkout_payload['line_items'][0]['price_data']['unit_amount'], 2990)
