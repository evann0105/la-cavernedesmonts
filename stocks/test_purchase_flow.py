"""Browser endpoints + verified payment callback, with an isolated test database."""
import hashlib
import hmac
import json
import time
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.models import Product
from espace.legal_test_helpers import APPROVED_LEGAL_SETTINGS
from paiement.models import Order
from .models import Reservation, Variant


class CheckoutSession(dict):
    def __getattr__(self,name):
        return self[name]


SETTINGS={**APPROVED_LEGAL_SETTINGS, 'EMAIL_BACKEND':'django.core.mail.backends.locmem.EmailBackend',
    'PAYMENTS_ENABLED':True,'SHOP_READY':True,'STRIPE_SECRET_KEY':'sk_test_stock_flow',
    'STRIPE_WEBHOOK_SECRET':'whsec_stock_flow','STRIPE_SHIPPING_RATE':'shr_stock_flow',
    'SITE_URL':'http://testserver','DEBUG':True}


@override_settings(**SETTINGS)
class StockPurchaseFlowTests(TestCase):
    def setUp(self):
        self.product=Product.objects.create(name='Article de test de rupture',price=10,availability='available')
        self.variant=Variant.objects.create(product=self.product,size='M',color='Gris',quantity=5)

    def buy(self, quantity):
        buyer=Client()
        before=self.variant.quantity
        buyer.post(reverse('core:cart_add',args=[self.product.slug]),{'variant':self.variant.pk,'quantity':quantity})
        identifier=f'cs_stock_{Order.objects.count()+1}'
        with patch('paiement.stock_checkout.stripe.checkout.Session.create',return_value=CheckoutSession(
                id=identifier,status='open',url='https://checkout.stripe.com/stock-test')):
            response=buyer.post(reverse('paiement:checkout'))
        self.assertEqual(response.status_code,302)
        self.assertEqual(response.url,'https://checkout.stripe.com/stock-test')
        order=Order.objects.get(pk=buyer.session['checkout_order'])
        self.variant.refresh_from_db()
        self.assertEqual((self.variant.quantity,self.variant.reserved),(before,quantity))
        session={'id':identifier,'client_reference_id':str(order.pk),'metadata':{'order_id':str(order.pk)},
            'currency':'eur','payment_status':'paid','status':'complete','amount_subtotal':quantity*1000,
            'amount_total':quantity*1000,'total_details':{'amount_shipping':0,'amount_discount':0},
            'customer_details':{'email':'client-stock@example.test'}}
        event=json.dumps({'type':'checkout.session.completed','data':{'object':{'id':identifier}}}).encode()
        timestamp=str(int(time.time()))
        digest=hmac.new(b'whsec_stock_flow',timestamp.encode()+b'.'+event,hashlib.sha256).hexdigest()
        signature=f't={timestamp},v1={digest}'
        with patch('paiement.views.stripe.checkout.Session.retrieve',return_value=session):
            for _ in range(2):
                self.assertEqual(buyer.post(reverse('paiement:webhook'),event,content_type='application/json',
                    HTTP_STRIPE_SIGNATURE=signature).status_code,200)
        self.variant.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(order.status,'paid')
        self.assertEqual((self.variant.quantity,self.variant.reserved),(before-quantity,0))
        self.assertEqual(order.reservations.get().state,'sold')

    def test_paid_purchases_decrease_stock_to_zero_and_block_every_new_purchase(self):
        self.buy(1)
        self.assertEqual(self.variant.quantity,4)
        self.buy(4)
        self.assertEqual(self.variant.quantity,0)
        visitor=Client()
        page=visitor.get(reverse('core:product_detail',args=[self.product.slug]))
        self.assertContains(page,'Rupture de stock')
        self.assertContains(page,'<button type="submit" class="button" disabled>')
        self.assertContains(page,f'value="{self.variant.pk}" required disabled')
        added=visitor.post(reverse('core:cart_add',args=[self.product.slug]),{'variant':self.variant.pk,'quantity':1},follow=True)
        self.assertContains(added,'dépasse le stock disponible')
        self.assertFalse(visitor.session.get('cart'))
        # Even a forged/stale basket must not create a payable Stripe session.
        basket=visitor.session
        basket['cart']={f'variant:{self.variant.pk}':{'product':self.product.pk,'variant':self.variant.pk,
            'size':'M','color':'Gris','quantity':1}}
        basket.save()
        with patch('paiement.stock_checkout.stripe.checkout.Session.create') as create:
            self.assertEqual(visitor.post(reverse('paiement:checkout')).status_code,302)
            create.assert_not_called()
        self.variant.refresh_from_db()
        self.assertEqual((self.variant.quantity,self.variant.reserved),(0,0))
        self.assertEqual(Order.objects.count(),2)
        self.assertEqual(Reservation.objects.filter(state='sold').count(),2)

    def test_a_reserved_last_unit_cannot_be_ordered_by_another_customer(self):
        self.variant.quantity=1
        self.variant.save(update_fields=['quantity'])
        first=Client()
        second=Client()
        # Both customers can fill a basket before either begins paying.
        for buyer in (first,second):
            buyer.post(reverse('core:cart_add',args=[self.product.slug]),{'variant':self.variant.pk,'quantity':1})
        with patch('paiement.stock_checkout.stripe.checkout.Session.create',return_value=CheckoutSession(
                id='cs_last_reserved',status='open',url='https://checkout.stripe.com/stock-test')) as create:
            first.post(reverse('paiement:checkout'))
            second.post(reverse('paiement:checkout'))
            self.assertEqual(create.call_count,1)
        self.variant.refresh_from_db()
        self.assertEqual((self.variant.quantity,self.variant.reserved,self.variant.available),(1,1,0))
        self.assertEqual(Order.objects.count(),1)
