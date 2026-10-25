from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from connexion.test_helpers import verify_test_user
from .catalog_duplication import DuplicationConflict, duplicate_product, duplication_snapshot
from .category_services import set_category_archived
from .models import Category, Product


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class CatalogDuplicationArchivalTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user(
            'copy-archived-manager', email='copy-archived@example.test', is_staff=True)
        self.manager.user_permissions.add(Permission.objects.get(
            content_type__app_label='core', codename='manage_catalog'))
        verify_test_user(self.manager)
        self.client.force_login(self.manager, backend='connexion.backends.EmailOrUsernameBackend')
        self.main = Category.objects.create(name='Ancienne collection hiver', slug='copy-old-winter', position=30)
        self.collection = Category.objects.create(name='Ancienne sélection montagne', slug='copy-old-mountain', position=10)
        self.active = Category.objects.create(name='Collection actuelle', slug='copy-current', position=20)
        self.source = Product.objects.create(name='Polaire à reprendre', price='80.00', category=self.main,
            include_imported_gallery=False, benefits='Une polaire douce.', is_published=False)
        self.source.collections.add(self.main, self.collection, self.active)

    def archive(self, category, archived=True):
        category.refresh_from_db()
        set_category_archived(category.pk, category.revision, archived, self.manager)

    def token(self):
        self.source.refresh_from_db()
        return duplication_snapshot(self.source, actor=self.manager)[0]

    def duplicate(self, token=None):
        return duplicate_product(self.source.pk, snapshot=token or self.token(), actor=self.manager)

    def url(self):
        return reverse('core:catalog_duplicate', args=[self.source.pk])

    def test_copy_removes_archived_assignments_and_preserves_source_and_active_collections(self):
        self.archive(self.main)
        self.archive(self.collection)
        result = self.duplicate()
        self.assertIsNone(result.product.category_id)
        self.assertCountEqual(result.product.collections.all(), [self.active])
        self.assertFalse(result.product.is_published)
        self.assertCountEqual(result.excluded_categories, [self.main.name, self.collection.name])
        self.source.refresh_from_db()
        self.assertEqual(self.source.category_id, self.main.pk)
        self.assertCountEqual(self.source.collections.all(), [self.main, self.collection, self.active])

    def test_copy_keeps_active_main_category_when_an_additional_rubric_is_archived(self):
        self.archive(self.collection)
        result = self.duplicate()
        self.assertEqual(result.product.category_id, self.main.pk)
        self.assertCountEqual(result.product.collections.all(), [self.main, self.active])
        self.assertEqual(result.excluded_categories, (self.collection.name,))

    def test_preview_explains_excluded_rubrics_before_confirmation_without_mutation(self):
        self.archive(self.main)
        self.archive(self.collection)
        count, audit_count = Product.objects.count(), LogEntry.objects.count()
        response = self.client.get(self.url())
        self.assertContains(response, 'Un brouillon à classer')
        self.assertContains(response, 'ne seront pas reprises')
        self.assertContains(response, self.main.name)
        self.assertContains(response, self.collection.name)
        self.assertCountEqual(response.context['excluded_categories'], [self.main.name, self.collection.name])
        self.assertEqual((Product.objects.count(), LogEntry.objects.count()), (count, audit_count))

    def test_success_reports_removed_rubrics_and_repeated_confirmation_does_not_duplicate(self):
        self.archive(self.main)
        token = self.token()
        response = self.client.post(self.url(), {'snapshot': token})
        self.assertEqual(response.status_code, 302)
        texts = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertTrue(any('Rubriques archivées non reprises' in text and self.main.name in text for text in texts))
        self.assertTrue(any('Classez ce brouillon' in text for text in texts))
        count = Product.objects.count()
        repeat = self.duplicate(token)
        self.assertTrue(repeat.reused)
        self.assertIsNone(repeat.product.category_id)
        self.assertEqual(Product.objects.count(), count)

    def test_archive_after_preview_requires_new_confirmation_then_excludes_rubric(self):
        token = self.token()
        self.archive(self.collection)
        count, audit_count = Product.objects.count(), LogEntry.objects.count()
        with self.assertRaises(DuplicationConflict):
            self.duplicate(token)
        self.assertEqual((Product.objects.count(), LogEntry.objects.count()), (count, audit_count))
        response = self.client.post(self.url(), {'snapshot': token})
        self.assertContains(response, 'ses rubriques ont été modifiées', status_code=409)
        self.assertContains(response, 'Recharger la confirmation', status_code=409)
        result = self.duplicate()
        self.assertNotIn(self.collection, result.product.collections.all())
        self.assertEqual(result.product.category_id, self.main.pk)

    def test_restore_after_preview_requires_new_confirmation_then_keeps_rubric(self):
        self.archive(self.main)
        token = self.token()
        self.archive(self.main, archived=False)
        count = Product.objects.count()
        with self.assertRaises(DuplicationConflict):
            self.duplicate(token)
        self.assertEqual(Product.objects.count(), count)
        result = self.duplicate()
        self.assertEqual(result.product.category_id, self.main.pk)
        self.assertCountEqual(result.product.collections.all(), [self.main, self.collection, self.active])
        self.assertEqual(result.excluded_categories, ())

    def test_category_locks_include_archived_rows_in_pk_order(self):
        self.archive(self.collection)
        token = self.token()
        with CaptureQueriesContext(connection) as queries:
            self.duplicate(token)
        category_reads = [query['sql'] for query in queries if
            'FROM "core_category" WHERE "core_category"."id" IN' in query['sql']]
        self.assertEqual(len(category_reads), 1)
        self.assertIn('ORDER BY "core_category"."id" ASC', category_reads[0])
        self.assertNotIn('"archived_at" IS NULL', category_reads[0])
        if connection.features.has_select_for_update:
            self.assertIn('FOR UPDATE', category_reads[0])
