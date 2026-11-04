from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode
from unittest.mock import patch

from PIL import Image
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from connexion.test_helpers import verify_test_user
from . import services
from .models import Folder, Photo


def uploaded(name='photo-originale.png', color='red'):
    output = BytesIO()
    Image.new('RGB', (16, 12), color).save(output, format='PNG')
    return SimpleUploadedFile(name, output.getvalue(), content_type='image/png')


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'], EMAIL_DELIVERY_ENABLED=False, MEDIA_URL='/media/')
class PhotoLibraryViewsTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix='photo-library-ui-')
        self.addCleanup(self.directory.cleanup)
        self.media = override_settings(MEDIA_ROOT=self.directory.name)
        self.media.enable(); self.addCleanup(self.media.disable)
        self.actor = get_user_model().objects.create_user('photo-seller', is_staff=True)
        self.actor.user_permissions.add(Permission.objects.get(content_type__app_label='core', codename='manage_catalog'))
        verify_test_user(self.actor); self.client.force_login(self.actor)
        self.folder = services.create_folder('Collection hiver', actor=self.actor)
        self.photo = services.upload_photos([uploaded()], actor=self.actor, folder_id=self.folder.pk)[0][0]

    def url(self, name, **kwargs):
        return reverse('phototheque:'+name, kwargs=kwargs)

    def urls(self):
        return [self.url('overview'), self.url('photos_json'), self.url('upload'), self.url('folders'),
                self.url('folder_edit', pk=self.folder.pk), self.url('detail', pk=self.photo.pk),
                self.url('photo_action', pk=self.photo.pk, action='archive')]

    def test_routes_have_single_local_prefix(self):
        self.assertEqual(self.url('overview'), '/gestion/photos/')
        self.assertEqual(self.url('photos_json'), '/gestion/photos/photos.json')

    def test_all_pages_and_json_require_catalog_access(self):
        self.client.logout()
        for url in self.urls():
            self.assertEqual(self.client.get(url).status_code, 302)
        for staff in (False, True):
            user = get_user_model().objects.create_user('photo-no-access-'+str(staff), is_staff=staff)
            verify_test_user(user); self.client.force_login(user)
            for url in self.urls():
                self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(self.url('move'), {'photo_ids': [self.photo.pk]}).status_code, 403)

    def test_email_access_gate_applies_to_picker_and_pages(self):
        with patch('core.catalog_access.email_access_allowed', return_value=False):
            for url in self.urls():
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response['Location'], reverse('connexion:verify_email'))

    def test_get_requests_are_private_and_do_not_write_or_import_photos(self):
        files = sorted(str(path) for path in Path(self.directory.name).rglob('*') if path.is_file())
        for url in self.urls():
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertIn('private', response['Cache-Control'])
            self.assertIn('no-store', response['Cache-Control'])
            self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')
            self.assertFalse(any(query['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for query in queries), url)
        self.assertEqual(Photo.objects.count(), 1)
        self.assertEqual(files, sorted(str(path) for path in Path(self.directory.name).rglob('*') if path.is_file()))
        self.assertEqual(self.client.get(self.url('move')).status_code, 405)
        self.assertEqual(self.client.post(self.url('photos_json')).status_code, 405)

    def test_title_search_and_folder_filter_support_unfiled_and_archived(self):
        other = services.upload_photos([uploaded('bleu.png', 'blue')], actor=self.actor)[0][0]
        services.archive_photo(self.photo.pk, actor=self.actor)
        response = self.client.get(self.url('overview'), {'folder': 'unfiled'})
        self.assertEqual([row['photo'].pk for row in response.context['rows']], [other.pk])
        response = self.client.get(self.url('overview'), {'state': 'archived', 'folder': self.folder.pk, 'q': 'originale'})
        self.assertEqual([row['photo'].pk for row in response.context['rows']], [self.photo.pk])
        self.assertContains(response, 'réactiver')

    def test_picker_contract_uses_only_active_local_files_and_folder_list(self):
        response = self.client.get(self.url('photos_json'), {'folder': self.folder.pk, 'q': 'hiver'})
        data = response.json()
        self.assertEqual(data['folders'], [{'id': self.folder.pk, 'name': self.folder.name}])
        self.assertEqual(data['pagination'], {'page': 1, 'pages': 1, 'total': 1, 'has_next': False, 'has_previous': False})
        row = data['results'][0]
        self.assertEqual(set(row), {'id', 'title', 'alt', 'url', 'thumb', 'width', 'height', 'folder'})
        self.assertEqual(row['alt'], '')
        self.assertTrue(row['url'].startswith('/media/phototheque/images/'))
        self.assertTrue(row['thumb'].startswith('/media/phototheque/thumbnails/'))
        services.archive_photo(self.photo.pk, actor=self.actor)
        self.assertEqual(self.client.get(self.url('photos_json'), {'state': 'archived'}).json()['results'], [])

    def test_missing_or_unsafe_files_are_not_offered_and_detail_is_honest(self):
        Path(self.photo.image.path).unlink()
        self.assertEqual(self.client.get(self.url('photos_json')).json()['results'], [])
        self.assertContains(self.client.get(self.url('detail', pk=self.photo.pk)), 'fichier de cette photo est indisponible')
        unsafe = Photo.objects.create(title='Fichier étranger', image='../secret.png', thumbnail='https://example.invalid/photo.png',
            digest='a'*64, source_digest='b'*64, original_name='secret.png', width=1, height=1, size=1)
        response = self.client.get(self.url('detail', pk=unsafe.pk))
        self.assertNotContains(response, 'src="../secret.png"')
        self.assertNotContains(response, 'https://example.invalid')

    def test_search_titles_and_folder_names_are_escaped(self):
        payload = '<img src=x onerror=alert(1)>'
        services.update_photo(self.photo.pk, actor=self.actor, title=payload, folder_id=self.folder.pk)
        services.rename_folder(self.folder.pk, payload, actor=self.actor)
        response = self.client.get(self.url('overview'), {'q': payload})
        self.assertContains(response, '&lt;img src=x onerror=alert(1)&gt;')
        self.assertNotContains(response, '<img src=x onerror=alert(1)>')
        self.assertEqual(response.context['page_obj'].paginator.count, 1)

    def test_invalid_huge_filters_and_object_ids_return_controlled_errors(self):
        self.assertEqual(self.client.get(self.url('overview'), {'folder': '9'*30}).status_code, 400)
        self.assertEqual(self.client.get(self.url('photos_json'), {'folder': '9'*30}).status_code, 400)
        self.assertEqual(self.client.get(self.url('photos_json'), {'q': 'x'*161}).status_code, 400)
        self.assertEqual(self.client.get(self.url('detail', pk=10**30)).status_code, 404)
        self.assertEqual(self.client.post(self.url('detail', pk=self.photo.pk), {'title': 'Photo', 'folder': '9'*30}).status_code, 400)
        self.assertEqual(self.client.post(self.url('move'), {'photo_ids': ['9'*30]}).status_code, 400)

    def test_library_and_picker_are_paginated_and_keep_filters(self):
        for number in range(25):
            Photo.objects.create(title=f'Photo hiver {number}', image=self.photo.image.name, thumbnail=self.photo.thumbnail.name,
                digest=f'{number:064x}', source_digest=f'{number:064x}', original_name=f'hiver-{number}.png',
                width=16, height=12, size=self.photo.size, folder=self.folder)
        response = self.client.get(self.url('overview'), {'folder': self.folder.pk, 'q': 'hiver', 'page': 2})
        self.assertEqual(len(response.context['rows']), 2)
        self.assertIn('folder='+str(self.folder.pk), response.context['previous_url'])
        self.assertIn('q=hiver', response.context['previous_url'])
        data = self.client.get(self.url('photos_json'), {'folder': self.folder.pk, 'q': 'hiver'}).json()
        self.assertEqual(len(data['results']), 24)
        self.assertTrue(data['pagination']['has_next'])
        self.assertEqual(data['pagination']['total'], 26)

    def test_upload_preserves_original_name_optimizes_once_and_preselects_folder(self):
        back = urlencode({'folder': self.folder.pk})
        response = self.client.get(self.url('upload'), {'back': back})
        self.assertEqual(response.context['form']['folder'].value(), str(self.folder.pk))
        response = self.client.post(self.url('upload'), {'photos': uploaded('Polaire verte face.png', 'green'), 'folder': self.folder.pk, 'back': back})
        self.assertEqual(response.status_code, 302)
        photo = Photo.objects.get(original_name='Polaire verte face.png')
        self.assertEqual(photo.title, 'Polaire verte face')
        self.assertEqual(photo.folder_id, self.folder.pk)
        self.assertTrue(photo.image.name.endswith('.webp'))

    def test_upload_limit_and_invalid_image_do_not_create_any_photo(self):
        for files in ([], [uploaded() for _ in range(13)],
                      [uploaded('valid.png', 'green'), SimpleUploadedFile('bad.svg', b'<svg onload="alert(1)"></svg>', content_type='image/svg+xml')]):
            response = self.client.post(self.url('upload'), {'photos': files})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(Photo.objects.count(), 1)
        self.assertContains(response, 'sélectionnez à nouveau', status_code=400)

    def test_duplicate_archived_upload_does_not_reactivate_or_change_folder(self):
        services.archive_photo(self.photo.pk, actor=self.actor)
        response = self.client.post(self.url('upload'), {'photos': uploaded()})
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('phototheque:detail', args=[self.photo.pk]), response['Location'])
        self.photo.refresh_from_db()
        self.assertIsNotNone(self.photo.archived_at)
        self.assertEqual(self.photo.folder_id, self.folder.pk)
        self.assertEqual(Photo.objects.count(), 1)

    def test_multiple_duplicates_return_to_real_common_folder_instead_of_requested_folder(self):
        services.upload_photos([uploaded('bleu.png', 'blue')], actor=self.actor, folder_id=self.folder.pk)
        response = self.client.post(self.url('upload'), {'photos': [uploaded(), uploaded('bleu.png', 'blue')]})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], self.url('overview')+'?'+urlencode({'folder': self.folder.pk}))
        self.assertEqual(Photo.objects.count(), 2)

    def test_multiple_results_in_different_folders_return_to_all_photos(self):
        services.upload_photos([uploaded('bleu.png', 'blue')], actor=self.actor)
        response = self.client.post(self.url('upload'), {'photos': [uploaded(), uploaded('bleu.png', 'blue')], 'folder': self.folder.pk})
        self.assertEqual(response['Location'], self.url('overview'))
        self.assertEqual(Photo.objects.count(), 2)
        self.assertIsNone(Photo.objects.get(original_name='bleu.png').folder_id)

    def test_multiple_archived_duplicates_return_to_archives(self):
        other = services.upload_photos([uploaded('bleu.png', 'blue')], actor=self.actor, folder_id=self.folder.pk)[0][0]
        services.archive_photo(self.photo.pk, actor=self.actor)
        services.archive_photo(other.pk, actor=self.actor)
        response = self.client.post(self.url('upload'), {'photos': [uploaded(), uploaded('bleu.png', 'blue')]})
        self.assertEqual(response['Location'], self.url('overview')+'?'+urlencode({'folder': self.folder.pk, 'state': 'archived'}))
        self.assertEqual(Photo.objects.filter(archived_at__isnull=True).count(), 0)

    def test_detail_edits_name_and_folder_without_replacing_file(self):
        original_file = self.photo.image.name
        response = self.client.post(self.url('detail', pk=self.photo.pk), {'title': 'Photo renommée', 'folder': ''})
        self.assertEqual(response.status_code, 302)
        self.photo.refresh_from_db()
        self.assertEqual(self.photo.title, 'Photo renommée')
        self.assertIsNone(self.photo.folder_id)
        self.assertEqual(self.photo.image.name, original_file)

    def test_bulk_move_is_atomic_and_preserves_titles_and_images(self):
        other = services.upload_photos([uploaded('bleu.png', 'blue')], actor=self.actor)[0][0]
        destination = services.create_folder('Été', actor=self.actor)
        response = self.client.post(self.url('move'), {'photo_ids': [self.photo.pk, 999999], 'folder': destination.pk})
        self.assertEqual(response.status_code, 400)
        self.photo.refresh_from_db(); self.assertEqual(self.photo.folder_id, self.folder.pk)
        before = [(photo.title, photo.image.name) for photo in (self.photo, other)]
        response = self.client.post(self.url('move'), {'photo_ids': [self.photo.pk, other.pk], 'folder': destination.pk})
        self.assertEqual(response.status_code, 302)
        for number, photo in enumerate((self.photo, other)):
            photo.refresh_from_db()
            self.assertEqual(photo.folder_id, destination.pk)
            self.assertEqual((photo.title, photo.image.name), before[number])

    def test_archive_and_reactivate_require_confirmation_and_never_delete_files(self):
        for action, archived in [('archive', True), ('restore', False)]:
            url = self.url('photo_action', pk=self.photo.pk, action=action)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.client.post(url).status_code, 400)
            self.assertEqual(self.client.post(url, {'confirmed': 'on'}).status_code, 302)
            self.photo.refresh_from_db()
            self.assertEqual(self.photo.archived_at is not None, archived)
            self.assertTrue(Path(self.photo.image.path).exists())
        self.assertEqual(self.client.get(self.url('photo_action', pk=self.photo.pk, action='delete')).status_code, 404)

    def test_folder_create_rename_and_duplicate_errors_are_clear(self):
        response = self.client.post(self.url('folders'), {'name': 'Accueil'})
        self.assertEqual(response.status_code, 302)
        folder = Folder.objects.get(name='Accueil')
        response = self.client.post(self.url('folder_edit', pk=folder.pk), {'name': 'Vitrine'})
        self.assertEqual(response.status_code, 302)
        folder.refresh_from_db(); self.assertEqual(folder.name, 'Vitrine')
        response = self.client.post(self.url('folders'), {'name': 'vitrine'})
        self.assertContains(response, 'Un dossier porte déjà ce nom', status_code=400)

    def test_return_query_cannot_redirect_outside_the_library(self):
        for back in ('https://evil.invalid/path', 'next=https%3A%2F%2Fevil.invalid', 'folder=999999&next=https://evil.invalid'):
            response = self.client.post(self.url('detail', pk=self.photo.pk), {'title': 'Photo', 'folder': '', 'back': back})
            self.assertTrue(response['Location'].startswith(self.url('detail', pk=self.photo.pk)))
            self.assertNotIn('evil.invalid', response['Location'])

    def test_post_actions_require_csrf(self):
        client = Client(enforce_csrf_checks=True); client.force_login(self.actor)
        for url in [self.url('upload'), self.url('folders'), self.url('folder_edit', pk=self.folder.pk),
                    self.url('detail', pk=self.photo.pk), self.url('move'),
                    self.url('photo_action', pk=self.photo.pk, action='archive')]:
            self.assertEqual(client.post(url, {'title': 'Photo', 'confirmed': 'on'}).status_code, 403)

    def test_filter_and_bulk_folder_controls_have_distinct_accessible_ids(self):
        response = self.client.get(self.url('overview'))
        self.assertContains(response, 'id="id_folder"', count=1)
        self.assertContains(response, 'id="id_move_folder"', count=1)
        self.assertContains(response, 'for="id_move_folder"')
