from unittest.mock import patch
from urllib.parse import urlencode

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import Client, RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from connexion.test_helpers import verify_test_user
from .catalog_bulk import apply_bulk_confirmation, prepare_bulk_preview, selection_snapshot
from .catalog_bulk_forms import CatalogBulkForm
from .catalog_forms import CatalogProductForm
from .category_navigation import category_navigation
from .category_services import (CategoryConflict, CategoryError, category_order_snapshot,
    move_category, save_category, set_category_archived, set_category_visibility)
from .models import Category, Product
from .seo_views import sitemap_paths
from stocks.models import Variant


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=False, EMAIL_DELIVERY_ENABLED=False,
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class CategoryArchivalTests(TestCase):
    backend = 'connexion.backends.EmailOrUsernameBackend'

    def setUp(self):
        Category.objects.all().delete()
        self.manager = get_user_model().objects.create_user('archive-manager', email='archive@example.test', is_staff=True)
        self.manager.user_permissions.add(Permission.objects.get(content_type__app_label='core', codename='manage_catalog'))
        verify_test_user(self.manager)
        self.client.force_login(self.manager, backend=self.backend)
        self.category = Category.objects.create(name='Collection hiver', slug='collection-hiver', show_in_menu=True, position=10)
        self.other = Category.objects.create(name='Collection été', slug='collection-ete', position=20)
        self.product = Product.objects.create(name='Polaire exemple', price='45.00', brand='Peak Mountain', category=self.category,
            description='Une polaire.', static_image='products/polaire.webp', is_published=True)
        self.product.refresh_from_db()
        self.product.collections.add(self.category, self.other)
        self.variant = Variant.objects.create(product=self.product, size='M', color='Vert', quantity=6, reserved=1)

    def url(self, action, category=None):
        return reverse('core:category_' + action, kwargs={'pk': (category or self.category).pk})

    def archive(self):
        row = set_category_archived(self.category.pk, self.category.revision, True, self.manager)
        self.category.refresh_from_db()
        return row

    def test_archive_preserves_every_product_membership_and_stock(self):
        before = {field.attname: field.value_from_object(self.product) for field in Product._meta.concrete_fields}
        row = self.archive()
        self.assertIsNotNone(row.archived_at)
        self.assertFalse(row.show_in_menu)
        self.assertEqual(row.revision, 2)
        self.assertEqual(row.slug, 'collection-hiver')
        self.product.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual({field.attname: field.value_from_object(self.product) for field in Product._meta.concrete_fields}, before)
        self.assertCountEqual(self.product.collections.all(), [self.category, self.other])
        self.assertEqual((self.variant.quantity, self.variant.reserved), (6, 1))
        self.assertIn('Rubrique archivée', LogEntry.objects.latest('pk').change_message)

    def test_restore_preserves_url_and_requires_explicit_menu_publication(self):
        self.archive()
        row = set_category_archived(self.category.pk, self.category.revision, False, self.manager)
        self.assertIsNone(row.archived_at)
        self.assertFalse(row.show_in_menu)
        self.assertEqual(row.revision, 3)
        self.assertEqual(row.slug, 'collection-hiver')
        set_category_visibility(row.pk, row.revision, True, self.manager)
        row.refresh_from_db()
        self.assertTrue(row.show_in_menu)

    def test_archived_rubric_disappears_from_public_choices_but_shared_url_stays_valid(self):
        self.archive()
        self.assertNotIn(self.category, category_navigation(RequestFactory().get('/'))['navigation_categories'])
        response = self.client.get(reverse('core:product_list'), {'categorie': self.category.slug})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.product.name)
        self.assertEqual(response.context['selected_category'], self.category)
        self.assertNotIn(self.category, list(response.context['categories']))
        paths = list(sitemap_paths())
        self.assertNotIn(reverse('core:product_list') + '?' + urlencode({'categorie': self.category.slug}), paths)
        self.assertIn(reverse('core:product_detail', kwargs={'slug': self.product.slug}), paths)

    def test_archive_confirmation_get_is_read_only_and_explains_publication(self):
        before = LogEntry.objects.count()
        response = self.client.get(self.url('archive'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Les articles déjà publiés restent en vente.')
        self.assertContains(response, 'Archiver la rubrique')
        self.assertEqual(response.context['product_count'], 1)
        self.category.refresh_from_db()
        self.assertIsNone(self.category.archived_at)
        self.assertEqual(LogEntry.objects.count(), before)

    def test_post_archive_and_restore_go_to_the_corresponding_list(self):
        response = self.client.post(self.url('archive'), {'revision': 1})
        self.assertRedirects(response, reverse('core:category_list') + '?scope=archived#rubrique-' + str(self.category.pk), fetch_redirect_response=False)
        self.category.refresh_from_db()
        response = self.client.get(self.url('restore'))
        self.assertContains(response, 'Elle restera hors du menu.')
        self.assertIsNotNone(Category.objects.get(pk=self.category.pk).archived_at)
        response = self.client.post(self.url('restore'), {'revision': self.category.revision})
        self.assertRedirects(response, reverse('core:category_list') + '#rubrique-' + str(self.category.pk), fetch_redirect_response=False)
        self.assertFalse(Category.objects.get(pk=self.category.pk).show_in_menu)

    def test_active_and_archive_lists_count_products_once_and_offer_correct_actions(self):
        self.archive()
        active = self.client.get(reverse('core:category_list'))
        archived = self.client.get(reverse('core:category_list'), {'scope': 'archived'})
        self.assertEqual([row.pk for row in active.context['categories']], [self.other.pk])
        self.assertEqual([row.pk for row in archived.context['categories']], [self.category.pk])
        self.assertEqual(archived.context['categories'][0].product_count, 1)
        self.assertEqual((archived.context['active_count'], archived.context['archive_count']), (1, 1))
        self.assertContains(archived, 'Restaurer la rubrique')
        self.assertNotContains(archived, 'aria-label="Monter : Collection hiver"')
        self.assertNotContains(archived, 'aria-label="Afficher dans le menu : Collection hiver"')
        self.assertNotContains(archived, 'aria-label="Modifier : Collection hiver"')
        self.assertIn('no-store', archived['Cache-Control'])
        self.assertIn('noindex', archived['X-Robots-Tag'])

    def test_stale_archive_or_restore_never_overwrites_other_changes(self):
        set_category_visibility(self.category.pk, 1, False, self.manager)
        with self.assertRaises(CategoryConflict):
            set_category_archived(self.category.pk, 1, True, self.manager)
        self.assertEqual(self.client.post(self.url('archive'), {'revision': 1}).status_code, 409)
        self.category.refresh_from_db()
        self.archive()
        with self.assertRaises(CategoryConflict):
            set_category_archived(self.category.pk, 2, False, self.manager)
        self.assertIsNotNone(Category.objects.get(pk=self.category.pk).archived_at)

    def test_already_performed_and_invalid_operations_are_rejected(self):
        for value in (None, '1', 1):
            with self.assertRaises(CategoryError):
                set_category_archived(self.category.pk, 1, value, self.manager)
        for revision in (None, True, 0, '1'):
            with self.assertRaises(CategoryError):
                set_category_archived(self.category.pk, revision, True, self.manager)
        self.archive()
        with self.assertRaises(CategoryConflict):
            set_category_archived(self.category.pk, self.category.revision, True, self.manager)

    def test_archive_is_protected_by_permissions_csrf_and_required_revision(self):
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.manager, backend=self.backend)
        self.assertEqual(csrf.post(self.url('archive'), {'revision': 1}).status_code, 403)
        self.assertEqual(self.client.post(self.url('archive'), {}).status_code, 400)
        self.assertEqual(self.client.put(self.url('archive'), {}).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.get(self.url('archive')).status_code, 302)
        user = get_user_model().objects.create_user('archive-customer', is_staff=True)
        verify_test_user(user)
        self.client.force_login(user, backend=self.backend)
        self.assertEqual(self.client.post(self.url('archive'), {'revision': 1}).status_code, 403)
        with self.assertRaises(PermissionDenied):
            set_category_archived(self.category.pk, 1, True, user)
        self.assertIsNone(Category.objects.get(pk=self.category.pk).archived_at)

    def test_archived_rubric_cannot_be_edited_reordered_or_shown_without_restoring(self):
        self.archive()
        with self.assertRaises(CategoryError):
            set_category_visibility(self.category.pk, self.category.revision, True, self.manager)
        with self.assertRaises(CategoryError):
            save_category(self.category.pk, {'name': 'Autre nom', 'menu_label': '', 'show_in_menu': False, 'revision': self.category.revision}, self.manager)
        token = category_order_snapshot(list(Category.objects.filter(archived_at__isnull=True)), self.manager)
        with self.assertRaises(CategoryConflict):
            move_category(self.category.pk, 'up', token, self.manager)
        self.assertRedirects(self.client.get(self.url('edit')), self.url('restore'), fetch_redirect_response=False)

    def test_order_snapshot_becomes_stale_after_archive_and_active_moves_skip_archives(self):
        third = Category.objects.create(name='Collection automne', slug='automne', position=30)
        old = category_order_snapshot(list(Category.objects.all()), self.manager)
        self.archive()
        with self.assertRaises(CategoryConflict):
            move_category(third.pk, 'up', old, self.manager)
        active = list(Category.objects.filter(archived_at__isnull=True))
        move_category(third.pk, 'up', category_order_snapshot(active, self.manager), self.manager)
        self.assertEqual(list(Category.objects.filter(archived_at__isnull=True).values_list('pk', flat=True)), [third.pk, self.other.pk])
        self.assertEqual(Category.objects.get(pk=self.category.pk).position, 10)

    def test_audit_error_rolls_back_archive_and_database_constraint_prevents_visibility(self):
        with patch('django.contrib.admin.models.LogEntryManager.log_actions', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                self.archive()
        self.assertIsNone(Category.objects.get(pk=self.category.pk).archived_at)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Category.objects.filter(pk=self.category.pk).update(archived_at=timezone.now())

    def test_archived_choices_are_hidden_for_new_products_and_new_bulk_assignments(self):
        self.archive()
        form = CatalogProductForm()
        self.assertNotIn(self.category, form.fields['category'].queryset)
        self.assertNotIn(self.category, form.fields['collections'].queryset)
        forged = CatalogProductForm({'name': 'Nouvelle polaire', 'price': '30', 'category': self.category.pk})
        self.assertFalse(forged.is_valid())
        self.assertIn('category', forged.errors)
        for data in ({'change_category': '1', 'category': self.category.pk}, {'collections_add': [self.category.pk]}):
            self.assertFalse(CatalogBulkForm(data).is_valid())
        remove = CatalogBulkForm({'collections_remove': [self.category.pk]})
        self.assertTrue(remove.is_valid(), remove.errors)

    def test_existing_archived_memberships_can_be_kept_or_removed_without_being_newly_assigned(self):
        self.archive()
        form = CatalogProductForm(instance=self.product)
        self.assertIn(self.category, form.fields['category'].queryset)
        self.assertIn(self.category, form.fields['collections'].queryset)
        self.assertIn('archivée', form.fields['category'].label_from_instance(self.category))
        kept = CatalogProductForm({'name': self.product.name, 'price': '45', 'category': self.category.pk,
                                  'collections': [self.category.pk, self.other.pk]}, instance=self.product)
        self.assertTrue(kept.is_valid(), kept.errors)
        removed = CatalogProductForm({'name': self.product.name, 'price': '45', 'category': self.other.pk,
                                     'collections': [self.other.pk]}, instance=self.product)
        self.assertTrue(removed.is_valid(), removed.errors)
        removed.save()
        self.product.refresh_from_db()
        fresh = CatalogProductForm(instance=self.product)
        self.assertNotIn(self.category, fresh.fields['category'].queryset)
        self.assertNotIn(self.category, fresh.fields['collections'].queryset)

    def test_new_bulk_assignment_prepared_before_archival_is_rejected_on_apply(self):
        product = Product.objects.create(name='À ranger', price=20, is_published=False, category=self.other)
        product.refresh_from_db()
        snapshot = selection_snapshot(self.manager, [product])
        _, token = prepare_bulk_preview(self.manager, [product.pk], {'category': self.category.pk}, snapshot)
        self.archive()
        with self.assertRaises(ValidationError):
            apply_bulk_confirmation(self.manager, token)
        product.refresh_from_db()
        self.assertEqual(product.category_id, self.other.pk)


    def test_moves_lock_categories_by_pk_before_reading_merchant_display_order(self):
        # Lock order must agree with product edits/undo, even when display order
        # puts the last-created category first. Otherwise concurrent writes can
        # hold these same rows in opposite orders.
        first_displayed = Category.objects.create(name='Été prioritaire', slug='ete-prioritaire', position=1)
        active = list(Category.objects.filter(archived_at__isnull=True))
        token = category_order_snapshot(active, self.manager)
        with CaptureQueriesContext(connection) as captured:
            moved = move_category(self.other.pk, 'up', token, self.manager)
        selects = [item['sql'] for item in captured.captured_queries
                   if item['sql'].lstrip().upper().startswith('SELECT')
                   and 'FROM "core_category"' in item['sql']]
        self.assertGreaterEqual(len(selects), 2)
        self.assertIn('SELECT "core_category"."id" AS "pk"', selects[0])
        # Django may order a selected field by its column ordinal.
        self.assertRegex(selects[0], r'ORDER BY (?:1|"core_category"\."id") ASC')
        self.assertNotIn('"position"', selects[0])
        if connection.vendor == 'postgresql':
            self.assertIn('FOR UPDATE', selects[0])
        self.assertIn('ORDER BY "core_category"."position" ASC', selects[1])
        self.assertEqual([item.pk for item in moved], [first_displayed.pk, self.other.pk, self.category.pk])
