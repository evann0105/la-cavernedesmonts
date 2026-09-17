from django.test import TestCase, Client, override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from django_otp.plugins.otp_totp.models import TOTPDevice
from decimal import Decimal
from unittest.mock import patch
from types import SimpleNamespace
from core.models import Product, Category
from paiement.models import Order
from .models import Address, CreditNote, Voucher


class CustomerPagesTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('customer',email='customer@example.com',password='Test-password-123')
        self.other=get_user_model().objects.create_user('other',email='other@example.com')
        self.client.force_login(self.user,backend='connexion.backends.EmailOrUsernameBackend')
        self.order=Order.objects.create(user=self.user,session_key='first',items=[],subtotal=1000,status='paid',total_paid=1200)
        self.other_order=Order.objects.create(user=self.other,session_key='second',items=[],subtotal=2000,status='paid')

    def test_private_pages_require_login_and_are_not_cached(self):
        for name in ['account','orders','credits','addresses','personal','vouchers']:
            url=reverse('espace:'+name)
            self.assertEqual(Client().get(url).status_code,302)
            response=self.client.get(url)
            self.assertEqual(response.status_code,200)
            self.assertIn('no-store',response.headers['Cache-Control'])

    def test_order_and_credit_privacy_not_based_on_email(self):
        self.other_order.customer_email=self.user.email
        self.other_order.save()
        CreditNote.objects.create(order=self.order,reference='MY-CREDIT',amount=Decimal('5.00'),reason='Retour')
        CreditNote.objects.create(order=self.other_order,reference='PRIVATE-CREDIT',amount=Decimal('5.00'),reason='Autre retour')
        page=self.client.get(reverse('espace:orders'))
        self.assertContains(page,str(self.order.pk))
        self.assertNotContains(page,str(self.other_order.pk))
        self.assertEqual(self.client.get(reverse('espace:order',args=[self.other_order.pk])).status_code,404)
        page=self.client.get(reverse('espace:credits'))
        self.assertContains(page,'MY-CREDIT');self.assertNotContains(page,'PRIVATE-CREDIT')

    def test_vouchers_are_only_visible_to_recipient(self):
        Voucher.objects.create(user=self.user,code='MY-CODE',description='Offre test')
        Voucher.objects.create(user=self.other,code='SECRET-CODE',description='Autre offre')
        page=self.client.get(reverse('espace:vouchers'))
        self.assertContains(page,'MY-CODE'); self.assertNotContains(page,'SECRET-CODE')

    def test_address_crud_and_ownership(self):
        data={'label':'Maison','recipient':'Client','line1':'Rue Test','line2':'','postal_code':'01410','city':'Lélex','country':'FR','user':self.other.pk}
        self.assertEqual(self.client.post(reverse('espace:address_add'),data).status_code,302)
        address=Address.objects.get();self.assertEqual(address.user,self.user)
        self.client.post(reverse('espace:address_edit',args=[address.pk]),dict(data,city='Autre ville'))
        address.refresh_from_db();self.assertEqual(address.city,'Autre ville')
        self.client.force_login(self.other,backend='connexion.backends.EmailOrUsernameBackend')
        self.assertEqual(self.client.post(reverse('espace:address_edit',args=[address.pk]),data).status_code,404)
        self.assertEqual(self.client.post(reverse('espace:address_delete',args=[address.pk])).status_code,404)
        self.client.force_login(self.user,backend='connexion.backends.EmailOrUsernameBackend')
        self.assertEqual(self.client.get(reverse('espace:address_delete',args=[address.pk])).status_code,405)
        self.assertEqual(self.client.post(reverse('espace:address_delete',args=[address.pk])).status_code,302)
        self.assertFalse(Address.objects.exists())

    def test_csrf_and_profile_reauthentication(self):
        strict=Client(enforce_csrf_checks=True)
        strict.force_login(self.user,backend='connexion.backends.EmailOrUsernameBackend')
        self.assertEqual(strict.post(reverse('espace:personal'),{}).status_code,403)
        data={'first_name':'Nouveau','last_name':'Nom','email':'new@example.com','current_password':'wrong','is_superuser':'true'}
        self.client.post(reverse('espace:personal'),data)
        self.user.refresh_from_db();self.assertEqual(self.user.email,'customer@example.com')
        data['current_password']='Test-password-123'
        self.assertEqual(self.client.post(reverse('espace:personal'),data).status_code,302)
        self.user.refresh_from_db();self.assertEqual(self.user.email,'new@example.com');self.assertFalse(self.user.is_superuser)
        data['email']=self.other.email
        self.assertEqual(self.client.post(reverse('espace:personal'),data).status_code,200)
        self.user.refresh_from_db();self.assertEqual(self.user.email,'new@example.com')

    def test_staff_and_enabled_otp_cannot_bypass_otp(self):
        self.user.is_staff=True;self.user.save()
        self.assertRedirects(self.client.get(reverse('espace:account')),reverse('two_factor:setup'),fetch_redirect_response=False)
        TOTPDevice.objects.create(user=self.user,confirmed=True)
        self.assertEqual(self.client.get(reverse('espace:orders')).status_code,302)

    def test_public_pages_and_sales_ranking(self):
        a=Product.objects.create(name='Paid product',price=10,is_published=True)
        b=Product.objects.create(name='Pending product',price=20,is_published=True)
        hidden=Product.objects.create(name='Hidden product',price=20,is_published=False)
        self.order.items=[{'product':a.pk,'quantity':2},{'product':hidden.pk,'quantity':50}];self.order.save()
        Order.objects.create(session_key='p',subtotal=2000,status='pending',items=[{'product':b.pk,'quantity':100}])
        response=self.client.get(reverse('espace:best'))
        self.assertContains(response,'Paid product');self.assertNotContains(response,'Pending product');self.assertNotContains(response,'Hidden product')
        self.assertNotContains(self.client.get(reverse('espace:terms')), 'Consulter la source')
        for name in ['promotions','new','contact','about','terms','sitemap']:
            self.assertEqual(self.client.get(reverse('espace:'+name)).status_code,200)
        self.assertNotContains(self.client.get(reverse('espace:new')),'Hidden product')

    @override_settings(PAYMENTS_ENABLED=True,SHOP_READY=True,STRIPE_SECRET_KEY='test',STRIPE_WEBHOOK_SECRET='test',STRIPE_SHIPPING_RATE='test',DEBUG=True)
    @patch('paiement.views.stripe.checkout.Session.create')
    def test_checkout_binds_owner_on_server(self,create):
        create.return_value=SimpleNamespace(id='cs_test_owner',url='https://checkout.stripe.com/test')
        product=Product.objects.create(name='Veste',price=10,sizes='M')
        session=self.client.session;session['cart']={f'{product.pk}:M':{'product':product.pk,'size':'M','quantity':1}};session.save()
        response=self.client.post(reverse('paiement:checkout'),{'user':self.other.pk})
        self.assertEqual(response.status_code,302)
        order=Order.objects.get(stripe_session_id='cs_test_owner')
        self.assertEqual(order.user,self.user)
        self.assertTrue(create.call_args.kwargs['allow_promotion_codes'])
