"""Verified duplicate archival must never change stock or purchase snapshots."""
from datetime import timedelta
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone

from core.catalog_deduplication import archive_verified_duplicate
from core.models import PriceObservation, Product, ProductImage, ProductPromotion
from paiement.models import Order
from stocks.models import Variant


class VerifiedCatalogueDuplicateTests(TestCase):
    def setUp(self):
        self.canonical = Product.objects.create(name='Veste polaire blanche LHOTSE', slug='veste-blanche', price='49.90', source_url='https://example.org/1024-produit.html')
        self.duplicate = Product.objects.create(name=self.canonical.name, slug='veste-blanche-copie', price=self.canonical.price, source_url='https://example.org/1046-produit.html', is_featured=True)

    def archive(self, **kwargs):
        values = {'expected_source_url': self.duplicate.source_url, 'expected_target_url': self.canonical.source_url}
        values.update(kwargs)
        return archive_verified_duplicate(self.duplicate, self.canonical, **values)

    def test_archive_is_idempotent_and_preserves_purchase_snapshots_and_target_stock(self):
        stock = Variant.objects.create(product=self.canonical, size='M', color='Blanc', quantity=3, reserved=1, price='45.00')
        items = [{'product_id': self.duplicate.pk, 'name': self.duplicate.name, 'price': '49.90', 'size': 'M', 'color': 'Blanc'}]
        order = Order.objects.create(session_key='duplicate-test', items=items, subtotal=4990, status='demo')
        previous_observations = list(PriceObservation.objects.order_by('pk').values())
        first = self.archive(evidence={'merchant_reference': 'FME/Pol/lhots marb./Blc'})
        second = self.archive()
        self.duplicate.refresh_from_db()
        self.canonical.refresh_from_db()
        stock.refresh_from_db()
        order.refresh_from_db()
        self.assertTrue(first.changed)
        self.assertFalse(second.changed)
        self.assertEqual(self.duplicate.duplicate_of_id, self.canonical.pk)
        self.assertFalse(self.duplicate.is_published)
        self.assertFalse(self.duplicate.is_featured)
        self.assertTrue(self.canonical.is_published)
        self.assertEqual(stock.quantity, 3)
        self.assertEqual(stock.reserved, 1)
        self.assertEqual(str(stock.price), '45.00')
        self.assertEqual(order.items, items)
        self.assertEqual(order.subtotal, 4990)
        self.assertEqual(list(PriceObservation.objects.order_by('pk').values()), previous_observations)
        self.assertEqual(self.duplicate.information_sources['_duplicate_review']['evidence']['merchant_reference'], 'FME/Pol/lhots marb./Blc')
        self.assertTrue(Product.objects.filter(pk=self.duplicate.pk).exists())

    def test_gallery_preserves_original_rows_and_shares_file_names_without_file_copy(self):
        first = ProductImage.objects.create(product=self.duplicate, image='products/existing-photo.webp', alt='Vue de dos', order=2)
        second = ProductImage.objects.create(product=self.duplicate, image='products/shared.webp', order=3)
        ProductImage.objects.create(product=self.canonical, image=second.image.name, order=5)
        result = self.archive()
        self.assertEqual(result.copied_images, 1)
        self.assertTrue(ProductImage.objects.filter(pk=first.pk, product=self.duplicate).exists())
        copy = self.canonical.images.get(image=first.image.name)
        self.assertEqual(copy.alt, 'Vue de dos')
        self.assertGreater(copy.order, 5)
        self.assertEqual(self.canonical.images.filter(image=second.image.name).count(), 1)
        self.archive()
        self.assertEqual(self.canonical.images.count(), 2)

    def test_dry_run_performs_no_archival_or_gallery_write(self):
        ProductImage.objects.create(product=self.duplicate, image='products/kept.webp')
        result = self.archive(apply=False)
        self.duplicate.refresh_from_db()
        self.assertTrue(result.changed)
        self.assertTrue(self.duplicate.is_published)
        self.assertIsNone(self.duplicate.duplicate_of_id)
        self.assertEqual(self.canonical.images.count(), 0)

    def test_source_stock_configuration_blocks_archival_even_at_zero(self):
        stock = Variant.objects.create(product=self.duplicate, size='S', color='Blanc', quantity=0)
        with self.assertRaisesMessage(CommandError, 'variantes de stock'):
            self.archive()
        self.duplicate.refresh_from_db()
        stock.refresh_from_db()
        self.assertTrue(self.duplicate.is_published)
        self.assertIsNone(self.duplicate.duplicate_of_id)
        self.assertEqual(stock.quantity, 0)

    def test_source_unknown_stock_is_not_silently_hidden(self):
        Variant.objects.create(product=self.duplicate, size='M', color='Blanc', quantity=None)
        with self.assertRaisesMessage(CommandError, 'variantes de stock'):
            self.archive()
        self.duplicate.refresh_from_db()
        self.assertTrue(self.duplicate.is_published)

    def test_source_reduction_blocks_archival_without_cancelling_it(self):
        start = timezone.now()
        promotion = ProductPromotion.objects.create(product=self.duplicate, starts_at=start, ends_at=start + timedelta(days=1), kind='percent', value=10)
        with self.assertRaisesMessage(CommandError, 'réductions'):
            self.archive()
        promotion.refresh_from_db()
        self.assertTrue(promotion.enabled)
        self.duplicate.refresh_from_db()
        self.assertTrue(self.duplicate.is_published)

    def test_source_reference_mismatch_has_no_effect(self):
        with self.assertRaisesMessage(CommandError, 'adresses source'):
            self.archive(expected_source_url='https://example.org/wrong.html')
        self.duplicate.refresh_from_db()
        self.assertTrue(self.duplicate.is_published)
        self.assertIsNone(self.duplicate.duplicate_of_id)

    def test_target_reference_mismatch_has_no_effect(self):
        with self.assertRaisesMessage(CommandError, 'adresses source'):
            self.archive(expected_target_url='https://example.org/wrong.html')
        self.duplicate.refresh_from_db()
        self.assertTrue(self.duplicate.is_published)

    def test_missing_expected_reference_is_rejected(self):
        with self.assertRaisesMessage(CommandError, 'adresses source'):
            self.archive(expected_source_url='')

    def test_later_merchant_name_or_price_changes_require_new_review(self):
        Product.objects.filter(pk=self.duplicate.pk).update(price='50.00')
        with self.assertRaisesMessage(CommandError, 'nom ou le prix'):
            self.archive()
        self.duplicate.refresh_from_db()
        self.assertTrue(self.duplicate.is_published)

    def test_unpublished_target_and_alias_chain_are_rejected(self):
        Product.objects.filter(pk=self.canonical.pk).update(is_published=False)
        with self.assertRaisesMessage(CommandError, 'fiche conservée'):
            self.archive()
        other = Product.objects.create(name='Autre fiche', slug='autre-fiche', price=10)
        Product.objects.filter(pk=self.canonical.pk).update(is_published=True, duplicate_of=other)
        with self.assertRaisesMessage(CommandError, 'fiche conservée'):
            self.archive()

    def test_self_alias_is_rejected_by_service_and_database(self):
        with self.assertRaisesMessage(CommandError, 'elle-même'):
            archive_verified_duplicate(self.canonical, self.canonical, expected_source_url=self.canonical.source_url, expected_target_url=self.canonical.source_url)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.filter(pk=self.canonical.pk).update(duplicate_of=self.canonical)

    def test_canonical_cannot_be_deleted_while_duplicate_history_exists(self):
        self.archive()
        with self.assertRaises(ProtectedError):
            self.canonical.delete()

    def test_management_command_dry_run_and_apply(self):
        with TemporaryDirectory() as folder:
            manifest = Path(folder) / 'verified.json'
            manifest.write_text(json.dumps({'duplicates': [self.entry()]}))
            call_command('reconcile_catalog_duplicates', manifest=manifest, stdout=StringIO())
            self.duplicate.refresh_from_db()
            self.assertTrue(self.duplicate.is_published)
            call_command('reconcile_catalog_duplicates', manifest=manifest, apply=True, stdout=StringIO())
            self.duplicate.refresh_from_db()
            self.assertFalse(self.duplicate.is_published)
            again = StringIO()
            call_command('reconcile_catalog_duplicates', manifest=manifest, apply=True, stdout=again)
            self.assertIn('1 déjà traité', again.getvalue())

    def entry(self):
        return {'duplicate_slug': self.duplicate.slug, 'canonical_slug': self.canonical.slug, 'duplicate_source_url': self.duplicate.source_url, 'canonical_source_url': self.canonical.source_url, 'evidence': {'sources': [self.duplicate.source_url, self.canonical.source_url]}}

    def test_management_batch_rolls_back_earlier_archival_on_later_conflict(self):
        first = self.entry()
        second = self.entry()
        second['canonical_source_url'] = 'https://example.org/wrong.html'
        with TemporaryDirectory() as folder:
            manifest = Path(folder) / 'verified.json'
            manifest.write_text(json.dumps({'duplicates': [first, second]}))
            with self.assertRaises(CommandError):
                call_command('reconcile_catalog_duplicates', manifest=manifest, apply=True, stdout=StringIO())
        self.duplicate.refresh_from_db()
        self.assertTrue(self.duplicate.is_published)
        self.assertIsNone(self.duplicate.duplicate_of_id)

    def test_later_canonical_merchant_edits_are_preserved_on_repeat(self):
        self.archive()
        Product.objects.filter(pk=self.canonical.pk).update(name='Nom corrigé par la boutique', price='52.00', is_published=False)
        result = self.archive()
        self.assertFalse(result.changed)
        self.canonical.refresh_from_db()
        self.assertEqual(self.canonical.name, 'Nom corrigé par la boutique')
        self.assertEqual(str(self.canonical.price), '52.00')
        self.assertFalse(self.canonical.is_published)
