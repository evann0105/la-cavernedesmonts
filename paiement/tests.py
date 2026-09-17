import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from unittest.mock import patch
from django.test import TestCase, override_settings, Client
from core.models import Product
from .models import Order

CONFIG = dict(PAYMENTS_ENABLED=True, SHOP_READY=True, STRIPE_SECRET_KEY='sk_test_placeholder', STRIPE_WEBHOOK_SECRET='whsec_test', STRIPE_SHIPPING_RATE='shr_test', SITE_URL='http://testserver', DEBUG=True)


@override_settings(**CONFIG)
class PaymentTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(name='Polaire', slug='polaire', price='36.00', sizes='S,M')
        self.client.post('/panier/ajouter/polaire/', {'size':'M','quantity':2})

    @patch('paiement.views.stripe.checkout.Session.create')
    def start(self, create):
        create.return_value = SimpleNamespace(id='cs_test', url='https://checkout.stripe.com/test')
        response = self.client.post('/paiement/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(create.call_args.kwargs['line_items'][0]['price_data']['unit_amount'], 3600)
        return Order.objects.get()

    def payload(self, order, **changes):
        data = dict(id='cs_test', payment_status='paid', currency='eur', amount_subtotal=7200, amount_total=7800, client_reference_id=str(order.id), metadata={'order_id':str(order.id)}, customer_details={'email':'acheteur@example.test'}, collected_information={'shipping_details':{'name':'Test', 'address':{'country':'FR'}}})
        data.update(changes)
        return data

    def signed_event(self):
        payload = json.dumps({'type':'checkout.session.completed','data':{'object':{'id':'cs_test'}}}).encode()
        timestamp = str(int(time.time()))
        signature = hmac.new(b'whsec_test', timestamp.encode()+b'.'+payload, hashlib.sha256).hexdigest()
        return payload, f't={timestamp},v1={signature}'

    def test_checkout_server_price(self):
        order = self.start(); self.assertEqual(order.subtotal, 7200)

    @override_settings(PAYMENTS_ENABLED=False)
    @patch('paiement.views.stripe.checkout.Session.create')
    def test_disabled_checkout_never_calls_stripe(self, create):
        self.assertContains(self.client.get('/paiement/'), 'bientôt disponible')
        self.client.post('/paiement/')
        create.assert_not_called(); self.assertFalse(Order.objects.exists())

    @patch('paiement.views.stripe.checkout.Session.create')
    def test_unconfirmed_sizes_block_payment(self, create):
        self.product.sizes = ''; self.product.save()
        self.client.post('/paiement/')
        create.assert_not_called()

    def test_forged_webhook_rejected(self):
        response = self.client.post('/paiement/webhook/', '{}', content_type='application/json', HTTP_STRIPE_SIGNATURE='invalid')
        self.assertEqual(response.status_code, 400)

    @patch('paiement.views.stripe.checkout.Session.retrieve')
    def test_paid_webhook_is_idempotent(self, retrieve):
        order = self.start()
        retrieve.return_value = self.payload(order)
        payload, signature = self.signed_event()
        for _ in range(2):
            response = self.client.post('/paiement/webhook/', payload, content_type='application/json', HTTP_STRIPE_SIGNATURE=signature)
            self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, 'paid'); self.assertEqual(order.total_paid, 7800)
        self.assertEqual(order.customer_email, 'acheteur@example.test')
        self.assertEqual(Order.objects.count(), 1)
        self.client.get(f'/paiement/confirmation/{order.pk}/')
        self.assertFalse(self.client.session.get('cart'))

    @patch('paiement.views.stripe.checkout.Session.retrieve')
    def test_amount_mismatch_rejected(self, retrieve):
        order = self.start(); retrieve.return_value = self.payload(order, amount_subtotal=1)
        payload, signature = self.signed_event()
        response = self.client.post('/paiement/webhook/', payload, content_type='application/json', HTTP_STRIPE_SIGNATURE=signature)
        self.assertEqual(response.status_code, 400)
        order.refresh_from_db(); self.assertEqual(order.status, 'pending')

    @patch('paiement.views.stripe.checkout.Session.retrieve')
    def test_unpaid_webhook_does_not_confirm(self, retrieve):
        order = self.start(); retrieve.return_value = self.payload(order, payment_status='unpaid')
        payload, signature = self.signed_event()
        self.client.post('/paiement/webhook/', payload, content_type='application/json', HTTP_STRIPE_SIGNATURE=signature)
        order.refresh_from_db(); self.assertEqual(order.status, 'pending')

    def test_return_url_does_not_mark_paid_and_is_private(self):
        order = self.start()
        self.assertContains(self.client.get(f'/paiement/confirmation/{order.pk}/'), 'CONFIRMATION EN COURS')
        self.assertEqual(Client().get(f'/paiement/confirmation/{order.pk}/').status_code, 404)
        order.refresh_from_db(); self.assertEqual(order.status, 'pending')
        self.assertTrue(self.client.session.get('cart'))

    @patch('paiement.views.stripe.checkout.Session.retrieve')
    @patch('paiement.views.stripe.checkout.Session.create')
    def test_retry_reuses_session(self, create, retrieve):
        create.return_value = SimpleNamespace(id='cs_test', url='https://checkout.stripe.com/test')
        retrieve.return_value = SimpleNamespace(id='cs_test', status='open', url='https://checkout.stripe.com/test')
        self.client.post('/paiement/'); self.client.post('/paiement/')
        self.assertEqual(create.call_count, 1); self.assertEqual(Order.objects.count(), 1)

    @patch('paiement.views.stripe.checkout.Session.create')
    def test_provider_error_preserves_cart(self, create):
        import stripe
        create.side_effect = stripe.APIConnectionError('test')
        self.assertRedirects(self.client.post('/paiement/'), '/paiement/')
        self.assertTrue(self.client.session.get('cart'))
