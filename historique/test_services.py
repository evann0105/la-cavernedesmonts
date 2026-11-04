from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.test import TestCase, override_settings

from connexion.test_helpers import verify_test_user
from .exceptions import HistoryError
from .media import archive_files, asset_name, image_url, media_name, safe_name
from .models import HistoryVersion, RestorationReceipt
from .services import SALT, apply_restore, fingerprint, prepare_restore, record


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class SharedHistoryTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        settings = override_settings(MEDIA_ROOT=self.directory.name)
        settings.enable(); self.addCleanup(settings.disable)
        self.actor = get_user_model().objects.create_user('version-manager', is_staff=True, is_superuser=True)
        verify_test_user(self.actor)

    def version(self, snapshot=None, **kwargs):
        return record('product', 90001, snapshot or {'schema': 1, 'note': 'Texte privé à conserver'},
                      actor=self.actor, object_label='Produit témoin', **kwargs)

    def test_snapshot_is_encrypted_in_database_and_immutable_by_model(self):
        version = self.version()
        with connection.cursor() as cursor:
            cursor.execute('SELECT snapshot FROM historique_historyversion WHERE id=%s', [version.pk])
            raw = cursor.fetchone()[0]
        self.assertTrue(raw.startswith('enc:v1:'))
        self.assertNotIn('Texte privé', raw)
        version.refresh_from_db()
        self.assertEqual(version.snapshot['note'], 'Texte privé à conserver')
        with self.assertRaises(ValueError): version.save()
        with self.assertRaises(ValueError): version.delete()
        with self.assertRaises(ValueError): HistoryVersion.objects.filter(pk=version.pk).update(label='Écrasement')
        with self.assertRaises(ValueError): HistoryVersion.objects.filter(pk=version.pk).delete()
        with self.assertRaises(ValueError): HistoryVersion.objects.bulk_update([version], ['label'])

    def test_reconstructed_identity_cannot_overwrite_old_version(self):
        from django.db import IntegrityError
        version = self.version()
        rebuilt = HistoryVersion(pk=version.pk, scope='product', object_id=version.object_id,
            sequence=version.sequence, snapshot={'note': 'écrasement'}, fingerprint='x' * 64,
            object_label='Intrus', label='Intrus')
        with self.assertRaises(IntegrityError), transaction.atomic(): rebuilt.save()
        version.refresh_from_db()
        self.assertEqual(version.snapshot['note'], 'Texte privé à conserver')

    def test_deleted_actor_keeps_frozen_attribution(self):
        version = self.version()
        self.actor.delete()
        version.refresh_from_db()
        self.assertIsNone(version.actor_id)
        self.assertEqual(version.actor_label, 'version-manager')

    def test_fingerprint_is_stable_and_equal_states_do_not_duplicate(self):
        self.assertEqual(fingerprint({'b': Decimal('20.00'), 'a': 3}), fingerprint({'a': 3, 'b': '20.00'}))
        first = self.version()
        self.assertEqual(self.version().pk, first.pk)
        changed = self.version({'schema': 1, 'note': 'Autre texte'})
        self.assertEqual(changed.sequence, first.sequence + 1)

    def test_history_rolls_back_with_business_transaction(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self.version()
                raise RuntimeError('Enregistrement annulé')
        self.assertFalse(HistoryVersion.objects.exists())

    def test_preserved_asset_recovers_removed_and_overwritten_original(self):
        path = Path(self.directory.name) / 'products/photo.webp'
        path.parent.mkdir()
        path.write_bytes(b'ancienne photo')
        version = self.version(assets=[{'kind': 'media', 'name': 'products/photo.webp'}])
        archive = version.assets[0]['archive']
        self.assertEqual(media_name(version, 'products/photo.webp'), 'products/photo.webp')
        path.unlink()
        self.assertEqual(media_name(version, 'products/photo.webp'), archive)
        path.write_bytes(b'photo remplacee hors application')
        self.assertEqual(media_name(version, 'products/photo.webp'), archive)
        self.assertEqual((Path(self.directory.name) / archive).read_bytes(), b'ancienne photo')

    def test_assets_deduplicate_and_missing_asset_does_not_break_editing(self):
        path = Path(self.directory.name) / 'test.webp'
        path.write_bytes(b'image')
        assets = [{'kind': 'media', 'name': 'test.webp'}] * 2
        self.assertEqual(len(archive_files(assets)), 1)
        self.assertEqual(archive_files(assets)[0]['archive'], archive_files(assets)[0]['archive'])
        version = self.version(assets=[{'kind': 'media', 'name': 'absente.webp'}])
        self.assertTrue(version.assets[0]['missing'])
        self.assertEqual(image_url('absente.webp', version=version), '')
        with self.assertRaises(HistoryError): media_name(version, 'absente.webp')

    def test_unsafe_asset_paths_and_symlink_escape_are_rejected(self):
        for name in ('../photo.webp', '/private/photo.webp', 'https://site/photo.webp',
                     'products/../../photo.webp', 'products\\photo.webp', 'photo.txt', 'photo.webp?url=x'):
            with self.subTest(name=name), self.assertRaises(HistoryError): safe_name(name)
        outside = TemporaryDirectory(); self.addCleanup(outside.cleanup)
        (Path(outside.name) / 'private.webp').write_bytes(b'private')
        (Path(self.directory.name) / 'escape.webp').symlink_to(Path(outside.name) / 'private.webp')
        with self.assertRaises(HistoryError): archive_files([{'kind': 'media', 'name': 'escape.webp'}])

    def test_damaged_archive_cannot_restore_wrong_bytes(self):
        path = Path(self.directory.name) / 'test.webp'; path.write_bytes(b'original')
        version = self.version(assets=[{'kind': 'media', 'name': 'test.webp'}])
        path.unlink()
        (Path(self.directory.name) / version.assets[0]['archive']).write_bytes(b'damaged')
        with self.assertRaises(HistoryError): asset_name(version, 'media', 'test.webp')

    def test_confirmation_is_private_expires_and_is_bound_to_version_and_actor(self):
        version = self.version()
        backend = SimpleNamespace(restore_warnings=lambda entry: [])
        with patch('historique.services.snapshot_current', return_value={'note': 'état courant'}), patch('historique.services._backend', return_value=backend):
            prepared = prepare_restore(self.actor, version)
        payload = signing.loads(prepared['token'], salt=SALT)
        self.assertEqual(payload['actor'], self.actor.pk)
        self.assertEqual(payload['version'], version.pk)
        other = get_user_model().objects.create_user('version-other', is_staff=True, is_superuser=True)
        verify_test_user(other)
        for actor, token, expected in ((other, prepared['token'], version.pk),
                                       (self.actor, prepared['token'] + 'x', version.pk),
                                       (self.actor, prepared['token'], version.pk + 1)):
            with self.subTest(actor=actor.pk, expected=expected), self.assertRaises(HistoryError):
                apply_restore(actor, token, expected)
        with patch('django.core.signing.time.time', return_value=0):
            expired = signing.dumps(payload, salt=SALT)
        with self.assertRaises(HistoryError): apply_restore(self.actor, expired)
        ordinary = get_user_model().objects.create_user('ordinary-customer')
        with self.assertRaises(PermissionDenied): apply_restore(ordinary, prepared['token'])

    def test_repeated_confirmation_applies_once_and_role_is_rechecked(self):
        version = self.version()
        current = {'note': 'courant'}
        backend = SimpleNamespace(restore_warnings=lambda entry: [])
        with patch('historique.services.snapshot_current', return_value=current), patch('historique.services._backend', return_value=backend):
            prepared = prepare_restore(self.actor, version)
        result = self.version({'note': 'nouvelle version'}, source='restore', restored_from=version)
        backend.restore_product = Mock(return_value=result)
        with patch('historique.services._backend', return_value=backend):
            self.assertEqual(apply_restore(self.actor, prepared['token']).pk, result.pk)
            self.assertEqual(apply_restore(self.actor, prepared['token']).pk, result.pk)
        self.assertEqual(backend.restore_product.call_count, 1)
        self.assertEqual(RestorationReceipt.objects.count(), 1)
        self.actor.is_active = False
        with self.assertRaises(PermissionDenied): apply_restore(self.actor, prepared['token'])
