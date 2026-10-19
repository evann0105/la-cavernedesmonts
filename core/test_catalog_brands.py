import json
from copy import deepcopy
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from core.models import PriceObservation, Product
from stocks.models import Variant


class CatalogBrandImportTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'brands.json'
        self.product = Product.objects.create(
            slug='polaire-rouge', name='Polaire rouge', source_url='https://boutique.test/polaire',
            price=Decimal('42.50'), description='Description de la boutique',
            model_reference='MODELE', composition='100 % coton', brand_contact='Contact existant',
            information_review_notes='Note juridique privée conservée',
            information_sources={'composition': {'value': '100 % coton', 'sources': []}},
            translations={'en': {'name': 'Red fleece'}},
        )
        self.variant = Variant.objects.create(
            product=self.product, size='M', color='Rouge', quantity=7,
            reserved=2, price=Decimal('40.00'),
        )
        self.entry = {
            'slug': self.product.slug,
            'expected_name': self.product.name,
            'source_url': self.product.source_url,
            'brand': 'Marque vérifiée',
            'evidence': {
                'value': 'Marque vérifiée', 'checked_on': '2026-10-19',
                'basis': 'Étiquette lisible sur la photographie de cette référence exacte.',
                'sources': [{'url': self.product.source_url, 'label': 'Fiche d’origine exacte'}],
                'source_snapshot_sha256': 'a' * 64,
            },
            'review_notes': 'Identité juridique du fabricant restant à préciser.',
        }
        self.manifest = {'checked_on': '2026-10-19', 'products': [self.entry]}

    def run_import(self, *, apply=True, manifest=None, use_default=False):
        self.path.write_text(json.dumps(manifest or self.manifest), encoding='utf-8')
        options = {'stdout': StringIO()}
        if not use_default:
            options['manifest'] = self.path
        if apply:
            options['apply'] = True
        call_command('enrich_catalog_brands', **options)
        return options['stdout'].getvalue()

    def test_default_is_a_simulation_including_private_review_metadata(self):
        before = Product.objects.filter(pk=self.product.pk).values().get()
        output = self.run_import(apply=False)
        self.assertEqual(Product.objects.filter(pk=self.product.pk).values().get(), before)
        self.assertIn('Simulation', output)
        self.assertIn('1 marque(s)', output)

    def test_apply_changes_only_brand_and_sources_and_preserves_inventory(self):
        before = Product.objects.filter(pk=self.product.pk).values().get()
        variant_before = Variant.objects.filter(pk=self.variant.pk).values().get()
        price_history_before = list(PriceObservation.objects.order_by('pk').values())
        self.run_import()
        after = Product.objects.filter(pk=self.product.pk).values().get()
        changed = {key for key in before if before[key] != after[key]}
        self.assertEqual(changed, {'brand', 'information_sources'})
        self.assertEqual(after['brand'], 'Marque vérifiée')
        self.assertEqual(after['information_sources']['brand'], self.entry['evidence'])
        self.assertEqual(after['information_sources']['composition'], before['information_sources']['composition'])
        self.assertEqual(Variant.objects.filter(pk=self.variant.pk).values().get(), variant_before)
        self.assertEqual(list(PriceObservation.objects.order_by('pk').values()), price_history_before)

    def test_unconfirmed_brand_stays_empty_and_evidence_stays_private(self):
        self.entry.update(brand=None, evidence={'sources': [{'url': self.product.source_url, 'label': 'Recherche'}]})
        self.run_import()
        self.product.refresh_from_db()
        self.assertEqual(self.product.brand, '')
        self.assertNotIn('brand', self.product.information_sources)
        review = self.product.information_sources['_brand_review']
        self.assertEqual(review['status'], 'unconfirmed')
        self.assertEqual(review['notes'], self.entry['review_notes'])
        self.assertEqual(review['evidence'], self.entry['evidence'])
        self.assertEqual(self.product.information_source_links, [])
        self.assertEqual(self.product.information_review_notes, 'Note juridique privée conservée')

    def test_changed_name_source_or_missing_slug_is_skipped(self):
        for field, value in (
            ('expected_name', 'Une autre référence'),
            ('source_url', 'https://boutique.test/autre'),
            ('slug', 'produit-inexistant'),
        ):
            with self.subTest(field=field):
                manifest = deepcopy(self.manifest)
                manifest['products'][0][field] = value
                self.run_import(manifest=manifest)
                self.product.refresh_from_db()
                self.assertEqual(self.product.brand, '')
                self.assertNotIn('_brand_review', self.product.information_sources)

    def test_null_source_matches_only_a_product_without_source(self):
        self.entry['source_url'] = None
        self.run_import()
        self.product.refresh_from_db()
        self.assertEqual(self.product.brand, '')
        self.product.source_url = None
        self.product.save(update_fields=['source_url'])
        self.run_import()
        self.product.refresh_from_db()
        self.assertEqual(self.product.brand, 'Marque vérifiée')

    def test_merchant_brand_is_never_overwritten(self):
        self.product.brand = 'Correction de la vendeuse'
        self.product.save(update_fields=['brand'])
        before = self.product.information_sources
        self.run_import()
        self.product.refresh_from_db()
        self.assertEqual(self.product.brand, 'Correction de la vendeuse')
        self.assertEqual(self.product.information_sources, before)

    def test_historical_evidence_prevents_recreating_a_deleted_brand(self):
        self.product.information_sources['brand'] = deepcopy(self.entry['evidence'])
        self.product.save(update_fields=['information_sources'])
        self.run_import()
        self.product.refresh_from_db()
        self.assertEqual(self.product.brand, '')
        self.assertNotIn('_brand_review', self.product.information_sources)

    def test_later_merchant_edit_survives_a_second_import(self):
        self.run_import()
        self.product.refresh_from_db()
        self.product.brand = 'Marque corrigée sur le lot'
        self.product.save(update_fields=['brand'])
        self.run_import()
        self.product.refresh_from_db()
        self.assertEqual(self.product.brand, 'Marque corrigée sur le lot')

    def test_repeating_verified_or_unconfirmed_import_does_not_save_again(self):
        self.run_import()
        with patch.object(Product, 'save') as save:
            self.run_import()
            save.assert_not_called()
        self.product.brand = ''
        self.product.information_sources = {}
        self.product.save(update_fields=['brand', 'information_sources'])
        self.entry.update(brand=None, evidence={})
        self.run_import()
        with patch.object(Product, 'save') as save:
            self.run_import()
            save.assert_not_called()

    def test_a_later_verified_source_can_resolve_a_private_review(self):
        unresolved = deepcopy(self.manifest)
        unresolved['products'][0].update(brand=None, evidence={})
        self.run_import(manifest=unresolved)
        self.run_import()
        self.product.refresh_from_db()
        self.assertEqual(self.product.brand, 'Marque vérifiée')
        self.assertEqual(self.product.information_sources['_brand_review']['status'], 'verified')

    def test_non_https_or_credential_sources_are_rejected_without_writing(self):
        for url in ('http://boutique.test/fiche', '/fiche', 'javascript:alert(1)',
                    'https://user:secret@boutique.test/fiche', 'https://boutique.test/\nfiche'):
            with self.subTest(url=url):
                manifest = deepcopy(self.manifest)
                manifest['products'][0]['evidence']['sources'][0]['url'] = url
                with self.assertRaises(CommandError):
                    self.run_import(manifest=manifest)
                self.product.refresh_from_db()
                self.assertEqual(self.product.brand, '')

    def test_invalid_evidence_and_duplicate_entries_are_rejected(self):
        mutations = (
            lambda m: m['products'][0]['evidence'].update(value='Autre marque'),
            lambda m: m['products'][0]['evidence'].update(source_snapshot_sha256='invalide'),
            lambda m: m['products'][0]['evidence'].update(checked_on='2026-99-99'),
            lambda m: m['products'][0]['evidence'].update(sources=[]),
            lambda m: m['products'][0].update(brand='X' * 101),
            lambda m: m['products'].append(deepcopy(m['products'][0])),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                manifest = deepcopy(self.manifest)
                mutation(manifest)
                with self.assertRaises(CommandError):
                    self.run_import(manifest=manifest)
                self.product.refresh_from_db()
                self.assertEqual(self.product.brand, '')

    def test_save_failure_rolls_back_every_product(self):
        second = Product.objects.create(
            slug='polaire-bleue', name='Polaire bleue',
            source_url='https://boutique.test/bleu', price=40,
        )
        entry = deepcopy(self.entry)
        entry.update(slug=second.slug, expected_name=second.name, source_url=second.source_url)
        self.manifest['products'].append(entry)
        original_save = Product.save

        def fail_on_second(instance, *args, **kwargs):
            if instance.pk == second.pk:
                raise RuntimeError('Échec de sauvegarde simulé')
            return original_save(instance, *args, **kwargs)

        with patch.object(Product, 'save', fail_on_second):
            with self.assertRaises(RuntimeError):
                self.run_import()
        self.product.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(self.product.brand, '')
        self.assertEqual(second.brand, '')
        self.assertNotIn('_brand_review', self.product.information_sources)

    def test_default_manifest_path_is_loaded_from_project_settings(self):
        directory = Path(self.directory.name)
        (directory / 'core').mkdir()
        (directory / 'core/catalog_brands_seed.json').write_text(json.dumps(self.manifest), encoding='utf-8')
        with override_settings(BASE_DIR=directory):
            self.run_import(use_default=True)
        self.product.refresh_from_db()
        self.assertEqual(self.product.brand, 'Marque vérifiée')
