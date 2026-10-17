from django.test import TestCase, Client, override_settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.admin.models import LogEntry
from django.db import IntegrityError, transaction
from django.urls import reverse
from core.models import Product
from .models import Variant


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True)
class StockTests(TestCase):
    def setUp(self):
        self.manager=get_user_model().objects.create_user('stock-manager',is_staff=True)
        self.manager.user_permissions.add(Permission.objects.get(codename='manage_catalog'))
        self.client.force_login(self.manager,backend='connexion.backends.EmailOrUsernameBackend')
        self.product=Product.objects.create(name='Polaire test',price='35.00')
        self.url=reverse('stocks:product',args=[self.product.pk])

    def data(self,**kwargs):
        return dict(size='M',color='Bleu',quantity=3,reference='TEST-M',revision=0,**kwargs)

    def test_add_edit_unknown_zero_and_log(self):
        self.assertRedirects(self.client.post(self.url,self.data()),self.url)
        variant=Variant.objects.get();self.assertEqual(variant.quantity,3)
        url=reverse('stocks:edit',args=[self.product.pk,variant.pk])
        data=self.data();data.update(quantity='',revision=variant.revision)
        self.assertRedirects(self.client.post(url,data),self.url)
        variant.refresh_from_db();self.assertIsNone(variant.quantity)
        data.update(quantity=0,revision=variant.revision)
        self.assertRedirects(self.client.post(url,data),self.url)
        self.assertContains(self.client.get(self.url),'Rupture de stock')
        self.assertEqual(LogEntry.objects.filter(user=self.manager).count(),3)

    def test_invalid_and_duplicate_combinations(self):
        self.client.post(self.url,self.data())
        data=self.data();data.update(size=' m ',color=' BLEU ')
        self.assertContains(self.client.post(self.url,data),'existent déjà')
        data.update(size='L',quantity=-1)
        self.assertEqual(self.client.post(self.url,data).status_code,200)
        self.assertEqual(Variant.objects.count(),1)
        with self.assertRaises(IntegrityError),transaction.atomic():
            Variant.objects.create(product=self.product,size='m',color='BLEU',quantity=2)
        with self.assertRaises(IntegrityError),transaction.atomic():
            Variant.objects.create(product=self.product,size='L',color='Bleu',quantity=-1)

    def test_stale_edit_does_not_overwrite_stock(self):
        variant=Variant.objects.create(product=self.product,size='M',color='Bleu',quantity=9,revision=4)
        url=reverse('stocks:edit',args=[self.product.pk,variant.pk])
        self.assertContains(self.client.post(url,self.data()),'autre onglet')
        variant.refresh_from_db();self.assertEqual(variant.quantity,9)

    def test_permission_csrf_and_cross_product_checks(self):
        variant=Variant.objects.create(product=self.product,size='M',color='Bleu')
        other=Product.objects.create(name='Autre',price=20)
        self.assertEqual(self.client.post(reverse('stocks:edit',args=[other.pk,variant.pk]),self.data()).status_code,404)
        strict=Client(enforce_csrf_checks=True);strict.force_login(self.manager,backend='connexion.backends.EmailOrUsernameBackend')
        self.assertEqual(strict.post(self.url,self.data()).status_code,403)
        customer=get_user_model().objects.create_user('customer')
        self.client.force_login(customer,backend='connexion.backends.EmailOrUsernameBackend')
        self.assertEqual(self.client.post(self.url,self.data()).status_code,403)
        self.assertNotContains(self.client.get(reverse('espace:account')),'Gérer la boutique')
        self.client.logout();self.assertEqual(self.client.get(self.url).status_code,302)

    def test_admin_section_and_overview(self):
        response=self.client.get(reverse('espace:account'))
        self.assertContains(response,'Gérer la boutique',count=1)
        self.assertContains(response,reverse('stocks:overview'))
        self.assertNotContains(self.client.get('/'),'Gérer les produits')
        self.assertContains(self.client.get(reverse('stocks:overview')),'Stock non renseigné')
        self.assertContains(self.client.get(self.url),'Ajouter une variante')
