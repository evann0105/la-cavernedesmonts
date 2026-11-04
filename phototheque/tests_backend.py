from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import tempfile
from threading import Barrier, Event, current_thread
from unittest import skipUnless
from unittest.mock import patch

from PIL import Image, PngImagePlugin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings

from connexion.test_helpers import verify_test_user
from .models import Folder, Photo
from .services import (PhotoContentFile, PhotoError, _normalize, archive_photo,
    create_folder, is_library_photo_file, photo_urls, rename_folder,
    resolve_photo, restore_photo, update_photo, upload_photos)


def image_bytes(color='red', size=(40, 30), mode='RGB', metadata=''):
    stream = BytesIO()
    info = PngImagePlugin.PngInfo()
    if metadata:
        info.add_text('Comment', metadata)
    Image.new(mode, size, color).save(stream, format='PNG', pnginfo=info)
    return stream.getvalue()


def upload(color='red', name='Ma photo.png', **kwargs):
    return SimpleUploadedFile(name, image_bytes(color, **kwargs), content_type='image/png')


class FilesMixin:
    def setUp(self):
        super().setUp()
        self.directory = tempfile.TemporaryDirectory(prefix='photo-library-test-')
        self.addCleanup(self.directory.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.directory.name, LOCAL_SKIP_EMAIL_VERIFICATION=False,
            PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.actor = get_user_model().objects.create_user('photos-admin', is_staff=True, is_superuser=True)
        verify_test_user(self.actor)

    def add_photo(self, **kwargs):
        return upload_photos([upload(**kwargs)], actor=self.actor)[0][0]

    def files(self):
        return sorted(path for path in Path(self.directory.name).rglob('*') if path.is_file())


class PhotoBackendTests(FilesMixin, TestCase):
    def test_optimized_copy_thumbnail_original_filename_and_transparency(self):
        photo = self.add_photo(name='Polaire verte.png', color=(0, 255, 0, 120), size=(3000, 1500), mode='RGBA')
        self.assertEqual(photo.original_name, 'Polaire verte.png')
        self.assertEqual(photo.title, 'Polaire verte')
        self.assertEqual((photo.width, photo.height), (2400, 1200))
        self.assertEqual(photo.uploaded_by, self.actor)
        with photo.image.open('rb') as stored:
            data = stored.read()
        self.assertEqual(photo.digest, sha256(data).hexdigest())
        self.assertEqual(photo.size, len(data))
        with Image.open(BytesIO(data)) as image:
            self.assertEqual(image.format, 'WEBP')
            self.assertIn('A', image.getbands())
            self.assertNotIn('exif', image.info)
        with Image.open(photo.thumbnail.path) as thumbnail:
            self.assertLessEqual(max(thumbnail.size), 360)

    def test_original_and_normalized_content_are_deduplicated_without_reencoding(self):
        first = self.add_photo()
        second, created = upload_photos([upload(name='Autre nom.png')], actor=self.actor)[0]
        self.assertEqual((second.pk, created), (first.pk, False))
        content = resolve_photo(first.pk)
        original = content.read()
        self.assertTrue(is_library_photo_file(content))
        self.assertEqual(content.tell(), 0)
        optimized, _, _, _ = _normalize(original)
        self.assertEqual(optimized, original)
        third, created = upload_photos([content], actor=self.actor)[0]
        self.assertEqual((third.pk, created), (first.pk, False))
        self.assertEqual(Photo.objects.count(), 1)
        self.assertEqual(len(self.files()), 2)

    def test_metadata_differences_deduplicate_and_result_keeps_upload_order(self):
        first = self.add_photo(metadata='first')
        result = upload_photos([upload('blue', name='Bleu.png'), upload(metadata='second')], actor=self.actor)
        self.assertEqual([item.title for item, _ in result], ['Bleu', first.title])
        self.assertEqual(result[1][0].pk, first.pk)
        self.assertFalse(result[1][1])

    def test_whole_batch_is_validated_before_any_write(self):
        for invalid in (b'not an image', b'<svg></svg>', b'wOFF\x00font'):
            with self.subTest(invalid=invalid), self.assertRaises(PhotoError):
                upload_photos([upload(), SimpleUploadedFile('bad.png', invalid)], actor=self.actor)
        self.assertFalse(Photo.objects.exists())
        self.assertEqual(self.files(), [])
        for files in ([], [upload() for _ in range(13)]):
            with self.assertRaises(PhotoError):
                upload_photos(files, actor=self.actor)
        with patch('phototheque.services.MAX_BYTES', 10), self.assertRaises(PhotoError):
            upload_photos([upload()], actor=self.actor)
        with patch('phototheque.services.MAX_PIXELS', 100), self.assertRaises(PhotoError):
            upload_photos([upload()], actor=self.actor)

    def test_folder_names_casefold_and_rename_conflicts(self):
        folder = create_folder('  Straße  ', actor=self.actor)
        self.assertEqual(folder.name, 'Straße')
        for name in ('STRASSE', 'ＳＴＲＡＳＳＥ'):
            with self.assertRaises(PhotoError):
                create_folder(name, actor=self.actor)
        second = create_folder('Saison', actor=self.actor)
        with self.assertRaises(PhotoError):
            rename_folder(second.pk, 'strasse', actor=self.actor)
        second.refresh_from_db()
        self.assertEqual(second.name, 'Saison')
        rename_folder(second.pk, 'Hiver', actor=self.actor)
        self.assertEqual(Folder.objects.get(pk=second.pk).name_key, 'hiver')

    def test_unauthorized_and_unverified_actors_cannot_mutate(self):
        photo = self.add_photo()
        folder = create_folder('Tests', actor=self.actor)
        client = get_user_model().objects.create_user('ordinary')
        unverified = get_user_model().objects.create_user('unverified', is_staff=True, is_superuser=True)
        for actor in (None, AnonymousUser(), client, unverified):
            operations = (
                lambda: upload_photos([upload()], actor=actor),
                lambda: create_folder('Autre', actor=actor),
                lambda: rename_folder(folder.pk, 'Autre', actor=actor),
                lambda: update_photo(photo.pk, actor=actor, title='Autre'),
                lambda: archive_photo(photo.pk, actor=actor),
                lambda: restore_photo(photo.pk, actor=actor),
            )
            for operation in operations:
                with self.subTest(actor=actor), self.assertRaises(PermissionDenied):
                    operation()
        self.assertEqual(Photo.objects.count(), 1)

    def test_rename_move_archive_and_explicit_restore_preserve_file(self):
        photo = self.add_photo()
        original = resolve_photo(photo.pk).read()
        folder = create_folder('Été', actor=self.actor)
        update_photo(photo.pk, actor=self.actor, title='Polaire', folder_id=folder.pk)
        photo.refresh_from_db()
        self.assertEqual((photo.title, photo.folder), ('Polaire', folder))
        archive_photo(photo.pk, actor=self.actor)
        with self.assertRaisesMessage(PhotoError, 'archivée'):
            resolve_photo(photo.pk)
        repeated, created = upload_photos([upload()], actor=self.actor)[0]
        self.assertFalse(created)
        self.assertIsNotNone(repeated.archived_at)
        restore_photo(photo.pk, actor=self.actor)
        self.assertEqual(resolve_photo(photo.pk).read(), original)
        update_photo(photo.pk, actor=self.actor, title='Polaire', folder_id=None)
        self.assertIsNone(Photo.objects.get(pk=photo.pk).folder_id)

    def test_bad_names_and_references_leave_existing_photo_unchanged(self):
        photo = self.add_photo()
        for title in ('', 'a' * 161, 'bad\x00name'):
            with self.assertRaises(PhotoError):
                update_photo(photo.pk, actor=self.actor, title=title)
        with self.assertRaises(PhotoError):
            update_photo(photo.pk, actor=self.actor, title='Valid', folder_id=999999)
        for identifier in ('../../private', -1, True, 999999, '1.2'):
            with self.assertRaises(PhotoError):
                resolve_photo(identifier)
        self.assertEqual(Photo.objects.get(pk=photo.pk).title, photo.title)

    def test_private_marker_requires_original_bytes_and_cannot_be_forged(self):
        photo = self.add_photo()
        trusted = resolve_photo(photo.pk)
        data = trusted.read()
        self.assertFalse(is_library_photo_file(ContentFile(data)))
        self.assertFalse(is_library_photo_file(PhotoContentFile(data, digest=photo.digest, token=object())))
        trusted.seek(0)
        trusted.write(b'broken')
        self.assertFalse(is_library_photo_file(trusted))

    def test_missing_corrupt_and_symlink_files_are_not_reusable(self):
        photo = self.add_photo()
        location = Path(photo.image.path)
        original = location.read_bytes()
        location.write_bytes(b'corrupt')
        with self.assertRaises(PhotoError):
            resolve_photo(photo.pk)
        with self.assertRaises(PhotoError):
            self.add_photo()
        location.unlink()
        self.assertEqual(photo_urls(photo)['url'], '')
        with self.assertRaises(PhotoError):
            resolve_photo(photo.pk)
        outside = Path(self.directory.name) / 'outside.webp'
        outside.write_bytes(original)
        location.symlink_to(outside)
        self.assertEqual(photo_urls(photo)['url'], '')
        with self.assertRaises(PhotoError):
            resolve_photo(photo.pk)

    def test_grid_and_resolution_do_not_write(self):
        photo = self.add_photo()
        before = [(path, path.stat().st_mtime_ns) for path in self.files()]
        with patch('phototheque.services.default_storage.save', side_effect=AssertionError('write')):
            self.assertTrue(photo_urls(photo)['thumb'])
            self.assertEqual(resolve_photo(photo.pk).read(), Path(photo.image.path).read_bytes())
        self.assertEqual(before, [(path, path.stat().st_mtime_ns) for path in self.files()])

    def test_photo_file_metadata_cannot_be_changed_or_deleted(self):
        photo = self.add_photo()
        with self.assertRaises(ValidationError):
            Photo.objects.filter(pk=photo.pk).update(digest='f' * 64)
        photo.width += 1
        with self.assertRaises(ValidationError):
            photo.save()
        with self.assertRaises(ValidationError):
            photo.delete()
        with self.assertRaises(ValidationError):
            Photo.objects.all().delete()

    def test_failed_database_insert_keeps_invisible_files_available_for_retry(self):
        with patch('phototheque.services.Photo.objects.create', side_effect=RuntimeError('database failure')):
            with self.assertRaises(RuntimeError):
                self.add_photo()
        self.assertFalse(Photo.objects.exists())
        files = self.files()
        self.assertEqual(len(files), 2)
        before = {path: path.read_bytes() for path in files}
        photo = self.add_photo()
        self.assertEqual(self.files(), files)
        self.assertEqual({path: path.read_bytes() for path in files}, before)
        self.assertTrue(is_library_photo_file(resolve_photo(photo.pk)))

    def test_writing_under_a_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as outside:
            (Path(self.directory.name) / 'phototheque').symlink_to(outside, target_is_directory=True)
            with self.assertRaises(PhotoError):
                self.add_photo()
            self.assertEqual(list(Path(outside).rglob('*')), [])
        self.assertFalse(Photo.objects.exists())


@skipUnless(connection.vendor == 'postgresql', 'PostgreSQL row/advisory locking required')
class PhotoConcurrencyTests(FilesMixin, TransactionTestCase):
    def _parallel(self, batches):
        barrier = Barrier(len(batches))
        actor_id = self.actor.pk
        def worker(colors):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=actor_id)
                barrier.wait(timeout=10)
                return [(item.pk, created) for item, created in upload_photos([upload(color) for color in colors], actor=actor)]
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(batches)) as pool:
            futures = [pool.submit(worker, batch) for batch in batches]
            return [future.result(timeout=30) for future in futures]

    def test_concurrent_identical_upload_creates_one_copy(self):
        results = self._parallel([['red'], ['red']])
        self.assertEqual(results[0][0][0], results[1][0][0])
        self.assertEqual(sum(created for result in results for _, created in result), 1)
        self.assertEqual(Photo.objects.count(), 1)
        self.assertEqual(len(self.files()), 2)

    def test_opposite_batch_order_cannot_deadlock_or_duplicate(self):
        results = self._parallel([['red', 'blue'], ['blue', 'red']])
        self.assertEqual([item[0] for item in results[0]], [item[0] for item in reversed(results[1])])
        self.assertEqual(Photo.objects.count(), 2)
        for photo in Photo.objects.all():
            self.assertTrue(is_library_photo_file(resolve_photo(photo.pk)))

    def test_concurrent_capture_can_adopt_files_from_failed_insert(self):
        written, adopter_started = Event(), Event()
        create = Photo.objects.create
        failing_thread = []
        def injected_create(**kwargs):
            if current_thread().name == failing_thread[0]:
                written.set()
                self.assertTrue(adopter_started.wait(timeout=10))
                raise RuntimeError('injected database failure')
            return create(**kwargs)
        def failed_upload():
            close_old_connections()
            failing_thread.append(current_thread().name)
            try:
                actor = get_user_model().objects.get(pk=self.actor.pk)
                with self.assertRaises(RuntimeError):
                    upload_photos([upload()], actor=actor)
            finally:
                close_old_connections()
        def successful_upload():
            close_old_connections()
            try:
                self.assertTrue(written.wait(timeout=10))
                actor = get_user_model().objects.get(pk=self.actor.pk)
                adopter_started.set()
                return upload_photos([upload()], actor=actor)[0][0].pk
            finally:
                close_old_connections()
        with patch('phototheque.services.Photo.objects.create', side_effect=injected_create):
            with ThreadPoolExecutor(max_workers=2) as pool:
                failed = pool.submit(failed_upload)
                successful = pool.submit(successful_upload)
                failed.result(timeout=30)
                photo_id = successful.result(timeout=30)
        self.assertEqual(Photo.objects.count(), 1)
        self.assertEqual(len(self.files()), 2)
        self.assertTrue(is_library_photo_file(resolve_photo(photo_id)))
