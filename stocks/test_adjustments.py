from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.db import close_old_connections, connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from core.models import Product
from .adjustments import AdjustmentError, MAX_QUANTITY, adjust_stock
from .models import Reservation, Variant
from .reservations import StockUnavailable, reserve
from .test_reservations import snapshot


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True)
class ManualAdjustmentTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user('manual-stock-manager', is_staff=True)
        self.manager.user_permissions.add(Permission.objects.get(codename='manage_catalog'))
        self.product = Product.objects.create(name='Polaire manuelle', price=10)
        self.variant = Variant.objects.create(product=self.product, size='M', color='Bleu', quantity=8, reserved=2)
        self.client.force_login(self.manager, backend='connexion.backends.EmailOrUsernameBackend')
        self.url = reverse('stocks:adjust', args=[self.product.pk, self.variant.pk])

    def adjust(self, operation, amount, **changes):
        values = {'actor':self.manager, 'operation':operation, 'amount':amount,
                  'revision':self.variant.revision, 'reason':'Vente en boutique'}
        values.update(changes)
        result = adjust_stock(self.product.pk, self.variant.pk, **values)
        self.variant.refresh_from_db()
        return result

    def test_add_remove_and_count_keep_reservations_and_audit(self):
        self.adjust('add', 3)
        self.assertEqual((self.variant.quantity, self.variant.reserved, self.variant.available), (11, 2, 9))
        self.adjust('remove', 5)
        self.assertEqual(self.variant.quantity, 6)
        self.adjust('set', 3)
        self.assertEqual((self.variant.quantity, self.variant.reserved, self.variant.revision), (3, 2, 3))
        entries = LogEntry.objects.filter(user=self.manager)
        self.assertEqual(entries.count(), 3)
        self.assertIn('6 → 3', entries.first().change_message)
        self.assertIn('Vente en boutique', entries.first().change_message)
        self.assertContains(self.client.get(reverse('stocks:product',args=[self.product.pk])), 'Historique des modifications')

    def test_cannot_remove_reserved_units_but_can_sell_all_available_units(self):
        with self.assertRaises(AdjustmentError):
            self.adjust('remove', 7)
        self.assertEqual(LogEntry.objects.count(), 0)
        self.adjust('remove', 6)
        self.assertEqual((self.variant.quantity, self.variant.reserved, self.variant.available), (2, 2, 0))
        with self.assertRaises(AdjustmentError):
            self.adjust('set', 1)

    def test_unknown_stock_needs_an_explicit_count_and_zero_is_allowed(self):
        self.variant.quantity = None
        self.variant.reserved = 0
        self.variant.save(update_fields=['quantity','reserved'])
        for operation in ('add','remove'):
            with self.assertRaises(AdjustmentError):
                self.adjust(operation, 2)
        self.adjust('set', 0)
        self.assertEqual(self.variant.quantity, 0)
        self.assertEqual(LogEntry.objects.count(), 1)

    def test_double_post_and_stale_revision_apply_at_most_one_movement(self):
        payload = {'operation':'add','amount':2,'revision':0,'reason':'Réassort'}
        self.assertRedirects(self.client.post(self.url, payload), reverse('stocks:product',args=[self.product.pk]))
        self.assertContains(self.client.post(self.url, payload), 'Le stock a changé')
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.quantity, 10)
        self.assertEqual(LogEntry.objects.count(), 1)

    def test_invalid_inputs_overflow_and_no_change_are_not_logged(self):
        for operation, amount in [('missing',1), ('add',0), ('remove',-1), ('set',True),
                                  ('add',1.5), ('add',MAX_QUANTITY), ('set',MAX_QUANTITY+1)]:
            with self.subTest(operation=operation, amount=amount), self.assertRaises(AdjustmentError):
                self.adjust(operation, amount)
        self.adjust('set',8)
        self.assertEqual(self.variant.revision,0)
        self.assertEqual(LogEntry.objects.count(),0)
        self.assertContains(self.client.post(self.url,{'operation':'remove','amount':0,'revision':0}), 'au moins une unité')
        self.assertContains(self.client.post(self.url,{'operation':'add','amount':1}), 'Rechargez les quantités')

    def test_failed_audit_rolls_back_the_quantity(self):
        with patch('stocks.adjustments.LogEntry.objects.log_actions', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                self.adjust('add', 2)
        self.variant.refresh_from_db()
        self.assertEqual((self.variant.quantity,self.variant.revision), (8,0))

    def test_manager_only_csrf_and_variant_ownership(self):
        payload = {'operation':'add','amount':2,'revision':0}
        other = Product.objects.create(name='Autre stock',price=10)
        wrong = reverse('stocks:adjust',args=[other.pk,self.variant.pk])
        self.assertEqual(self.client.post(wrong,payload).status_code,404)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.manager,backend='connexion.backends.EmailOrUsernameBackend')
        self.assertEqual(strict.post(self.url,payload).status_code,403)
        customer = get_user_model().objects.create_user('manual-stock-customer')
        self.client.force_login(customer,backend='connexion.backends.EmailOrUsernameBackend')
        self.assertEqual(self.client.get(self.url).status_code,403)
        self.assertEqual(self.client.post(self.url,payload).status_code,403)
        with self.assertRaises(PermissionDenied):
            self.adjust('add',1,actor=customer)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code,302)

    def test_profile_entry_overview_counts_and_search(self):
        unknown_product = Product.objects.create(name='Stock à compter',price=10)
        Variant.objects.create(product=unknown_product,size='Unique',color='Noir',quantity=None)
        Product.objects.create(name='Sans variante',price=10)
        response = self.client.get(reverse('stocks:overview'))
        self.assertEqual(response.context['summary']['products'],3)
        self.assertEqual(response.context['summary']['available'],6)
        self.assertEqual(response.context['summary']['reserved'],2)
        self.assertEqual(response.context['summary']['to_count'],2)
        searched = self.client.get(reverse('stocks:overview'), {'q':'Polaire manuelle'})
        self.assertContains(searched,'Polaire manuelle')
        self.assertNotContains(searched,'Stock à compter')
        account=self.client.get(reverse('espace:account'))
        self.assertContains(account,'Stock · ajouter / retirer')
        self.assertContains(account,'<h3>Stock</h3>',html=True)


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True)
class ConcurrentManualAdjustmentTests(TransactionTestCase):
    def setUp(self):
        self.manager=get_user_model().objects.create_user('concurrent-stock-manager',is_staff=True)
        self.manager.user_permissions.add(Permission.objects.get(codename='manage_catalog'))
        self.manager.has_perm('core.manage_catalog')
        self.product=Product.objects.create(name='Dernière polaire',price=10)
        self.variant=Variant.objects.create(product=self.product,size='M',color='Bleu',quantity=1)

    def test_reservation_and_manual_removal_cannot_claim_the_same_last_unit(self):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL row locks required')
        barrier=Barrier(2)
        data=snapshot(self.variant)
        def attempt(operation):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                if operation=='reserve':
                    reserve('concurrent-manual-stock',None,data)
                else:
                    adjust_stock(self.product.pk,self.variant.pk,actor=self.manager,
                        operation='remove',amount=1,revision=0)
                return True
            except (StockUnavailable,AdjustmentError):
                return False
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes=list(executor.map(attempt,['reserve','remove']))
        self.assertEqual(sorted(outcomes),[False,True])
        self.variant.refresh_from_db()
        self.assertGreaterEqual(self.variant.quantity,self.variant.reserved)
        self.assertEqual(self.variant.available,0)
        self.assertEqual(Reservation.objects.filter(state='held').count()+LogEntry.objects.count(),1)
