from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.test import TransactionTestCase, TestCase, override_settings
from django.db import close_old_connections, connection
from django.utils import timezone
from core.models import Product
from paiement.models import Order
from paiement.stock_checkout import settle
from .models import Variant, Reservation
from .reservations import reserve, StockUnavailable


def snapshot(variant, quantity=1):
    return [{'variant':variant.pk,'product':variant.product_id,'name':'Polaire','size':variant.size,'color':variant.color,'unit_amount':1000,'quantity':quantity}]


def paid(order):
    return dict(id='cs_'+str(order.pk),metadata={'order_id':str(order.pk)},client_reference_id=str(order.pk),currency='eur',amount_subtotal=order.subtotal,amount_total=order.subtotal,payment_status='paid',status='complete')

class ReservationTests(TestCase):
    def setUp(self):
        self.product=Product.objects.create(name='Polaire',price=10)
        self.variant=Variant.objects.create(product=self.product,size='M',color='Bleu',quantity=2)

    def test_hold_payment_and_repeated_events(self):
        order=reserve('session',None,snapshot(self.variant))
        self.assertEqual(reserve('session',None,snapshot(self.variant)).pk,order.pk)
        self.variant.refresh_from_db();self.assertEqual((self.variant.quantity,self.variant.reserved),(2,1))
        self.assertTrue(settle(paid(order)));self.assertTrue(settle(paid(order)))
        self.variant.refresh_from_db();self.assertEqual((self.variant.quantity,self.variant.reserved),(1,0))
        self.assertEqual(Reservation.objects.get().state,'sold')

    def test_elapsed_time_does_not_release_until_provider_confirms(self):
        order=reserve('session',None,snapshot(self.variant,2))
        order.reservation_expires_at=timezone.now();order.save()
        with self.assertRaises(StockUnavailable):reserve('other',None,snapshot(self.variant))
        session=paid(order);session.update(status='expired',payment_status='unpaid')
        self.assertTrue(settle(session));self.assertTrue(settle(session))
        self.variant.refresh_from_db();self.assertEqual((self.variant.quantity,self.variant.reserved),(2,0))
        self.assertFalse(settle(paid(order)))
        reserve('other',None,snapshot(self.variant,2))

    def test_partial_basket_rolls_back(self):
        missing=Variant.objects.create(product=self.product,size='L',color='Bleu',quantity=0)
        with self.assertRaises(StockUnavailable):reserve('session',None,snapshot(self.variant)+snapshot(missing))
        self.variant.refresh_from_db();self.assertEqual(self.variant.reserved,0)
        self.assertFalse(Order.objects.exists());self.assertFalse(Reservation.objects.exists())

    def test_cart_variant_ownership_and_quantities(self):
        other=Product.objects.create(name='Autre',price=10)
        wrong=Variant.objects.create(product=other,size='S',color='Noir',quantity=3)
        url=f'/panier/ajouter/{self.product.slug}/'
        self.client.post(url,{'variant':wrong.pk,'quantity':1})
        self.assertFalse(self.client.session.get('cart'))
        self.client.post(url,{'variant':self.variant.pk,'quantity':2})
        self.client.post('/panier/modifier/',{'key':f'variant:{self.variant.pk}','quantity':3})
        self.assertEqual(self.client.session['cart'][f'variant:{self.variant.pk}']['quantity'],2)

class ConcurrentReservationTests(TransactionTestCase):
    def test_last_item_only_one_customer_can_reserve(self):
        if connection.vendor!='postgresql':self.skipTest('PostgreSQL row locks required')
        product=Product.objects.create(name='Dernier article',price=10)
        variant=Variant.objects.create(product=product,size='M',color='Bleu',quantity=1)
        data=snapshot(variant);barrier=Barrier(2)
        def attempt(session):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                reserve(session,None,data)
                return True
            except StockUnavailable:return False
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(attempt,['one','two']))
        self.assertEqual(sorted(results),[False,True])
        variant.refresh_from_db();self.assertEqual(variant.reserved,1)
        self.assertEqual(Order.objects.count(),1)

@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True)
class StockAdminReservationTests(TestCase):
    def test_admin_cannot_reduce_or_rename_reserved_variant(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse
        user=get_user_model().objects.create_superuser('admin-stock',email='admin@example.invalid',password='test')
        self.client.force_login(user,backend='connexion.backends.EmailOrUsernameBackend')
        product=Product.objects.create(name='Produit',price=10)
        variant=Variant.objects.create(product=product,size='M',color='Bleu',quantity=2)
        reserve('buyer',None,snapshot(variant))
        variant.refresh_from_db()
        for quantity,color in [(0,'Bleu'),('','Bleu'),(2,'Rouge')]:
            self.client.post(reverse('stocks:edit',args=[product.pk,variant.pk]),{'size':'M','color':color,'quantity':quantity,'reference':'','revision':variant.revision})
            variant.refresh_from_db();self.assertEqual((variant.quantity,variant.reserved,variant.color),(2,1,'Bleu'))

class AmbiguousStripeTests(TestCase):
    @override_settings(STRIPE_SECRET_KEY='sk_test',STRIPE_SHIPPING_RATE='shr_test',SITE_URL='http://testserver')
    def test_network_timeout_keeps_hold_and_retries_identical_payload(self):
        from unittest.mock import patch
        from paiement.stock_checkout import get_session
        from types import SimpleNamespace
        import stripe
        product=Product.objects.create(name='Produit',price=10)
        variant=Variant.objects.create(product=product,size='M',color='Bleu',quantity=1)
        order=reserve('buyer',None,snapshot(variant))
        with patch('paiement.stock_checkout.stripe.checkout.Session.create') as create:
            create.side_effect=[stripe.APIConnectionError('timeout'),SimpleNamespace(id='cs_recovered')]
            with self.assertRaises(stripe.APIConnectionError):get_session(order.pk)
            variant.refresh_from_db();self.assertEqual(variant.reserved,1)
            get_session(order.pk)
            self.assertEqual(create.call_args_list[0],create.call_args_list[1])
        order.refresh_from_db();self.assertEqual(order.stripe_session_id,'cs_recovered')
