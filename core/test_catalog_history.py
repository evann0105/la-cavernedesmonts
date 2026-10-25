from copy import deepcopy
from decimal import Decimal
from unittest.mock import patch

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.utils import timezone

from connexion.test_helpers import verify_test_user
from stocks.models import Variant
from .catalog_bulk import apply_bulk_confirmation, prepare_bulk_preview, selection_snapshot
from .catalog_history import (CatalogUndoConflict, UNDO_SALT, apply_catalog_undo,
                              history_rows, prepare_catalog_undo)
from .models import CatalogChange, Category, PriceObservation, Product, ProductPromotion


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class CatalogHistoryTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user('history-manager', is_staff=True)
        self.manager.groups.add(Group.objects.get(name='Gestion du catalogue'))
        verify_test_user(self.manager)
        self.category = Category.objects.create(name='Montagne', slug='montagne-history')
        self.other = Category.objects.create(name='Collection', slug='collection-history')
        self.first = Product.objects.create(name='Polaire rouge', price='80.00', category=self.category,
            brand='Marque initiale', description='Description vérifiée', static_image='products/red.webp', is_published=False)
        self.second = Product.objects.create(name='Polaire verte', price='90.00', category=self.category,
            brand='Autre marque', description='Description verte', static_image='products/green.webp', is_published=False)

    def apply(self, changes, products=None):
        if products is None:
            products = list(Product.objects.filter(pk__in=[self.first.pk, self.second.pk]).order_by('pk'))
        _, confirmation = prepare_bulk_preview(self.manager, [product.pk for product in products], changes,
            selection_snapshot(self.manager, products))
        count = apply_bulk_confirmation(self.manager, confirmation)
        return count, CatalogChange.objects.filter(kind='bulk').first()

    def undo(self, change, actor=None):
        actor = actor or self.manager
        _, _, token = prepare_catalog_undo(actor, change.pk)
        return apply_catalog_undo(actor, token, expected_change_id=change.pk)

    def test_history_records_actual_fields_per_product_with_stable_labels(self):
        count, change = self.apply({'brand': 'Marque initiale', 'price': '80.00'})
        self.assertEqual(count, 1)
        self.assertEqual(change.product_count, 1)
        self.assertEqual(change.actor, self.manager)
        self.assertEqual(change.actor_label, 'history-manager')
        self.assertEqual(change.items[0]['product_id'], self.second.pk)
        self.assertEqual(change.items[0]['changes']['price']['before'], '90.00')
        self.assertEqual(change.items[0]['changes']['price']['after'], '80.00')
        Product.objects.filter(pk=self.second.pk).update(name='Nouveau nom')
        self.assertEqual(history_rows(change)[0]['product_name'], 'Polaire verte')
        self.assertEqual(history_rows(change)[0]['details'][1]['before'], '90,00 €')

    def test_no_op_creates_no_history(self):
        self.assertEqual(self.apply({'brand': 'Marque initiale'}, [self.first]), (0, None))
        self.assertEqual(CatalogChange.objects.count(), 0)

    def test_history_is_rolled_back_if_product_write_fails(self):
        with patch('core.models.Product.save', side_effect=RuntimeError('write failed')):
            with self.assertRaises(RuntimeError):
                self.apply({'brand': 'Nouvelle'})
        self.assertEqual(CatalogChange.objects.count(), 0)
        self.first.refresh_from_db()
        self.assertEqual(self.first.brand, 'Marque initiale')

    def test_preview_is_read_only_and_describes_current_to_restored_values(self):
        _, change = self.apply({'brand': 'Nouvelle', 'price': '60.00'})
        result, rows, token = prepare_catalog_undo(self.manager, change.pk)
        self.assertEqual(result.pk, change.pk)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['details'][0]['before'], 'Nouvelle')
        self.assertEqual(rows[0]['details'][0]['after'], 'Marque initiale')
        self.assertTrue(token)
        self.first.refresh_from_db()
        self.assertEqual(self.first.price, Decimal('60.00'))
        self.assertEqual(CatalogChange.objects.count(), 1)
        self.assertIsNone(CatalogChange.objects.get(pk=change.pk).undone_at)

    def test_undo_restores_batch_values_and_keeps_unrelated_later_edits(self):
        _, change = self.apply({'brand': 'Nouvelle', 'is_featured': True})
        Product.objects.filter(pk=self.first.pk).update(description='Texte corrigé ensuite', name='Polaire renommée')
        original, count, repeated = self.undo(change)
        self.assertEqual(count, 2)
        self.assertFalse(repeated)
        self.assertIsNotNone(original.undone_at)
        self.first.refresh_from_db(); self.second.refresh_from_db()
        self.assertEqual(self.first.brand, 'Marque initiale')
        self.assertEqual(self.second.brand, 'Autre marque')
        self.assertFalse(self.first.is_featured)
        self.assertEqual(self.first.name, 'Polaire renommée')
        self.assertEqual(self.first.description, 'Texte corrigé ensuite')
        inverse = CatalogChange.objects.get(kind='undo')
        self.assertEqual(inverse.reverses_id, change.pk)
        self.assertEqual(inverse.items[0]['changes']['brand']['before'], 'Nouvelle')
        self.assertEqual(inverse.items[0]['changes']['brand']['after'], 'Marque initiale')
        self.assertEqual(LogEntry.objects.filter(change_message__startswith='Annulation de la modification groupée').count(), 2)

    def test_price_undo_records_new_price_observation_not_a_rewritten_past(self):
        initial = list(PriceObservation.objects.filter(product=self.first).values_list('pk', 'price'))
        _, change = self.apply({'price': '60.00'})
        self.undo(change)
        observations = list(PriceObservation.objects.filter(product=self.first).values_list('pk', 'price'))
        self.assertEqual(observations[:len(initial)], initial)
        self.assertEqual([price for _, price in observations], [Decimal('80.00'), Decimal('60.00'), Decimal('80.00')])

    def test_stock_variant_prices_and_promotions_are_preserved(self):
        variant = Variant.objects.create(product=self.first, size='M', color='Rouge', quantity=5, reserved=1, price='70.00')
        promo = ProductPromotion.objects.create(product=self.first, starts_at=timezone.now(),
            ends_at=timezone.now()+timezone.timedelta(days=1), kind='percent', value='10')
        _, change = self.apply({'price': '60.00'})
        Variant.objects.filter(pk=variant.pk).update(quantity=3, reserved=0, price='65.00')
        self.undo(change)
        variant.refresh_from_db(); promo.refresh_from_db()
        self.assertEqual((variant.quantity, variant.reserved, variant.price), (3, 0, Decimal('65.00')))
        self.assertEqual(promo.value, Decimal('10'))
        self.assertTrue(promo.enabled)

    def test_collections_are_restored_without_affecting_primary_category(self):
        self.first.collections.add(self.category)
        self.second.collections.add(self.other)
        _, change = self.apply({'collections_add': [self.other.pk], 'collections_remove': [self.category.pk]})
        self.assertEqual(change.product_count, 1)
        self.undo(change)
        self.assertEqual(list(self.first.collections.values_list('pk', flat=True)), [self.category.pk])
        self.assertEqual(list(self.second.collections.values_list('pk', flat=True)), [self.other.pk])
        self.first.refresh_from_db()
        self.assertEqual(self.first.category_id, self.category.pk)

    def test_concurrent_changed_field_blocks_whole_batch(self):
        _, change = self.apply({'brand': 'Nouvelle'})
        _, _, token = prepare_catalog_undo(self.manager, change.pk)
        Product.objects.filter(pk=self.second.pk).update(brand='Correction après aperçu')
        with self.assertRaises(CatalogUndoConflict):
            apply_catalog_undo(self.manager, token)
        self.first.refresh_from_db(); self.second.refresh_from_db()
        self.assertEqual(self.first.brand, 'Nouvelle')
        self.assertEqual(self.second.brand, 'Correction après aperçu')
        change.refresh_from_db()
        self.assertIsNone(change.undone_at)
        self.assertEqual(CatalogChange.objects.count(), 1)

    def test_later_collection_edits_are_not_overwritten(self):
        _, change = self.apply({'collections_add': [self.other.pk]})
        self.first.collections.add(self.category)
        with self.assertRaises(CatalogUndoConflict):
            prepare_catalog_undo(self.manager, change.pk)
        self.assertCountEqual(self.first.collections.values_list('pk', flat=True), [self.other.pk, self.category.pk])

    def test_deleted_or_duplicate_product_prevents_undo(self):
        _, change = self.apply({'brand': 'Nouvelle'})
        Product.objects.filter(pk=self.first.pk).update(duplicate_of=self.second)
        with self.assertRaises(CatalogUndoConflict):
            prepare_catalog_undo(self.manager, change.pk)
        self.first.delete()
        with self.assertRaises(CatalogUndoConflict):
            prepare_catalog_undo(self.manager, change.pk)
        self.assertEqual(history_rows(change)[0]['product_name'], 'Polaire rouge')

    def test_category_restoration_requires_existing_active_category(self):
        _, change = self.apply({'category': self.other.pk})
        self.category.archived_at = timezone.now(); self.category.save()
        with self.assertRaises(CatalogUndoConflict):
            prepare_catalog_undo(self.manager, change.pk)
        self.category.delete()
        with self.assertRaises(CatalogUndoConflict):
            prepare_catalog_undo(self.manager, change.pk)

    def test_collection_restoration_refuses_reintroducing_archived_category(self):
        self.first.collections.add(self.other)
        _, change = self.apply({'collections_remove': [self.other.pk]}, [self.first])
        self.other.archived_at = timezone.now(); self.other.save()
        with self.assertRaises(CatalogUndoConflict):
            prepare_catalog_undo(self.manager, change.pk)

    def test_unchanged_archived_category_does_not_block_brand_undo(self):
        _, change = self.apply({'brand': 'Nouvelle'})
        self.category.archived_at = timezone.now(); self.category.save()
        self.undo(change)
        self.first.refresh_from_db()
        self.assertEqual(self.first.brand, 'Marque initiale')
        self.assertEqual(self.first.category_id, self.category.pk)

    def test_undo_can_remove_newly_archived_collection_without_reintroducing_it(self):
        _, change = self.apply({'collections_add': [self.other.pk]})
        self.other.archived_at = timezone.now(); self.other.save()
        self.undo(change)
        self.assertFalse(self.first.collections.exists())

    def test_target_category_rename_between_preview_and_apply_requires_new_preview(self):
        _, change = self.apply({'category': self.other.pk})
        _, _, token = prepare_catalog_undo(self.manager, change.pk)
        Category.objects.filter(pk=self.category.pk).update(name='Nouveau nom')
        with self.assertRaises(CatalogUndoConflict):
            apply_catalog_undo(self.manager, token)
        self.first.refresh_from_db()
        self.assertEqual(self.first.category_id, self.other.pk)

    def test_category_renamed_before_preview_uses_current_names_without_rewriting_original_history(self):
        _, change = self.apply({'category': self.other.pk}, [self.first])
        recorded = deepcopy(change.items)
        Category.objects.filter(pk=self.category.pk).update(name='Montagne renommée')
        Category.objects.filter(pk=self.other.pk).update(name='Collection actuelle')
        prepared, rows, token = prepare_catalog_undo(self.manager, change.pk)
        detail = rows[0]['details'][0]
        self.assertEqual((detail['before'], detail['after']), ('Collection actuelle', 'Montagne renommée'))
        self.assertEqual(prepared.items, recorded)
        self.assertEqual(history_rows(prepared)[0]['details'][0]['before'], 'Montagne')
        apply_catalog_undo(self.manager, token)
        self.first.refresh_from_db()
        self.assertEqual(self.first.category_id, self.category.pk)
        inverse = CatalogChange.objects.get(reverses=change).items[0]['changes']['category']
        self.assertEqual((inverse['before_label'], inverse['after_label']), ('Collection actuelle', 'Montagne renommée'))
        self.assertEqual((inverse['before'], inverse['after']), (self.other.pk, self.category.pk))
        self.assertTrue(LogEntry.objects.filter(change_message__contains='Collection actuelle → Montagne renommée').exists())
        change.refresh_from_db()
        self.assertEqual(change.items, recorded)

    def test_collections_renamed_before_preview_use_current_names_in_preview_and_undo_history(self):
        retained = Category.objects.create(name='Collection conservée', slug='retained-history')
        self.first.collections.add(self.category, retained)
        _, change = self.apply({'collections_add': [self.other.pk], 'collections_remove': [self.category.pk]}, [self.first])
        recorded = deepcopy(change.items)
        Category.objects.filter(pk=self.category.pk).update(name='Ancienne rubrique renommée')
        Category.objects.filter(pk=self.other.pk).update(name='Nouvelle rubrique renommée')
        Category.objects.filter(pk=retained.pk).update(name='Rubrique conservée renommée')
        prepared, rows, token = prepare_catalog_undo(self.manager, change.pk)
        expected_before = 'Nouvelle rubrique renommée, Rubrique conservée renommée'
        expected_after = 'Ancienne rubrique renommée, Rubrique conservée renommée'
        detail = rows[0]['details'][0]
        self.assertEqual((detail['before'], detail['after']), (expected_before, expected_after))
        self.assertEqual(prepared.items, recorded)
        apply_catalog_undo(self.manager, token)
        self.assertCountEqual(self.first.collections.values_list('pk', flat=True), [self.category.pk, retained.pk])
        inverse = CatalogChange.objects.get(reverses=change).items[0]['changes']['collections']
        self.assertEqual((inverse['before_label'], inverse['after_label']), (expected_before, expected_after))
        self.assertTrue(LogEntry.objects.filter(change_message__contains=f'{expected_before} → {expected_after}').exists())
        change.refresh_from_db()
        self.assertEqual(change.items, recorded)

    def test_restore_to_published_validates_current_content(self):
        self.first.is_published=True; self.first.save()
        _, change = self.apply({'is_published': False}, [self.first])
        Product.objects.filter(pk=self.first.pk).update(description='')
        with self.assertRaises(CatalogUndoConflict):
            self.undo(change)
        self.first.refresh_from_db()
        self.assertFalse(self.first.is_published)

    def test_restore_nullable_primary_category_on_a_draft(self):
        self.first.category=None; self.first.save()
        _, change = self.apply({'category': self.other.pk}, [self.first])
        self.undo(change)
        self.first.refresh_from_db()
        self.assertIsNone(self.first.category_id)

    def test_double_submit_is_idempotent_and_does_not_undo_subsequent_work(self):
        _, change = self.apply({'brand': 'Nouvelle'})
        _, _, token = prepare_catalog_undo(self.manager, change.pk)
        self.assertFalse(apply_catalog_undo(self.manager, token)[2])
        Product.objects.filter(pk=self.first.pk).update(brand='Travail suivant')
        _, count, repeated = apply_catalog_undo(self.manager, token)
        self.assertEqual(count, 0); self.assertTrue(repeated)
        self.first.refresh_from_db()
        self.assertEqual(self.first.brand, 'Travail suivant')
        self.assertEqual(CatalogChange.objects.filter(kind='undo').count(), 1)
        with self.assertRaises(CatalogUndoConflict):
            prepare_catalog_undo(self.manager, change.pk)

    def test_inverse_operation_cannot_itself_be_undone(self):
        _, change = self.apply({'brand': 'Nouvelle'})
        self.undo(change)
        with self.assertRaises(CatalogUndoConflict):
            prepare_catalog_undo(self.manager, CatalogChange.objects.get(kind='undo').pk)

    def test_undo_requires_catalogue_rights_and_confirmed_email_access(self):
        _, change = self.apply({'brand': 'Nouvelle'})
        _, _, token = prepare_catalog_undo(self.manager, change.pk)
        customer = get_user_model().objects.create_user('history-customer')
        for actor in (None, customer):
            with self.assertRaises(PermissionDenied):
                prepare_catalog_undo(actor, change.pk)
            with self.assertRaises(PermissionDenied):
                apply_catalog_undo(actor, token)
        with patch('core.catalog_history.email_access_allowed', return_value=False):
            with self.assertRaises(PermissionDenied):
                apply_catalog_undo(self.manager, token)

    def test_confirmation_is_actor_bound_and_route_bound(self):
        _, change = self.apply({'brand': 'Nouvelle'})
        _, _, token = prepare_catalog_undo(self.manager, change.pk)
        other_manager = get_user_model().objects.create_user('other-manager', is_staff=True)
        other_manager.groups.add(Group.objects.get(name='Gestion du catalogue')); verify_test_user(other_manager)
        with self.assertRaises(CatalogUndoConflict):
            apply_catalog_undo(other_manager, token)
        with self.assertRaises(CatalogUndoConflict):
            apply_catalog_undo(self.manager, token, expected_change_id=change.pk + 1)
        _, _, own_token = prepare_catalog_undo(other_manager, change.pk)
        self.assertEqual(apply_catalog_undo(other_manager, own_token)[1], 2)

    def test_expired_tampered_and_malformed_confirmations_are_rejected(self):
        _, change = self.apply({'brand': 'Nouvelle'})
        _, _, token = prepare_catalog_undo(self.manager, change.pk)
        for bad in ('', None, token+'x', 'x'*100001, signing.dumps([], salt=UNDO_SALT)):
            with self.subTest(token=type(bad).__name__), self.assertRaises(CatalogUndoConflict):
                apply_catalog_undo(self.manager, bad)
        with patch('django.core.signing.time.time', return_value=timezone.now().timestamp()+7201):
            with self.assertRaises(CatalogUndoConflict):
                apply_catalog_undo(self.manager, token)

    def test_history_mutation_after_preview_is_rejected(self):
        _, change = self.apply({'brand': 'Nouvelle'})
        _, _, token = prepare_catalog_undo(self.manager, change.pk)
        altered = deepcopy(change.items); altered[0]['changes']['brand']['before']='Altérée'
        CatalogChange.objects.filter(pk=change.pk).update(items=altered)
        with self.assertRaises(CatalogUndoConflict):
            apply_catalog_undo(self.manager, token)

    def test_failure_in_later_undo_write_rolls_back_all_products_and_history(self):
        _, change = self.apply({'brand': 'Nouvelle'})
        _, _, token = prepare_catalog_undo(self.manager, change.pk)
        original_save = Product.save
        def save(product, *args, **kwargs):
            if product.pk == self.second.pk:
                raise RuntimeError('write failed')
            return original_save(product, *args, **kwargs)
        with patch.object(Product, 'save', save), self.assertRaises(RuntimeError):
            apply_catalog_undo(self.manager, token)
        self.first.refresh_from_db(); change.refresh_from_db()
        self.assertEqual(self.first.brand, 'Nouvelle')
        self.assertIsNone(change.undone_at)
        self.assertEqual(CatalogChange.objects.count(), 1)
        self.assertFalse(LogEntry.objects.filter(change_message__startswith='Annulation').exists())

    def test_deleted_actor_leaves_stable_history_identity(self):
        _, change = self.apply({'brand': 'Nouvelle'})
        self.manager.delete()
        change.refresh_from_db()
        self.assertIsNone(change.actor_id)
        self.assertEqual(change.actor_label, 'history-manager')
