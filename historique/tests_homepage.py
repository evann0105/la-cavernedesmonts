"""Restore the entire editable homepage while keeping catalogue and stock live."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.core.files.storage import default_storage
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accueil import tests as home_tests
from accueil.configuration import configuration
from accueil.models import FeaturedHomepageProduct, HeroSlide, HomepageConfiguration
from accueil.schema import IMAGE_FIELDS, OPTIONAL_SECTIONS, SECTIONS
from accueil.services import HomepageConflict, save_section
from accueil.slideshow import save_slideshow
from core.models import Category, HomepageSelection, Product
from stocks.models import Variant

from .exceptions import HistoryConflict, HistoryError
from .homepage import (describe_changes, preview_response, record_homepage, restore_homepage,
                       restore_warnings, save_collections, snapshot_homepage, validate_restore)
from .media import asset_name
from .models import HistoryVersion
from .services import fingerprint


@override_settings(LOCAL_SKIP_EMAIL_VERIFICATION=False, EMAIL_DELIVERY_ENABLED=False)
class HomepageHistoryTests(TestCase):
    data = home_tests.HomepageManagementTests.data
    url = home_tests.HomepageManagementTests.url
    save = home_tests.HomepageManagementTests.save
    products_data = home_tests.HomepageManagementTests.products_data

    def setUp(self):
        home_tests.HomepageManagementTests.setUp(self)
        self.baseline = snapshot_homepage()

    def versions(self):
        return HistoryVersion.objects.filter(scope='homepage', object_id=1).order_by('sequence')

    def latest(self):
        return self.versions().last()

    def restore(self, version):
        return restore_homepage(version, actor=self.manager, expected_fingerprint=fingerprint(snapshot_homepage()))

    def collections(self):
        choices = {}
        for index, slot in enumerate(('femmes', 'hommes', 'enfants')):
            category, _ = Category.objects.get_or_create(slug=slot, defaults={'name': slot})
            self.products[index].category = category
            self.products[index].save(update_fields=['category'])
            choices[slot] = self.products[index]
        return choices

    def test_first_save_keeps_baseline_defaults_and_completed_after_state(self):
        self.assertFalse(self.versions().exists())
        self.assertEqual(self.save(title_line1='La montagne ensemble.').status_code, 302)
        baseline, latest = self.versions()
        self.assertEqual(baseline.snapshot, self.baseline)
        self.assertEqual(baseline.source, 'baseline')
        self.assertEqual(latest.snapshot, snapshot_homepage())
        self.assertEqual(latest.snapshot['values']['hero']['title_line1'], 'La montagne ensemble.')
        self.assertEqual(len(baseline.snapshot['images']), 7)
        self.assertTrue(all(asset.get('archive') for asset in baseline.assets))
        self.assertEqual(latest.fingerprint, fingerprint(latest.snapshot))

    def test_public_and_management_gets_do_not_create_configuration_or_versions(self):
        for url in ('/', reverse('core:catalog_homepage'), reverse('core:homepage_collections'), reverse('homepage:sections')):
            self.assertEqual(self.client.get(url).status_code, 200)
        self.assertFalse(self.versions().exists())
        self.assertFalse(HomepageConfiguration.objects.exists())

    def test_every_edited_region_is_captured_after_all_child_writes(self):
        for key, spec in SECTIONS.items():
            values = self.data(key)
            for name in spec['images']:
                values[name] = home_tests.photo()
            self.assertEqual(self.client.post(self.url(key), values).status_code, 302)
        selected = [self.products[3], self.products[0], self.products[2]]
        self.assertEqual(self.client.post(reverse('homepage:featured'), self.products_data(selected)).status_code, 302)
        self.assertEqual(self.latest().snapshot['featured_ids'], [p.pk for p in selected])
        chosen = self.collections()
        save_collections({'revision': configuration()['revision'], **chosen}, actor=self.manager)
        self.assertEqual(self.latest().snapshot['collections'], {slot: p.pk for slot, p in chosen.items()})
        for section in ('hero', 'feature'):
            save_slideshow({'revision': configuration()['revision'], 'autoplay': False,
                'interval': 10, 'new_photos': [home_tests.photo()]}, [], self.manager, section=section)
            row = HeroSlide.objects.get(section=section)
            save_slideshow({'revision': configuration()['revision'], 'autoplay': False, 'interval': 8},
                [{'id': row, 'position': 1, 'alt': f'Photo {section}', 'mobile_image': home_tests.photo()}], self.manager, section=section)
        version = self.latest()
        self.assertEqual(version.snapshot, snapshot_homepage())
        self.assertTrue(all(image['kind'] == 'media' for image in version.snapshot['images'].values()))
        self.assertEqual({slide['section'] for slide in version.snapshot['slides']}, {'hero', 'feature'})
        self.assertTrue(all(slide['mobile_image'] for slide in version.snapshot['slides']))
        self.assertEqual(version.snapshot['feature_slideshow'], {'autoplay': False, 'interval': 8})

    def test_full_restore_then_restore_again_keeps_previous_state_recoverable(self):
        self.save(title_line1='Première version', hero_image=home_tests.photo(), hero_mobile_image=home_tests.photo())
        selected = [self.products[3], self.products[1]]
        self.client.post(reverse('homepage:featured'), self.products_data(selected))
        choices = self.collections()
        save_collections({'revision': configuration()['revision'], **choices}, actor=self.manager)
        for section in ('hero', 'feature'):
            save_slideshow({'revision': configuration()['revision'], 'autoplay': False,
                'interval': 10, 'new_photos': [home_tests.photo()]}, [], self.manager, section=section)
        self.client.post(reverse('homepage:sections'), {'revision': configuration()['revision'], 'worlds': 'on', 'feature': 'on'})
        old = self.latest()
        self.save(title_line1='Deuxième version', hero_image=home_tests.photo())
        newer = self.latest()
        stock = Variant.objects.create(product=self.products[0], size='M', quantity=7, reserved=2)
        price = self.products[0].price
        result = self.restore(old)
        self.assertEqual(result.source, 'restore')
        self.assertEqual(result.restored_from_id, old.pk)
        current = snapshot_homepage()
        for key in ('values', 'visible', 'slideshow', 'feature_slideshow', 'featured_ids', 'collections'):
            self.assertEqual(current[key], old.snapshot[key])
        for name, image in old.snapshot['images'].items():
            self.assertEqual(current['images'][name]['name'], asset_name(old, image['kind'], image['name'], prefer_archive=True)[1])
        for actual, previous in zip(current['slides'], old.snapshot['slides']):
            self.assertEqual(actual['alt'], previous['alt'])
            self.assertEqual(actual['section'], previous['section'])
            self.assertEqual(actual['image']['name'], asset_name(old, **previous['image'], prefer_archive=True)[1])
        self.restore(newer)
        self.assertEqual(configuration()['values']['hero']['title_line1'], 'Deuxième version')
        self.restore(old)
        self.assertEqual(configuration()['values']['hero']['title_line1'], 'Première version')
        stock.refresh_from_db(); self.products[0].refresh_from_db()
        self.assertEqual((stock.quantity, stock.reserved, self.products[0].price), (7, 2, price))
        self.assertEqual(self.versions().filter(source='restore').count(), 3)

    def test_old_photos_survive_replacement_deletion_and_missing_original(self):
        self.save(hero_image=home_tests.photo())
        save_slideshow({'revision': configuration()['revision'], 'autoplay': True,
            'interval': 6, 'new_photos': [home_tests.photo()]}, [], self.manager)
        version = self.latest()
        original = version.snapshot['images']['hero_image']['name']
        slide = HeroSlide.objects.get(section='hero')
        save_slideshow({'revision': configuration()['revision'], 'autoplay': True, 'interval': 6},
                      [{'id': slide, 'DELETE': True}], self.manager)
        self.assertTrue(default_storage.exists(slide.image.name))
        self.save(**{'hero_image-clear': 'on'})
        default_storage.delete(original)
        validate_restore(version)
        self.restore(version)
        self.assertTrue(default_storage.exists(HomepageConfiguration.objects.get().hero_image.name))
        self.assertEqual(HeroSlide.objects.count(), 1)

    def test_missing_or_corrupted_retained_photo_blocks_restore_without_changes(self):
        self.save(hero_image=home_tests.photo())
        old = self.latest()
        image = old.snapshot['images']['hero_image']
        archive = asset_name(old, **image, prefer_archive=True)[1]
        default_storage.delete(image['name']); default_storage.delete(archive)
        self.save(title_line1='Version récente')
        current = snapshot_homepage(); count = self.versions().count()
        with self.assertRaises(HistoryError):
            self.restore(old)
        self.assertEqual(snapshot_homepage(), current)
        self.assertEqual(self.versions().count(), count)
        self.assertTrue(restore_warnings(old))

    def test_old_confirmation_cannot_replace_a_newer_change(self):
        self.save(title_line1='Ancien état'); old = self.latest()
        self.save(title_line1='État à vérifier'); reviewed = fingerprint(snapshot_homepage())
        self.save('feature', title_line1='Modification dans un autre onglet')
        current = snapshot_homepage()
        with self.assertRaises(HistoryConflict):
            restore_homepage(old, actor=self.manager, expected_fingerprint=reviewed)
        self.assertEqual(snapshot_homepage(), current)

    def test_card_changes_share_revision_and_history_with_other_home_editors(self):
        choices = self.collections()
        stale = {'revision': configuration()['revision'], **{slot: p.pk for slot, p in choices.items()}}
        self.save(title_line1='Modification récente')
        response = self.client.post(reverse('core:homepage_collections'), stale)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(snapshot_homepage()['collections'], self.baseline['collections'])
        stale['revision'] = configuration()['revision']
        self.assertEqual(self.client.post(reverse('core:homepage_collections'), stale).status_code, 302)
        self.assertEqual(self.latest().snapshot['collections'], {slot: p.pk for slot, p in choices.items()})
        self.assertEqual(configuration()['revision'], 2)

    def test_automatic_modes_are_restored_without_selecting_current_product_ids(self):
        self.save(title_line1='Point de départ')
        original = self.versions().first()
        self.client.post(reverse('homepage:featured'), self.products_data(self.products[:2]))
        save_collections({'revision': configuration()['revision'], **self.collections()}, actor=self.manager)
        self.restore(original)
        self.assertFalse(FeaturedHomepageProduct.objects.exists())
        self.assertEqual(snapshot_homepage()['collections'], original.snapshot['collections'])
        self.assertEqual(snapshot_homepage()['featured_ids'], [])

    def test_unavailable_product_reference_blocks_restore_and_is_not_republished(self):
        self.client.post(reverse('homepage:featured'), self.products_data(self.products[:2]))
        old = self.latest()
        self.products[0].is_published = False
        self.products[0].save(update_fields=['is_published'])
        current = snapshot_homepage()
        with self.assertRaisesMessage(HistoryError, 'supprimé, masqué ou sans photo'):
            self.restore(old)
        self.assertEqual(snapshot_homepage(), current)
        self.products[0].refresh_from_db(); self.assertFalse(self.products[0].is_published)
        self.assertTrue(any('produit mis en avant' in text for text in restore_warnings(old)))

    def test_missing_collection_reference_blocks_entire_restore(self):
        choices = self.collections()
        save_collections({'revision': 0, **choices}, actor=self.manager)
        old = self.latest()
        choices['femmes'].delete()
        current = snapshot_homepage()
        with self.assertRaisesMessage(HistoryError, 'carte Femme'):
            self.restore(old)
        self.assertEqual(snapshot_homepage(), current)

    def test_failed_save_rolls_back_history_and_removes_only_new_uploads(self):
        from historique import homepage
        original_record = homepage.record_homepage
        def fail_after(*args, **kwargs):
            if kwargs.get('source') != 'baseline':
                raise RuntimeError('Snapshot failure')
            return original_record(*args, **kwargs)
        with patch('historique.homepage.record_homepage', side_effect=fail_after), self.assertRaises(RuntimeError):
            save_section('hero', self.data(hero_image=home_tests.photo()), self.manager)
        self.assertFalse(HomepageConfiguration.objects.exists())
        self.assertFalse(self.versions().exists())
        from pathlib import Path
        self.assertFalse(list((Path(self.directory.name) / 'homepage').glob('*')))

    def test_noop_save_does_not_duplicate_snapshot(self):
        self.save(title_line1='Titre inchangé'); count = self.versions().count()
        self.save(title_line1='Titre inchangé')
        self.assertEqual(self.versions().count(), count)

    def test_preview_is_read_only_private_and_uses_the_version_without_current_relations(self):
        self.save(title_line1='Titre historique', hero_image=home_tests.photo())
        save_slideshow({'revision': configuration()['revision'], 'autoplay': False,
            'interval': 8, 'new_photos': [home_tests.photo()]}, [], self.manager, section='feature')
        self.client.post(reverse('homepage:featured'), self.products_data(self.products[:2]))
        old = self.latest()
        self.save(title_line1='Titre présent')
        self.client.post(reverse('homepage:featured'), self.products_data(self.products[3:]))
        current = snapshot_homepage(); count = self.versions().count()
        request = RequestFactory().get('/gestion/historique/accueil/apercu/')
        request.user = self.manager; request.session = {}
        with CaptureQueriesContext(connection) as queries:
            response = preview_response(request, old)
        html = response.content.decode()
        self.assertIn('Titre historique', html); self.assertNotIn('Titre présent', html)
        self.assertIn(old.snapshot['images']['hero_image']['name'], html)
        self.assertIn(old.snapshot['slides'][0]['image']['name'], html)
        self.assertIn('Article accueil 0', html); self.assertNotIn('Article accueil 4', html)
        self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')
        self.assertIn('private', response['Cache-Control']); self.assertIn('no-store', response['Cache-Control'])
        self.assertFalse([q['sql'] for q in queries if q['sql'].lstrip().startswith(('INSERT', 'UPDATE', 'DELETE'))])
        self.assertEqual(snapshot_homepage(), current); self.assertEqual(self.versions().count(), count)

    def test_private_preview_and_restore_require_catalog_access(self):
        self.save(title_line1='Privé'); old = self.latest()
        request = RequestFactory().get('/'); request.user = AnonymousUser()
        with self.assertRaises(PermissionDenied): preview_response(request, old)
        with self.assertRaises(PermissionDenied): restore_homepage(old, actor=None, expected_fingerprint=fingerprint(snapshot_homepage()))

    def test_tampered_snapshot_and_arbitrary_urls_never_reach_preview(self):
        self.save(title_line1='Conservé'); old = self.latest()
        for field, value in (('image', 'https://example.test/secret.jpg'), ('image', '../../secret.jpg'), ('target', 'javascript:alert(1)')):
            with self.subTest(value=value):
                snapshot = deepcopy(old.snapshot)
                if field == 'image': snapshot['images']['hero_image']['name'] = value
                else: snapshot['values']['hero']['button_target'] = value
                forged = SimpleNamespace(scope='homepage', object_id=1, snapshot=snapshot, fingerprint=fingerprint(snapshot), assets=[])
                with self.assertRaises(HistoryError): validate_restore(forged)

    def test_differences_include_text_visibility_both_carousels_photos_and_product_order(self):
        before = deepcopy(self.baseline)
        after = deepcopy(before)
        after['values']['hero']['title_line1'] = 'Nouveau titre'
        after['visible']['worlds'] = False
        after['images']['hero_image'] = {'kind': 'media', 'name': 'homepage/old.webp'}
        after['feature_slideshow'] = {'autoplay': False, 'interval': 10}
        after['slides'] = [{'section': 'feature', 'position': 1, 'alt': 'Photo conservée',
                           'image': {'kind': 'media', 'name': 'homepage/feature.webp'}, 'mobile_image': None}]
        after['featured_ids'] = [self.products[1].pk, self.products[0].pk]
        sections = describe_changes(before, after)
        titles = [section['title'] for section in sections]
        self.assertIn('Grande introduction', titles); self.assertIn('Les trois univers', titles)
        self.assertIn('Photo et invitation au grand air', titles); self.assertIn('Produits et cartes des collections', titles)
        rows = [row for section in sections for row in section['rows']]
        self.assertTrue(any(row['kind'] == 'image' for row in rows))
        self.assertTrue(any(row['kind'] == 'list' for row in rows))


from concurrent.futures import ThreadPoolExecutor
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest import skipUnless
from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TransactionTestCase
from connexion.test_helpers import verify_test_user


@skipUnless(connection.vendor == 'postgresql', 'Concurrency checks require PostgreSQL object locks.')
@override_settings(LOCAL_SKIP_EMAIL_VERIFICATION=False, EMAIL_DELIVERY_ENABLED=False)
class HomepageHistoryConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        override = override_settings(MEDIA_ROOT=self.directory.name)
        override.enable(); self.addCleanup(override.disable)
        self.manager = get_user_model().objects.create_user('home-race-manager',
            email='home-race@example.test', is_staff=True, is_superuser=True)
        verify_test_user(self.manager)

    def values(self, revision, title):
        return {'revision': revision, **{name: spec['default'] for name, spec in SECTIONS['hero']['fields'].items()},
                'title_line1': title}

    def race(self, first, second):
        gate = Barrier(2)
        def run(operation):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.manager.pk)
                gate.wait(timeout=10)
                try:
                    operation(actor)
                    return 'saved'
                except (HomepageConflict, HistoryConflict):
                    return 'conflict'
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run, operation) for operation in (first, second)]
            return [future.result(timeout=20) for future in futures]

    def test_two_first_writes_keep_one_baseline_and_reject_the_stale_edit(self):
        results = self.race(
            lambda actor: save_section('hero', self.values(0, 'Premier onglet'), actor),
            lambda actor: save_section('hero', self.values(0, 'Deuxième onglet'), actor),
        )
        self.assertCountEqual(results, ['saved', 'conflict'])
        versions = HistoryVersion.objects.filter(scope='homepage').order_by('sequence')
        self.assertEqual(list(versions.values_list('sequence', flat=True)), [1, 2])
        self.assertEqual(versions.first().source, 'baseline')
        self.assertEqual(versions.last().snapshot, snapshot_homepage())
        self.assertEqual(configuration()['revision'], 1)

    def test_restore_and_edit_cannot_overwrite_each_other(self):
        save_section('hero', self.values(0, 'Version ancienne'), self.manager)
        old = HistoryVersion.objects.filter(scope='homepage').order_by('sequence').last()
        save_section('hero', self.values(1, 'Version actuelle'), self.manager)
        reviewed = fingerprint(snapshot_homepage())
        results = self.race(
            lambda actor: restore_homepage(old, actor=actor, expected_fingerprint=reviewed),
            lambda actor: save_section('hero', self.values(2, 'Modification concurrente'), actor),
        )
        self.assertCountEqual(results, ['saved', 'conflict'])
        versions = HistoryVersion.objects.filter(scope='homepage').order_by('sequence')
        self.assertEqual(list(versions.values_list('sequence', flat=True)), [1, 2, 3, 4])
        self.assertEqual(versions.last().snapshot, snapshot_homepage())
        self.assertEqual(configuration()['revision'], 3)
