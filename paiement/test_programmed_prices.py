"""A scheduled price must agree across display, cart, charge and history."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
import uuid

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import PriceObservation, Product, ProductPromotion
from facturation.services import build_preview
from retours.services import submit
from stocks.models import Reservation, Variant
from .models import Order
from .stock_checkout import settle
from .tests import CONFIG, SimpleNamespace


@override_settings(**CONFIG)
class ProgrammedPriceCheckoutTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(name='Polaire programmée', price='80.00', availability='available', description='Polaire', static_image='core/img/slide1.png')
        self.variant = Variant.objects.create(product=self.product, size='Junior', color='Vert', quantity=5)
        self.other = Variant.objects.create(product=self.product, size='Ado', color='Rouge', price='90.00', quantity=5)
        self.start = timezone.now() + timedelta(minutes=1)
        self.end = self.start + timedelta(minutes=2)
        self.promotion = ProductPromotion.objects.create(product=self.product, starts_at=self.start, ends_at=self.end,
            kind='percent', value='10', all_variants=False, reference_confirmed=True,
            reference_prices={str(self.variant.pk): '80.00'})
        self.promotion.targets.set([self.variant])

    def add(self):
        return self.client.post(reverse('core:cart_add', args=[self.product.slug]), {'variant':self.variant.pk, 'quantity':1})

    def test_public_price_endpoint_follows_exact_bounds_without_writing_history(self):
        counts = (PriceObservation.objects.count(), ProductPromotion.objects.count())
        url = reverse('core:product_prices', args=[self.product.slug])
        for at, amount, reference in [(self.start-timedelta(microseconds=1),'80.00',None), (self.start,'72.00','80.00'), (self.end,'80.00',None)]:
            with self.subTest(at=at), patch('django.utils.timezone.now', return_value=at):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response['Cache-Control'], 'no-store')
                rows = {item['id']:item for item in response.json()['variants']}
                self.assertEqual((rows[self.variant.pk]['price'], rows[self.variant.pk]['reference']), (amount, reference))
                self.assertEqual(rows[self.other.pk]['price'], '90.00')
        self.assertEqual((PriceObservation.objects.count(), ProductPromotion.objects.count()), counts)

    def test_basket_uses_the_active_price_and_restores_regular_price_after_end(self):
        self.add()
        for at, amount in [(self.start, '72.00'), (self.end, '80.00')]:
            with self.subTest(at=at), patch('django.utils.timezone.now', return_value=at):
                response = self.client.get(reverse('core:cart'))
                self.assertEqual(response.context['total'], Decimal(amount))
                self.assertEqual(response.context['cart_items'][0]['unit_price'], Decimal(amount))

    @patch('paiement.stock_checkout.stripe.checkout.Session.create')
    def test_expired_price_must_be_reviewed_before_a_more_expensive_charge(self, create):
        self.add()
        with patch('django.utils.timezone.now', return_value=self.start):
            review = self.client.get(reverse('paiement:checkout'))
            self.assertEqual(review.context['total'], Decimal('72.00'))
        with patch('django.utils.timezone.now', return_value=self.end):
            blocked = self.client.post(reverse('paiement:checkout'))
            self.assertRedirects(blocked, reverse('paiement:checkout'), fetch_redirect_response=False)
            create.assert_not_called()
            self.assertFalse(Order.objects.exists())
            self.assertFalse(Reservation.objects.exists())
            review = self.client.get(reverse('paiement:checkout'))
            self.assertEqual(review.context['total'], Decimal('80.00'))
            create.return_value = SimpleNamespace(id='cs_after_promotion', url='https://checkout.stripe.com/after-promotion')
            accepted = self.client.post(reverse('paiement:checkout'))
            self.assertEqual(accepted.url, create.return_value.url)
        self.assertEqual(create.call_args.kwargs['line_items'][0]['price_data']['unit_amount'], 8000)

    @patch('paiement.stock_checkout.stripe.checkout.Session.create')
    def test_boundary_between_basket_read_and_reserve_never_charges_stale_amount(self, create):
        self.add()
        with patch('django.utils.timezone.now', return_value=self.start):
            rows = self.client.get(reverse('core:cart')).context['cart_items']
        with patch('django.utils.timezone.now', return_value=self.end), patch('paiement.views.lines', return_value=rows):
            self.client.post(reverse('paiement:checkout'))
        create.assert_not_called()
        self.assertFalse(Order.objects.exists())
        self.variant.refresh_from_db()
        self.assertEqual((self.variant.quantity, self.variant.reserved), (5,0))

    @patch('retours.services.return_notice')
    @patch('retours.mail.order_confirmation')
    @patch('paiement.stock_checkout.stripe.checkout.Session.create')
    def test_purchase_snapshot_invoice_and_refund_keep_scheduled_price_after_expiry(self, create, confirmation, notice):
        self.add()
        create.return_value = SimpleNamespace(id='cs_during_promotion', url='https://checkout.stripe.com/during-promotion')
        with patch('django.utils.timezone.now', return_value=self.start):
            self.client.post(reverse('paiement:checkout'))
        order = Order.objects.get()
        self.assertEqual(order.items[0]['unit_amount'], 7200)
        self.assertEqual(create.call_args.kwargs['line_items'][0]['price_data']['unit_amount'], 7200)
        with patch('django.utils.timezone.now', return_value=self.end):
            self.assertTrue(settle({'id':'cs_during_promotion', 'metadata':{'order_id':str(order.pk)},
                'client_reference_id':str(order.pk), 'currency':'eur', 'status':'complete', 'payment_status':'paid',
                'amount_subtotal':7200, 'amount_total':7200,
                'total_details':{'amount_shipping':0,'amount_discount':0},
                'customer_details':{'email':'scheduled@example.test'}}))
            order.refresh_from_db()
            self.assertEqual(build_preview(order)['lines'][0]['unit'], Decimal('72.00'))
            returned = submit(order.pk, {0:1}, uuid.uuid4())
            self.assertEqual(returned.amount, 7200)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.regular_price, Decimal('80.00'))
