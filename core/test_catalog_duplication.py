from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice
from PIL import Image

from connexion.test_helpers import verify_test_user
from stocks.models import Variant
from .catalog_duplication import DuplicationConflict, DuplicationError, duplicate_product, duplication_snapshot
from .models import Category, PriceObservation, Product, ProductColor, ProductImage, ProductPromotion


def photo(name, color='red'):
    stream = BytesIO()
    Image.new('RGB', (12, 10), color).save(stream, 'PNG')
    return SimpleUploadedFile(name, stream.getvalue(), content_type='image/png')


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class CatalogDuplicationTests(TestCase):
    def setUp(self):
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        setting = override_settings(MEDIA_ROOT=self.media.name)
        setting.enable()
        self.addCleanup(setting.disable)
        self.manager = get_user_model().objects.create_user('duplicate-manager', email='manager@example.test', is_staff=True)
        self.manager.groups.add(Group.objects.get(name='Gestion du catalogue'))
        verify_test_user(self.manager)
        self.customer = get_user_model().objects.create_user('duplicate-customer', email='customer@example.test')
        self.category = Category.objects.create(name='Rubrique de copie', slug='rubrique-copie')
        self.collection = Category.objects.create(name='Collection de copie', slug='collection-copie')
        self.source = Product.objects.create(name='Polaire adulte', slug='polaire-adulte-copie-test',
            category=self.category, price='80.00', description='Une polaire douce.',
            main_image=photo('original.png'), main_color='Rouge', sizes='M, L',
            brand='Marque vérifiée', model_reference='MODELE-123', composition='100 % polyester',
            benefits='Une douceur agréable.', is_published=True, is_featured=True, availability='available',
            source_url='https://example.test/polaire-originale', article_type='polaires',
            information_sources={'brand': {'value':'Marque vérifiée', 'sources':[]}},
            information_review_notes='Vérifier la nouvelle étiquette.',
            translations={'en': {'name':'Adult fleece', 'description':'A soft fleece.'}})
        self.source.collections.add(self.collection)
        self.gallery = ProductImage.objects.create(product=self.source, image=photo('gallery.png', 'blue'), alt='Dos de la polaire', order=8)
        self.color = ProductColor.objects.create(product=self.source, name='Vert', image=photo('green.png', 'green'))
        self.red = Variant.objects.create(product=self.source, size='M', color='Rouge', reference='INT-M', quantity=8, reserved=2, price='75.00', revision=4)
        self.green = Variant.objects.create(product=self.source, size='L', color='Vert', reference='INT-L', quantity=None, price=None)
        device = TOTPDevice.objects.create(user=self.manager, name='default', confirmed=True)
        self.client.force_login(self.manager, backend='connexion.backends.EmailOrUsernameBackend')
        session = self.client.session
        session['otp_device_id'] = device.persistent_id
        session.save()

    def token(self):
        self.source.refresh_from_db()
        return duplication_snapshot(self.source, actor=self.manager)[0]

    def duplicate(self, token=None):
        return duplicate_product(self.source.pk, snapshot=token or self.token(), actor=self.manager)

    def url(self):
        return reverse('core:catalog_duplicate', args=[self.source.pk])

    def test_get_explains_copy_without_creating_product(self):
        count = Product.objects.count()
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Product.objects.count(), count)
        self.assertContains(response, 'Créer la copie en brouillon')
        self.assertContains(response, 'Les quantités seront toutes à')
        self.assertContains(response, '2 déclinaisons')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('noindex', response['X-Robots-Tag'])
        self.assertTrue(response.context['form'].initial['snapshot'])

    def test_copy_is_unpublished_independent_and_keeps_content_and_regular_prices(self):
        result = self.duplicate()
        copy = result.product
        self.assertFalse(result.reused)
        self.assertEqual(copy.name, 'Copie de Polaire adulte')
        self.assertNotEqual(copy.slug, self.source.slug)
        self.assertEqual(copy.category, self.category)
        self.assertEqual(list(copy.collections.all()), [self.collection])
        self.assertEqual(copy.description, self.source.description)
        self.assertEqual((copy.brand, copy.model_reference, copy.composition), (self.source.brand, self.source.model_reference, self.source.composition))
        self.assertEqual(copy.benefits, self.source.benefits)
        self.assertEqual(copy.translations['en']['name'], 'Copie de Adult fleece')
        self.assertEqual(copy.information_sources, self.source.information_sources)
        self.assertEqual(copy.price, Decimal('80.00'))
        self.assertFalse(copy.is_published)
        self.assertFalse(copy.is_featured)
        self.assertEqual(copy.availability, 'unknown')
        self.assertIsNone(copy.source_url)
        self.assertIsNone(copy.duplicate_of_id)
        self.assertEqual(copy.static_image, '')
        self.assertFalse(copy.include_imported_gallery)
        variants = {v.color: v for v in copy.stock_variants.all()}
        self.assertEqual((variants['Rouge'].size, variants['Rouge'].price), ('M', Decimal('75.00')))
        self.assertEqual((variants['Vert'].size, variants['Vert'].price), ('L', None))
        for variant in variants.values():
            self.assertEqual((variant.quantity, variant.reserved, variant.reference, variant.revision), (0, 0, '', 1))
        self.red.refresh_from_db()
        self.assertEqual((self.red.quantity, self.red.reserved, self.red.reference, self.red.revision), (8, 2, 'INT-M', 4))
        self.source.refresh_from_db()
        self.assertTrue(self.source.is_published)
        self.assertTrue(self.source.is_featured)
        anonymous = Client()
        self.assertEqual(anonymous.get(reverse('core:product_detail', args=[copy.slug])).status_code, 404)
        self.assertTrue(LogEntry.objects.filter(user=self.manager, object_id=str(copy.pk), content_type__model='product', change_message__contains='catalog_duplicate').exists())

    def test_photos_are_new_files_and_source_deletion_cannot_break_copy(self):
        copy = self.duplicate().product
        pairs = [(self.source.main_image, copy.main_image), (self.gallery.image, copy.images.get().image), (self.color.image, copy.color_photos.get().image)]
        for original, duplicated in pairs:
            self.assertNotEqual(original.name, duplicated.name)
            with original.open('rb') as source_file, duplicated.open('rb') as duplicate_file:
                self.assertEqual(source_file.read(), duplicate_file.read())
            original.storage.delete(original.name)
            self.assertTrue(duplicated.storage.exists(duplicated.name))
        gallery = copy.images.get()
        self.assertEqual((gallery.alt, gallery.order), ('Dos de la polaire', 8))
        self.assertEqual(copy.color_photos.get().name, 'Vert')

    def test_static_main_imported_and_archived_alias_photos_become_ordinary_gallery(self):
        with TemporaryDirectory() as base:
            static_base = Path(base) / 'core/static'
            main_name = f'core/img/products/{self.source.slug}/00.png'
            second_name = f'core/img/products/{self.source.slug}/01.png'
            alias_name = 'core/img/products/old-alias/00.png'
            for name, color in [(main_name, 'red'), (second_name, 'blue'), (alias_name, 'green')]:
                path = static_base / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(photo('tmp.png', color).read())
            self.source.main_image = ''
            self.source.static_image = main_name
            self.source.save(update_fields=['main_image', 'static_image'])
            self.source.images.all().delete()
            Product.objects.create(name='Ancienne fiche', slug='old-alias', duplicate_of=self.source, is_published=False, price='80')
            with override_settings(BASE_DIR=Path(base), STATICFILES_DIRS=[static_base]):
                copy = self.duplicate().product
            self.assertTrue(copy.main_image.storage.exists(copy.main_image.name))
            self.assertEqual(copy.images.count(), 2)
            self.assertEqual(copy.static_image, '')
            self.assertFalse(copy.include_imported_gallery)
            self.assertIsNone(copy.source_url)
            self.assertIsNone(copy.duplicate_of_id)
            expected = {photo('tmp.png', 'blue').read(), photo('tmp.png', 'green').read()}
            actual = set()
            for image in copy.images.all():
                with image.image.open('rb') as file:
                    actual.add(file.read())
            self.assertEqual(actual, expected)

    def test_legacy_automatic_benefits_survive_new_slug(self):
        self.source.benefits = None
        self.source.save(update_fields=['benefits'])
        with patch('core.benefits.catalog_benefits', return_value=['Une poche pratique.', 'Une matière douce.']):
            copy = self.duplicate().product
        self.assertEqual(copy.benefit_lines, ['Une poche pratique.', 'Une matière douce.'])

    def test_promotions_and_previous_price_history_are_not_copied(self):
        promotion = ProductPromotion.objects.create(product=self.source, starts_at=timezone.now()-timedelta(days=1), ends_at=timezone.now()+timedelta(days=1), kind='percent', value=10)
        promotion.targets.add(self.red)
        old_ids = list(self.source.price_observations.values_list('pk', flat=True))
        PriceObservation.objects.filter(pk__in=old_ids).update(observed_at=timezone.now()-timedelta(days=60))
        before = timezone.now()
        copy = self.duplicate().product
        self.assertFalse(copy.promotions.exists())
        self.assertEqual(copy.price_observations.count(), 3)
        self.assertFalse(copy.price_observations.filter(observed_at__lt=before).exists())
        self.assertTrue(self.source.promotions.filter(pk=promotion.pk).exists())
        self.assertEqual(set(self.source.price_observations.values_list('pk', flat=True)), set(old_ids))

    def test_repeated_confirmation_reuses_copy_even_after_source_changes(self):
        token = self.token()
        first = self.duplicate(token)
        count = Product.objects.count()
        file_count = len(list(Path(self.media.name).rglob('*.*')))
        self.source.name = 'Source modifiée ensuite'
        self.source.save(update_fields=['name'])
        second = self.duplicate(token)
        self.assertTrue(second.reused)
        self.assertEqual(second.product.pk, first.product.pk)
        self.assertEqual(Product.objects.count(), count)
        self.assertEqual(len(list(Path(self.media.name).rglob('*.*'))), file_count)
        third = self.duplicate()
        self.assertNotEqual(third.product.pk, first.product.pk)

    def test_new_confirmation_for_same_name_gets_unique_slug(self):
        first, second = self.duplicate().product, self.duplicate().product
        self.assertEqual(first.name, second.name)
        self.assertNotEqual(first.slug, second.slug)

    def test_source_or_variant_change_requires_new_confirmation(self):
        for change in ('product', 'variant', 'collections'):
            with self.subTest(change=change):
                token = self.token()
                count = Product.objects.count()
                if change == 'product':
                    Product.objects.filter(pk=self.source.pk).update(description='Description mise à jour')
                elif change == 'variant':
                    Variant.objects.filter(pk=self.red.pk).update(price='74.00')
                else:
                    self.source.collections.clear()
                with self.assertRaises(DuplicationConflict):
                    self.duplicate(token)
                self.assertEqual(Product.objects.count(), count)

    def test_snapshot_is_bound_to_actor_source_and_expiry(self):
        token = self.token()
        other = get_user_model().objects.create_user('other-manager', email='other@example.test', is_staff=True)
        other.groups.add(Group.objects.get(name='Gestion du catalogue'))
        verify_test_user(other)
        with self.assertRaises(DuplicationError):
            duplicate_product(self.source.pk, snapshot=token, actor=other)
        with self.assertRaises(DuplicationError):
            duplicate_product(self.source.pk+1000, snapshot=token, actor=self.manager)
        with self.assertRaises(DuplicationError):
            self.duplicate(token+'tampered')
        with patch('django.core.signing.time.time', return_value=timezone.now().timestamp()+90000):
            with self.assertRaises(DuplicationError):
                self.duplicate(token)

    def test_failed_copy_cleans_new_files_and_rolls_back_rows(self):
        count = Product.objects.count()
        files = set(Path(self.media.name).rglob('*.*'))
        token = self.token()
        with patch('core.catalog_duplication.LogEntry.objects.log_actions', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                self.duplicate(token)
        self.assertEqual(Product.objects.count(), count)
        self.assertEqual(set(Path(self.media.name).rglob('*.*')), files)
        self.assertTrue(self.source.main_image.storage.exists(self.source.main_image.name))

    def test_missing_uploaded_photo_does_not_leave_partial_copy(self):
        self.gallery.image.storage.delete(self.gallery.image.name)
        count = Product.objects.count()
        files = set(Path(self.media.name).rglob('*.*'))
        with self.assertRaises(DuplicationError):
            self.duplicate()
        self.assertEqual(Product.objects.count(), count)
        self.assertEqual(set(Path(self.media.name).rglob('*.*')), files)

    def test_missing_static_photo_renders_recoverable_error(self):
        self.source.main_image = ''
        self.source.static_image = 'core/img/not-a-real-file.png'
        self.source.save(update_fields=['main_image', 'static_image'])
        response = self.client.get(self.url())
        self.assertContains(response, 'Une photo d’origine est introuvable', status_code=400)
        self.assertNotContains(response, 'Créer la copie en brouillon', status_code=400)

    def test_post_redirects_to_edit_and_stale_post_is_recoverable(self):
        response = self.client.get(self.url())
        token = response.context['form'].initial['snapshot']
        created = self.client.post(self.url(), {'snapshot':token})
        copy = Product.objects.get(name='Copie de Polaire adulte')
        self.assertRedirects(created, reverse('core:catalog_edit', args=[copy.pk]), fetch_redirect_response=False)
        token = self.token()
        self.source.description = 'Changement'
        self.source.save(update_fields=['description'])
        response = self.client.post(self.url(), {'snapshot':token})
        self.assertContains(response, 'Recharger la confirmation', status_code=409)
        self.assertEqual(Product.objects.filter(name='Copie de Polaire adulte').count(), 1)
        self.assertEqual(self.client.post(self.url(), {}).status_code, 400)

    def test_access_and_csrf_are_required_including_service(self):
        token = self.token()
        with self.assertRaises(PermissionDenied):
            duplicate_product(self.source.pk, snapshot=token, actor=self.customer)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.manager, backend='connexion.backends.EmailOrUsernameBackend')
        self.assertEqual(csrf.post(self.url(), {'snapshot':token}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url()).status_code, 302)
        self.assertEqual(self.client.post(self.url(), {'snapshot':token}).status_code, 302)
        self.client.force_login(self.customer, backend='connexion.backends.EmailOrUsernameBackend')
        self.assertEqual(self.client.get(self.url()).status_code, 403)
        self.assertEqual(self.client.post(self.url(), {'snapshot':token}).status_code, 403)

    def test_archived_duplicate_cannot_be_confirmed_or_opened_for_copy(self):
        canonical = Product.objects.create(name='Fiche conservée', slug='fiche-conservee-copie-test', price='80')
        self.source.duplicate_of = canonical
        self.source.is_published = False
        self.source.save(update_fields=['duplicate_of', 'is_published'])
        count = Product.objects.count()
        with self.assertRaisesMessage(DuplicationConflict, 'doublon archivé'):
            duplication_snapshot(self.source, actor=self.manager)
        for response in (self.client.get(self.url()), self.client.post(self.url(), {'snapshot': 'forged'})):
            self.assertContains(response, 'Cette fiche est un doublon archivé.', status_code=409)
            self.assertContains(response, reverse('core:catalog_duplicate', args=[canonical.pk]), status_code=409)
            self.assertNotContains(response, 'Créer la copie en brouillon', status_code=409)
        self.assertEqual(Product.objects.count(), count)

    def test_archiving_source_after_confirmation_blocks_service_and_post(self):
        token = self.token()
        canonical = Product.objects.create(name='Fiche conservée', slug='fiche-conservee-apres-test', price='80')
        Product.objects.filter(pk=self.source.pk).update(duplicate_of=canonical, is_published=False)
        count = Product.objects.count()
        files = set(Path(self.media.name).rglob('*.*'))
        with self.assertRaisesMessage(DuplicationConflict, 'doublon archivé'):
            duplicate_product(self.source.pk, snapshot=token, actor=self.manager)
        response = self.client.post(self.url(), {'snapshot': token})
        self.assertContains(response, 'Dupliquer la fiche conservée', status_code=409)
        self.assertEqual(Product.objects.count(), count)
        self.assertEqual(set(Path(self.media.name).rglob('*.*')), files)
