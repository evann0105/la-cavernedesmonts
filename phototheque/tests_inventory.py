from io import StringIO
from pathlib import Path
from types import SimpleNamespace
import tempfile
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.core.management import call_command
from django.db import transaction
from django.test import TestCase, override_settings

from accueil.models import HomepageConfiguration, HeroSlide
from boutique.models import ContentPage
from core.models import Product, ProductImage, ProductColor
from .inventory import MODEL_FIELDS, iter_sources, register_reference, sync_inventory
from .models import Folder, Photo
from .services import PhotoError, archive_photo, read_source, rename_folder, resolve_photo
from .signals import PhotoActorMiddleware, _actor
from .tests_backend import FilesMixin, image_bytes


class PhotoInventoryTests(FilesMixin, TestCase):
    def source(self, name='products/polaire.png', color='red'):
        path = Path(self.directory.name) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(image_bytes(color))
        return name

    def product(self, **kwargs):
        return Product.objects.create(name='Polaire', price='40.00', is_published=False, **kwargs)

    def test_inventory_copies_survive_replacement_or_removal_of_business_file(self):
        name = self.source()
        photo, created = register_reference('media', name, title='Polaire', folder_name='Produits')
        original = resolve_photo(photo.pk).read()
        self.assertTrue(created)
        self.assertEqual(photo.folder.system_key, 'products')
        Path(self.directory.name, name).write_bytes(image_bytes('blue'))
        self.assertEqual(resolve_photo(photo.pk).read(), original)
        Path(self.directory.name, name).unlink()
        self.assertEqual(resolve_photo(photo.pk).read(), original)

    def test_renamed_system_folder_is_used_by_future_captures(self):
        first, _ = register_reference('media', self.source(), folder_name='Produits')
        rename_folder(first.folder_id, 'Catalogue', actor=self.actor)
        second, _ = register_reference('media', self.source('products/bleu.png', 'blue'), folder_name='Produits')
        self.assertEqual(second.folder_id, first.folder_id)
        self.assertEqual(second.folder.name, 'Catalogue')
        self.assertFalse(Folder.objects.filter(name='Produits').exists())

    def test_multiple_sources_deduplicate_without_reactivating_archive(self):
        first, _ = register_reference('media', self.source(), folder_name='Produits')
        archive_photo(first.pk, actor=self.actor)
        second, created = register_reference('media', self.source('homepage/another.png'), folder_name='Accueil')
        self.assertEqual(second.pk, first.pk)
        self.assertFalse(created)
        self.assertIsNotNone(second.archived_at)
        self.assertEqual(second.folder.system_key, 'products')
        self.assertFalse(Folder.objects.filter(system_key='homepage').exists())

    def test_private_history_remote_traversal_and_symlink_sources_are_rejected(self):
        self.source()
        bad = ('private/client.png', 'historique-assets/photo.png', 'products/../private.png',
               'https://example.com/photo.png', '/products/a.png', 'products/a.ttf', 'products\\a.png')
        for name in bad:
            with self.subTest(name=name), self.assertRaises(PhotoError):
                read_source('media', name)
        original = Path(self.directory.name, 'products/polaire.png')
        link = Path(self.directory.name, 'products/link.png')
        link.symlink_to(original)
        with self.assertRaises(PhotoError):
            read_source('media', 'products/link.png')
        with self.assertRaises(PhotoError):
            read_source('static', 'fonts/picture.png')

    def test_missing_or_corrupt_sources_report_errors_without_empty_folders(self):
        name = self.source()
        Path(self.directory.name, name).write_bytes(b'not a picture')
        result = sync_inventory(sources=[
            {'kind': 'media', 'name': name, 'folder_name': 'Produits'},
            {'kind': 'media', 'name': 'products/missing.png', 'folder_name': 'Accueil'},
        ])
        self.assertEqual((result['created'], result['existing'], result['failed']), (0, 0, 2))
        self.assertFalse(Folder.objects.exists())
        self.assertFalse(Photo.objects.exists())

    def test_known_static_gallery_is_inventoried_without_scanning_unrelated_files(self):
        with tempfile.TemporaryDirectory() as repo, override_settings(BASE_DIR=Path(repo)):
            base = Path(repo, 'core/static')
            main = base / 'core/img/products/main.png'
            main.parent.mkdir(parents=True)
            main.write_bytes(image_bytes('red'))
            gallery = base / 'core/img/products/polaire/extra.webp'
            gallery.parent.mkdir(parents=True)
            gallery.write_bytes(image_bytes('blue'))
            unsafe = gallery.parent / 'unsafe.png'
            unsafe.symlink_to(main)
            self.source('products/unreferenced.png', 'green')
            product = self.product(static_image='core/img/products/main.png')
            with patch('accueil.schema.IMAGE_DEFAULTS', {}), patch('phototheque.services.finders.find', side_effect=lambda name: str(base / name) if (base / name).exists() else None):
                sources = list(iter_sources())
                self.assertIn('core/img/products/polaire/extra.webp', [item['name'] for item in sources])
                self.assertNotIn('products/unreferenced.png', [item['name'] for item in sources])
                result = sync_inventory(sources=[item for item in sources if item.get('folder_name') == 'Produits'])
            self.assertEqual((result['created'], result['failed']), (2, 1))
            self.assertEqual(Photo.objects.count(), 2)

    def test_business_images_are_registered_only_after_commit(self):
        name = self.source()
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            with transaction.atomic():
                self.product(main_image=name)
                self.assertFalse(Photo.objects.exists())
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(Photo.objects.count(), 1)

    def test_rollback_discards_capture_and_creates_no_library_files(self):
        name = self.source()
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            try:
                with transaction.atomic():
                    self.product(main_image=name)
                    raise RuntimeError('rollback')
            except RuntimeError:
                pass
        self.assertEqual(callbacks, [])
        self.assertFalse(Photo.objects.exists())
        self.assertFalse(Path(self.directory.name, 'phototheque').exists())

    def test_callback_failure_cannot_break_business_save_and_sync_can_retry(self):
        name = self.source()
        with patch('phototheque.signals.register_reference', side_effect=RuntimeError('temporary failure')):
            with self.captureOnCommitCallbacks(execute=True):
                product = self.product(main_image=name)
        self.assertTrue(Product.objects.filter(pk=product.pk).exists())
        self.assertFalse(Photo.objects.exists())
        result = sync_inventory(sources=[{'kind': 'media', 'name': name, 'folder_name': 'Produits'}])
        self.assertEqual(result['created'], 1)

    def test_only_last_committed_image_is_captured_and_metadata_edits_are_ignored(self):
        red = self.source()
        blue = self.source('products/blue.png', 'blue')
        with self.captureOnCommitCallbacks(execute=True):
            product = self.product(main_image=red)
            product.main_image = blue
            product.save(update_fields=['main_image'])
        self.assertEqual(Photo.objects.count(), 1)
        self.assertEqual(Photo.objects.get().original_name, 'blue.png')
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            product.price = '41.00'
            product.save(update_fields=['price'])
            product.save(update_fields=['main_image'])
        self.assertEqual(callbacks, [])

    def test_all_declared_business_image_fields_have_committed_capture(self):
        product = self.product()
        config = HomepageConfiguration.objects.create()
        page = ContentPage.objects.create(key='about')
        slide = HeroSlide.objects.create(configuration=config, position=1, alt='Montagne', image='')
        image = ProductImage.objects.create(product=product, image='')
        color = ProductColor.objects.create(product=product, name='Rouge', image='')
        records = [product, config, page, slide, image, color]
        count = 0
        with patch('phototheque.signals.register_reference') as register:
            for instance in records:
                for field in MODEL_FIELDS[instance._meta.label]:
                    count += 1
                    with self.captureOnCommitCallbacks(execute=True):
                        setattr(instance, field, f'products/photo-{count}.webp')
                        instance.save(update_fields=[field])
        self.assertEqual(register.call_count, 14)

    def test_capture_uses_middleware_actor_then_resets_context(self):
        def response(request):
            self.product(main_image=self.source())
            return 'ok'
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(PhotoActorMiddleware(response)(SimpleNamespace(user=self.actor)), 'ok')
        self.assertEqual(Photo.objects.get().uploaded_by, self.actor)
        self.assertIsNone(_actor.get())

    def test_capture_falls_back_to_page_updated_by(self):
        name = self.source('editorial/page.png')
        with self.captureOnCommitCallbacks(execute=True):
            ContentPage.objects.create(key='about', image=name, updated_by=self.actor)
        self.assertEqual(Photo.objects.get().uploaded_by, self.actor)

    def test_sync_command_is_repeatable_and_reports_counts(self):
        source = {'kind': 'media', 'name': self.source(), 'folder_name': 'Produits'}
        with patch('phototheque.inventory.iter_sources', return_value=[source, source]):
            out = StringIO()
            call_command('sync_phototheque', stdout=out)
            self.assertIn('1', out.getvalue())
            call_command('sync_phototheque', stdout=StringIO())
        self.assertEqual(Photo.objects.count(), 1)
