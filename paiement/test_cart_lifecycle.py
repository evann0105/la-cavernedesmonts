from unittest.mock import patch
from django.test import TestCase, override_settings
from django.urls import reverse
import stripe
from core.models import Product
from stocks.models import Variant
from paiement.models import Order
from paiement.stock_checkout import settle
from paiement.tests import CONFIG, SimpleNamespace


@override_settings(**{**CONFIG, "EMAIL_DELIVERY_ENABLED":True})
class CartPaymentLifecycleTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(name='Polaire', price=36, slug='cart-payment')
        self.variant = Variant.objects.create(product=self.product, size='M', color='Beige', quantity=2)
        self.key = f'variant:{self.variant.pk}'
        self.client.post(reverse('core:cart_add', args=[self.product.slug]), {'variant':self.variant.pk, 'quantity':2})

    def start(self):
        with patch('paiement.stock_checkout.stripe.checkout.Session.create', return_value=SimpleNamespace(id='cs_cart',status='open',url='https://checkout.stripe.com/cart')):
            self.client.post(reverse('paiement:checkout'))
        return Order.objects.get()

    def session(self, order, status='expired', paid=False):
        return SimpleNamespace(id='cs_cart',url='https://checkout.stripe.com/cart',status=status,payment_status='paid' if paid else 'unpaid',
            currency='eur',amount_subtotal=7200,amount_total=7800,total_details={'amount_shipping':600},
            client_reference_id=str(order.pk),metadata={'order_id':str(order.pk)},customer_details={'email':'client@example.test'})

    def test_late_payment_cleans_cart_before_any_new_checkout(self):
        order = self.start()
        settle(self.session(order, 'complete', True))
        with patch('paiement.stock_checkout.stripe.checkout.Session.create') as create:
            response = self.client.post(reverse('paiement:checkout'))
        self.assertRedirects(response, reverse('paiement:confirmation',args=[order.pk]))
        create.assert_not_called()
        self.assertEqual(Order.objects.count(), 1)
        self.assertFalse(self.client.session.get('cart'))

    def test_paid_cart_reconciles_once_and_preserves_new_quantities(self):
        order = self.start()
        session = self.client.session
        cart = session['cart']; cart[self.key]['quantity'] = 3
        session['cart'] = cart; session.save()
        settle(self.session(order, 'complete', True))
        self.client.get(reverse('core:cart'))
        self.assertEqual(self.client.session['cart'][self.key]['quantity'], 1)
        self.client.get(reverse('paiement:confirmation',args=[order.pk]))
        self.assertEqual(self.client.session['cart'][self.key]['quantity'], 1)

    def test_cancel_checkout_then_decrease_expires_stripe_before_release(self):
        order = self.start()
        with patch('paiement.stock_checkout.stripe.checkout.Session.retrieve',return_value=self.session(order,'open')), patch('paiement.stock_checkout.stripe.checkout.Session.expire',return_value=self.session(order)) as expire:
            response = self.client.post(reverse('core:cart_update'), {'key':self.key, 'quantity':1})
        self.assertEqual(response.status_code,302)
        expire.assert_called_once()
        self.assertEqual(self.client.session['cart'][self.key]['quantity'],1)
        self.variant.refresh_from_db(); order.refresh_from_db()
        self.assertEqual(self.variant.reserved,0)
        self.assertEqual(order.status,'expired')
        self.assertEqual(order.reservations.get().state,'released')

    def test_uncertain_cancellation_keeps_stock_and_cart(self):
        order = self.start()
        with patch('paiement.stock_checkout.stripe.checkout.Session.retrieve',return_value=self.session(order,'open')), patch('paiement.stock_checkout.stripe.checkout.Session.expire',side_effect=stripe.APIConnectionError('timeout')):
            self.client.post(reverse('core:cart_update'), {'key':self.key,'quantity':1})
        self.assertEqual(self.client.session['cart'][self.key]['quantity'],2)
        self.variant.refresh_from_db(); order.refresh_from_db()
        self.assertEqual(self.variant.reserved,2); self.assertEqual(order.status,'pending')

    def test_payment_winning_cancellation_race_is_never_released(self):
        order = self.start()
        with patch('paiement.stock_checkout.stripe.checkout.Session.retrieve',side_effect=[self.session(order,'open'), self.session(order,'complete',True)]), patch('paiement.stock_checkout.stripe.checkout.Session.expire',side_effect=stripe.InvalidRequestError('Already completed','id')):
            self.client.post(reverse('core:cart_update'), {'key':self.key,'quantity':1})
        self.assertFalse(self.client.session.get('cart'))
        self.variant.refresh_from_db(); order.refresh_from_db()
        self.assertEqual(self.variant.quantity,0); self.assertEqual(self.variant.reserved,0)
        self.assertEqual(order.status,'paid'); self.assertEqual(order.reservations.get().state,'sold')

    def test_pending_identical_checkout_still_reuses_session(self):
        order = self.start()
        with patch('paiement.stock_checkout.stripe.checkout.Session.retrieve',return_value=self.session(order,'open')), patch('paiement.stock_checkout.stripe.checkout.Session.create') as create, patch('paiement.stock_checkout.stripe.checkout.Session.expire') as expire:
            self.client.post(reverse('paiement:checkout'))
        create.assert_not_called(); expire.assert_not_called(); self.assertEqual(Order.objects.count(),1)

    def test_ambiguous_creation_is_recovered_before_cancelling(self):
        with patch('paiement.stock_checkout.stripe.checkout.Session.create',side_effect=stripe.APIConnectionError('timeout')):
            self.client.post(reverse('paiement:checkout'))
        order = Order.objects.get()
        self.assertIsNone(order.stripe_session_id)
        with patch('paiement.stock_checkout.stripe.checkout.Session.create',return_value=self.session(order,'open')) as create, patch('paiement.stock_checkout.stripe.checkout.Session.expire',return_value=self.session(order)):
            self.client.post(reverse('core:cart_update'),{'key':self.key,'quantity':1})
        self.assertEqual(create.call_args.kwargs['idempotency_key'],f'order-{order.pk}')
        self.variant.refresh_from_db(); self.assertEqual(self.variant.reserved,0)
        self.assertEqual(self.client.session['cart'][self.key]['quantity'],1)
