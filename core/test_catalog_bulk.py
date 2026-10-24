from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from connexion.test_helpers import verify_test_user
from .catalog_bulk import (CatalogBulkConflict, apply_bulk_confirmation,
    prepare_bulk_preview, selection_snapshot)
from .catalog_bulk_forms import CatalogBulkForm
from .models import Category, Product, ProductPromotion
from stocks.models import Variant


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class CatalogBulkTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user('bulk-manager', is_staff=True)
        self.manager.groups.add(Group.objects.get(name='Gestion du catalogue'))
        verify_test_user(self.manager)
        self.category = Category.objects.create(name='Femmes', slug='femmes-bulk')
        self.other_category = Category.objects.create(name='Montagne', slug='montagne-bulk')
        self.extra_category = Category.objects.create(name='Cadeaux', slug='cadeaux-bulk')
        self.first = Product.objects.create(name='Polaire rouge', price=80, category=self.category, is_published=False, brand='Marque initiale', description='Une polaire rouge.', static_image='products/red.webp')
        self.second = Product.objects.create(name='Polaire verte', price=90, category=self.category, is_published=False, description='Une polaire verte.', static_image='products/green.webp')

    def products(self):
        return list(Product.objects.filter(pk__in=[self.first.pk, self.second.pk]).order_by('pk'))

    def preview(self, changes, products=None):
        products = self.products() if products is None else products
        return prepare_bulk_preview(self.manager, [item.pk for item in products], changes, selection_snapshot(self.manager, products))

    def login_manager(self):
        device = TOTPDevice.objects.create(user=self.manager, name='default', confirmed=True)
        self.client.force_login(self.manager, backend='connexion.backends.EmailOrUsernameBackend')
        session = self.client.session
        session['otp_device_id'] = device.persistent_id
        session.save()

    def test_preview_describes_actual_changes_without_writing(self):
        rows, token = self.preview({'brand': 'Peak Mountain', 'is_featured': True})
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['details'][0]['before'], 'Marque initiale')
        self.assertEqual(rows[0]['details'][0]['after'], 'Peak Mountain')
        self.first.refresh_from_db()
        self.assertEqual(self.first.brand, 'Marque initiale')
        self.assertFalse(self.first.is_featured)
        self.assertFalse(LogEntry.objects.filter(object_id=str(self.first.pk)).exists())
        self.assertTrue(token)

    def test_group_changes_leave_unselected_fields_intact_and_audit_each_change(self):
        original_descriptions = {product.pk: product.description for product in self.products()}
        _, token = self.preview({'brand': 'Peak Mountain', 'is_featured': True, 'availability': 'available'})
        self.assertEqual(apply_bulk_confirmation(self.manager, token), 2)
        for product in self.products():
            self.assertEqual(product.brand, 'Peak Mountain')
            self.assertTrue(product.is_featured)
            self.assertEqual(product.availability, 'available')
            self.assertFalse(product.is_published)
            self.assertEqual(product.description, original_descriptions[product.pk])
        self.assertEqual(LogEntry.objects.filter(change_message__startswith='Modification groupée.').count(), 2)

    def test_collections_add_remove_preserve_unrelated_and_primary_category(self):
        self.first.collections.add(self.category, self.extra_category)
        self.second.collections.add(self.extra_category)
        _, token = self.preview({'collections_add': [self.other_category.pk], 'collections_remove': [self.category.pk]})
        apply_bulk_confirmation(self.manager, token)
        for product in self.products():
            self.assertEqual(set(product.collections.values_list('pk', flat=True)), {self.extra_category.pk, self.other_category.pk})
            self.assertEqual(product.category_id, self.category.pk)

    def test_category_change_and_clearing_brand_are_explicit(self):
        _, token = self.preview({'category': self.other_category.pk, 'brand': '', 'article_type': 'polaires'})
        apply_bulk_confirmation(self.manager, token)
        for product in self.products():
            self.assertEqual(product.category_id, self.other_category.pk)
            self.assertEqual(product.brand, '')
            self.assertEqual(product.article_type, 'polaires')

    def test_price_change_records_history_preserves_stock_overrides_and_promotions(self):
        variant = Variant.objects.create(product=self.first, size='M', color='Rouge', price=45, quantity=7, reserved=1)
        inherited = Variant.objects.create(product=self.first, size='L', color='Rouge', quantity=2)
        promotion = ProductPromotion.objects.create(product=self.first, starts_at=timezone.now(), ends_at=timezone.now()+timedelta(days=2), kind='percent', value=10)
        history_count = self.first.price_observations.count()
        rows, token = self.preview({'price': '70.50'})
        self.assertEqual(rows[0]['specific_prices'], 1)
        apply_bulk_confirmation(self.manager, token)
        self.first.refresh_from_db()
        variant.refresh_from_db()
        inherited.refresh_from_db()
        promotion.refresh_from_db()
        self.assertEqual(self.first.price, Decimal('70.50'))
        self.assertEqual(self.first.price_observations.count(), history_count + 2)
        self.assertEqual(inherited.price_observations.latest('pk').price, Decimal('70.50'))
        self.assertEqual((variant.price, variant.quantity, variant.reserved), (Decimal('45'), 7, 1))
        self.assertIsNone(inherited.price)
        self.assertEqual(promotion.value, Decimal('10'))
        self.assertTrue(promotion.enabled)

    def test_publishing_checks_every_product_before_any_write(self):
        self.second.description = ''
        self.second.save(update_fields=['description'])
        with self.assertRaisesMessage(ValidationError, 'une description'):
            self.preview({'is_published': True, 'brand': 'Nouvelle'})
        self.first.refresh_from_db()
        self.assertFalse(self.first.is_published)
        self.assertEqual(self.first.brand, 'Marque initiale')

    def test_publish_valid_products_and_unpublish_incomplete_ones(self):
        _, token = self.preview({'is_published': True})
        self.assertEqual(apply_bulk_confirmation(self.manager, token), 2)
        self.second.description = ''
        self.second.save(update_fields=['description'])
        _, token = self.preview({'is_published': False})
        self.assertEqual(apply_bulk_confirmation(self.manager, token), 2)
        self.assertFalse(Product.objects.filter(pk__in=[self.first.pk, self.second.pk], is_published=True).exists())

    def test_publication_requires_photo_category_and_positive_price(self):
        for field, value, expected in [('static_image', '', 'une photo principale'), ('category_id', None, 'une rubrique principale'), ('price', 0, 'un prix supérieur à zéro')]:
            fresh = Product.objects.get(pk=self.first.pk)
            setattr(fresh, field, value)
            # Deliberately simulate a malformed imported row without recording
            # an invalid zero-price observation through Product.save().
            Product.objects.filter(pk=fresh.pk).update(**{field: value})
            fresh.refresh_from_db()
            with self.subTest(field=field), self.assertRaisesMessage(ValidationError, expected):
                self.preview({'is_published': True}, [fresh])
            fresh.static_image = 'products/red.webp'
            fresh.category = self.category
            fresh.price = Decimal('80')
            fresh.save()

    def test_stale_product_or_collections_prevent_all_changes(self):
        for mutation in ('product', 'collections'):
            _, token = self.preview({'brand': 'Nouvelle'})
            if mutation == 'product':
                Product.objects.filter(pk=self.second.pk).update(description='Correction récente')
            else:
                self.second.collections.add(self.extra_category)
            with self.subTest(mutation=mutation), self.assertRaises(CatalogBulkConflict):
                apply_bulk_confirmation(self.manager, token)
            self.first.refresh_from_db()
            self.assertEqual(self.first.brand, 'Marque initiale')

    def test_stale_variant_price_is_detected_but_stock_change_is_not_a_blocker(self):
        variant = Variant.objects.create(product=self.first, size='M', color='Rouge', quantity=4)
        _, token = self.preview({'price': '70'})
        variant.price = Decimal('55')
        variant.save(update_fields=['price'])
        with self.assertRaises(CatalogBulkConflict):
            apply_bulk_confirmation(self.manager, token)
        _, token = self.preview({'price': '70'})
        variant.quantity = 3
        variant.save(update_fields=['quantity'])
        self.assertEqual(apply_bulk_confirmation(self.manager, token), 2)
        variant.refresh_from_db()
        self.assertEqual(variant.quantity, 3)

    def test_category_removed_or_renamed_after_preview_prevents_writes(self):
        _, token = self.preview({'category': self.other_category.pk})
        self.other_category.name = 'Nouveau nom'
        self.other_category.save(update_fields=['name'])
        with self.assertRaises(CatalogBulkConflict):
            apply_bulk_confirmation(self.manager, token)
        self.first.refresh_from_db()
        self.assertEqual(self.first.category_id, self.category.pk)

    def test_selection_rejects_empty_duplicate_unknown_archived_and_over_limit(self):
        products = self.products()
        snapshot = selection_snapshot(self.manager, products)
        for ids in ([], [self.first.pk, self.first.pk], [99999999], [str(i+1) for i in range(101)], ['x'], [True]):
            with self.subTest(ids=ids), self.assertRaises(ValidationError):
                prepare_bulk_preview(self.manager, ids, {'brand': 'Nouvelle'}, snapshot)
        self.second.duplicate_of = self.first
        self.second.save(update_fields=['duplicate_of'])
        with self.assertRaises(CatalogBulkConflict):
            prepare_bulk_preview(self.manager, [self.first.pk, self.second.pk], {'brand': 'Nouvelle'}, snapshot)

    def test_selection_snapshot_cannot_be_reused_for_another_selection(self):
        snapshot = selection_snapshot(self.manager, [self.first])
        with self.assertRaises(CatalogBulkConflict):
            prepare_bulk_preview(self.manager, [self.second.pk], {'brand': 'Nouvelle'}, snapshot)

    def test_expired_tampered_or_another_actor_token_is_rejected(self):
        _, token = self.preview({'brand': 'Nouvelle'})
        other = get_user_model().objects.create_user('other-bulk-manager', is_staff=True)
        other.groups.add(Group.objects.get(name='Gestion du catalogue'))
        verify_test_user(other)
        with self.assertRaises(CatalogBulkConflict):
            apply_bulk_confirmation(other, token)
        with self.assertRaises(CatalogBulkConflict):
            apply_bulk_confirmation(self.manager, token + 'x')
        with patch('core.catalog_bulk.signing.loads', side_effect=signing.SignatureExpired('expired')):
            with self.assertRaises(CatalogBulkConflict):
                apply_bulk_confirmation(self.manager, token)

    def test_service_denies_non_catalog_staff_and_unverified_user(self):
        user = get_user_model().objects.create_user('bulk-outsider', is_staff=True)
        verify_test_user(user)
        _, token = self.preview({'brand': 'Nouvelle'})
        with self.assertRaises(PermissionDenied):
            apply_bulk_confirmation(user, token)
        with patch('core.catalog_bulk.email_access_allowed', return_value=False):
            with self.assertRaises(PermissionDenied):
                apply_bulk_confirmation(self.manager, token)

    def test_replay_cannot_apply_old_values_again_and_noop_has_no_audit(self):
        _, token = self.preview({'brand': 'Nouvelle'})
        apply_bulk_confirmation(self.manager, token)
        with self.assertRaises(CatalogBulkConflict):
            apply_bulk_confirmation(self.manager, token)
        audit_count = LogEntry.objects.count()
        _, noop = self.preview({'brand': 'Nouvelle'})
        self.assertEqual(apply_bulk_confirmation(self.manager, noop), 0)
        self.assertEqual(LogEntry.objects.count(), audit_count)

    def test_audit_failure_rolls_back_products_collections_and_price_history(self):
        before = self.first.price_observations.count()
        _, token = self.preview({'brand': 'Nouvelle', 'price': '60', 'collections_add': [self.other_category.pk]})
        with patch('core.catalog_bulk.LogEntry.objects.log_actions', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                apply_bulk_confirmation(self.manager, token)
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertEqual(self.first.brand, 'Marque initiale')
        self.assertEqual(self.first.price, Decimal('80'))
        self.assertEqual(self.second.price, Decimal('90'))
        self.assertEqual(self.first.price_observations.count(), before)
        self.assertFalse(self.first.collections.exists())

    def test_form_only_applies_checked_fields_and_accepts_localized_price(self):
        form = CatalogBulkForm({'change_brand': '1', 'brand': 'Peak Mountain', 'price': 'invalid', 'category': '99999'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.changes(), {'brand': 'Peak Mountain'})
        form = CatalogBulkForm({'change_price': '1', 'price': '79,90'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.changes(), {'price': '79.90'})

    def test_form_rejects_no_change_missing_values_conflicting_collections_and_invalid_price(self):
        for data in ({}, {'change_price': '1'}, {'change_price': '1', 'price': '0'}, {'change_category': '1'}, {'change_is_published': '1'}, {'collections_add': [self.category.pk], 'collections_remove': [self.category.pk]}):
            with self.subTest(data=data):
                self.assertFalse(CatalogBulkForm(data).is_valid())

    def test_view_requires_selection_and_explicit_confirmation(self):
        self.login_manager()
        url = reverse('core:catalog_bulk')
        response = self.client.post(url, {'selected_products': [self.first.pk, self.second.pk]})
        self.assertEqual(response.status_code, 200)
        snapshot = response.context['selection_snapshot']
        response = self.client.post(url, {'action': 'preview', 'selected_products': [self.first.pk, self.second.pk], 'selection_snapshot': snapshot, 'change_brand': '1', 'brand': 'Peak Mountain'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Appliquer à 2 produits')
        self.first.refresh_from_db()
        self.assertEqual(self.first.brand, 'Marque initiale')
        token = response.context['confirmation']
        back = self.client.post(url, {'action': 'back', 'confirmation': token})
        self.assertEqual(back.status_code, 200)
        self.assertEqual(back.context['form']['brand'].value(), 'Peak Mountain')
        response = self.client.post(url, {'action': 'apply', 'confirmation': token, 'brand': 'Tampered', 'selected_products': [999999]})
        self.assertRedirects(response, reverse('core:catalog_list'))
        self.first.refresh_from_db()
        self.assertEqual(self.first.brand, 'Peak Mountain')

    def test_view_conflict_and_csrf_do_not_change_products(self):
        self.login_manager()
        _, token = self.preview({'brand': 'Nouvelle'})
        Product.objects.filter(pk=self.first.pk).update(description='Dernière correction')
        response = self.client.post(reverse('core:catalog_bulk'), {'action': 'apply', 'confirmation': token})
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, 'Aucune modification', status_code=409)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.cookies = self.client.cookies
        response = csrf_client.post(reverse('core:catalog_bulk'), {'action': 'apply', 'confirmation': token})
        self.assertEqual(response.status_code, 403)
        self.first.refresh_from_db()
        self.assertEqual(self.first.brand, 'Marque initiale')
