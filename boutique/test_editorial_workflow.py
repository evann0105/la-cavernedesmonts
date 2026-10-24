"""Publication workflow: private drafts, reversible history, and accurate banner status."""
from copy import deepcopy
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from connexion.models import EmailVerification
from connexion.test_helpers import verify_test_user
from .editorial_configuration import page_content, page_draft_content
from .editorial_forms import PageContentForm
from .editorial_services import (EditorialConflict, EditorialError, publish_page,
                                 restore_page_version, save_page)
from .models import ContentPage, ContentPageVersion, SiteAnnouncement
from .test_editorial import AT, PARIS, image


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True,
                   EMAIL_DELIVERY_ENABLED=False, TIME_ZONE='Europe/Paris')
class EditorialWorkflowTests(TestCase):
    backend = 'connexion.backends.EmailOrUsernameBackend'

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.media = Path(directory.name)
        settings = override_settings(MEDIA_ROOT=directory.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.manager = get_user_model().objects.create_user(
            'editorial-workflow-manager', email='workflow@example.test', is_staff=True)
        self.permission = Permission.objects.get(content_type__app_label='core', codename='manage_catalog')
        self.manager.user_permissions.add(self.permission)
        verify_test_user(self.manager)
        self.client.force_login(self.manager, backend=self.backend)

    def url(self, name, key='about', **kwargs):
        if key is not None:
            kwargs['key'] = key
        return reverse('editorial:' + name, kwargs=kwargs or None)

    def data(self, key='about', **changes):
        current = page_draft_content(key)
        values = dict(current['values'], revision=str(current['revision']))
        values.update(changes)
        return values

    def save(self, key='about', *, publish=True, **changes):
        data = self.data(key, **changes)
        upload = data.pop('image', None)
        form = PageContentForm(key, data, {'image': upload} if upload else None,
                               current=page_draft_content(key))
        self.assertTrue(form.is_valid(), form.errors)
        return save_page(key, form.cleaned_data, self.manager, publish=publish)

    def state(self):
        return tuple(list(model.objects.order_by('pk').values())
                     for model in (ContentPage, ContentPageVersion, SiteAnnouncement))

    def published(self, key='about'):
        return Client().get(reverse('espace:about' if key == 'about' else 'core:location'))

    def announcement(self, **changes):
        values = {'name': 'Annonce de test', 'kind': 'information', 'message': 'Message de test.',
                  'starts_at': AT - timedelta(hours=1), 'ends_at': AT + timedelta(hours=1),
                  'enabled': True, 'priority': 0, 'target': '', 'link_label': ''}
        values.update(changes)
        return SiteAnnouncement.objects.create(**values)

    @contextmanager
    def announcement_time(self):
        # Django sessions use the same timezone module as the announcement code.
        # Authenticate at the simulated time so the real one-hour session has
        # not already expired when these calendar checks run in the future.
        with patch('boutique.editorial_views.timezone.now', return_value=AT):
            self.client.force_login(self.manager, backend=self.backend)
            yield

    def announcement_data(self, row, **changes):
        values = {name: getattr(row, name) for name in
                  ('name', 'kind', 'message', 'target', 'link_label', 'priority', 'revision')}
        values.update({'starts_at': row.starts_at.astimezone(PARIS).strftime('%Y-%m-%dT%H:%M'),
                       'ends_at': row.ends_at.astimezone(PARIS).strftime('%Y-%m-%dT%H:%M'),
                       'enabled': 'on' if row.enabled else ''})
        values.update(changes)
        return values

    def test_saving_first_draft_never_changes_public_text_metadata_or_photo(self):
        before = deepcopy(page_content('about'))
        original_seo = deepcopy(self.published().context['seo'])
        row = self.save(publish=False, heading='Titre uniquement en préparation',
                        page_title='Titre SEO secret', intro='Description secrète non publiée',
                        image=image('blue'), image_alt='Photo en préparation')
        self.assertTrue(row.has_draft)
        self.assertEqual(page_content('about')['values'], before['values'])
        self.assertEqual(page_content('about')['image'], before['image'])
        public = self.published()
        self.assertEqual(public.context['seo'], original_seo)
        for marker in ('Titre uniquement en préparation', 'Titre SEO secret',
                       'Description secrète non publiée', 'Photo en préparation', row.draft_image.url):
            self.assertNotContains(public, marker)
        for version in row.versions.all():
            self.assertNotIn('Titre SEO secret', version.data.values())
        self.assertFalse(ContentPage.objects.filter(key='store').exists())

    def test_draft_photo_can_be_previewed_and_published_without_reuploading(self):
        row = self.save(publish=False, heading='Un aperçu avant publication', image=image('red'))
        draft_name, draft_url = row.draft_image.name, row.draft_image.url
        with Image.open(row.draft_image.path) as saved:
            self.assertEqual(saved.format, 'WEBP')
        before = self.state()
        frame = self.client.get(self.url('page_preview_frame'))
        self.assertContains(frame, 'Un aperçu avant publication')
        self.assertContains(frame, draft_url)
        self.assertEqual(self.state(), before)
        self.assertNotContains(self.published(), draft_url)
        response = self.client.post(self.url('page_publish'), {'revision': row.revision})
        self.assertEqual(response.status_code, 302)
        row.refresh_from_db()
        self.assertFalse(row.has_draft)
        self.assertEqual(row.image.name, draft_name)
        public = self.published()
        self.assertContains(public, 'Un aperçu avant publication')
        self.assertContains(public, draft_url)
        self.assertEqual(list(self.media.rglob('*.webp')), [Path(row.image.path)])

    def test_preview_action_saves_only_draft_and_redirects_to_its_private_preview(self):
        response = self.client.post(self.url('page_edit'), self.data(
            action='preview', heading='À vérifier avant de publier', image=image('yellow')))
        self.assertRedirects(response, self.url('page_preview'), fetch_redirect_response=False)
        row = ContentPage.objects.get(key='about')
        self.assertTrue(row.has_draft)
        self.assertNotContains(self.published(), 'À vérifier avant de publier')
        self.assertContains(self.client.get(self.url('page_preview_frame')), 'À vérifier avant de publier')

    def test_form_submission_without_explicit_publish_action_only_saves_a_draft(self):
        response = self.client.post(self.url('page_edit'), self.data(heading='Publication non demandée'))
        self.assertEqual(response.status_code, 302)
        row = ContentPage.objects.get(key='about')
        self.assertTrue(row.has_draft)
        self.assertNotContains(self.published(), 'Publication non demandée')
        self.assertContains(self.client.get(self.url('page_preview_frame')), 'Publication non demandée')

    def test_draft_edit_is_reloaded_and_keeps_existing_draft_photo(self):
        row = self.save(publish=False, heading='Premier brouillon', image=image('blue'))
        draft_name = row.draft_image.name
        response = self.client.get(self.url('page_edit'))
        self.assertEqual(response.context['form']['heading'].value(), 'Premier brouillon')
        self.assertContains(response, row.draft_image.url)
        row = self.save(publish=False, heading='Brouillon corrigé')
        self.assertEqual(row.draft_image.name, draft_name)
        self.assertEqual(page_draft_content('about')['values']['heading'], 'Brouillon corrigé')
        self.assertNotContains(self.published(), 'Brouillon corrigé')

    def test_photo_reset_is_a_draft_until_explicit_publication(self):
        default_image = page_content('about')['image']['url']
        row = self.save(image=image('green'))
        original_path, original_url = Path(row.image.path), row.image.url
        row = self.save(publish=False, **{'image-clear': 'on'})
        self.assertEqual(page_draft_content('about')['image']['url'], default_image)
        self.assertContains(self.published(), original_url)
        publish_page('about', row.revision, self.manager)
        self.assertEqual(page_content('about')['image']['url'], default_image)
        self.assertTrue(original_path.exists())

    def test_publications_preserve_default_and_each_previous_text_and_photo(self):
        defaults = deepcopy(page_content('about')['values'])
        first = self.save(heading='Première publication', image=image('red'))
        first_revision, first_image = first.published_revision, first.image.name
        second = self.save(heading='Deuxième publication', image=image('blue'))
        versions = {version.publication_revision: version for version in second.versions.all()}
        self.assertEqual(versions[0].data, defaults)
        self.assertFalse(versions[0].image)
        self.assertEqual(versions[first_revision].data['heading'], 'Première publication')
        self.assertEqual(versions[first_revision].image.name, first_image)
        self.assertEqual(versions[second.published_revision].data['heading'], 'Deuxième publication')
        self.assertEqual(versions[second.published_revision].image.name, second.image.name)
        self.assertTrue(versions[first_revision].image.storage.exists(first_image))

    def test_restoration_prepares_a_draft_and_preserves_public_version_until_published(self):
        first = self.save(heading='Texte historique', image=image('red'))
        first_version = first.versions.get(publication_revision=first.published_revision)
        first_photo = first.image.name
        row = self.save(heading='Texte actuellement publié', image=image('blue'))
        current_photo, current_revision = row.image.name, row.published_revision
        history_before = list(row.versions.order_by('pk').values())
        response = self.client.post(self.url('page_restore', pk=first_version.pk), {'revision': row.revision})
        self.assertEqual(response.status_code, 302)
        row.refresh_from_db()
        self.assertTrue(row.has_draft)
        self.assertEqual(row.draft_data['heading'], 'Texte historique')
        self.assertEqual(row.draft_image.name, first_photo)
        self.assertEqual(row.image.name, current_photo)
        self.assertEqual(row.published_revision, current_revision)
        self.assertContains(self.published(), 'Texte actuellement publié')
        self.assertEqual(list(row.versions.order_by('pk').values()), history_before)
        publish_page('about', row.revision, self.manager)
        row.refresh_from_db()
        self.assertEqual(row.image.name, first_photo)
        self.assertContains(self.published(), 'Texte historique')
        self.assertEqual(row.versions.count(), len(history_before) + 1)

    def test_restoring_original_version_restores_default_text_and_photo(self):
        original = deepcopy(page_content('about'))
        row = self.save(heading='Une nouvelle histoire', image=image('blue'))
        old_path = Path(row.image.path)
        baseline = row.versions.get(publication_revision=0)
        row = restore_page_version('about', baseline.pk, row.revision, self.manager)
        self.assertEqual(page_draft_content('about')['values'], original['values'])
        self.assertEqual(page_draft_content('about')['image']['url'], original['image']['url'])
        publish_page('about', row.revision, self.manager)
        self.assertEqual(page_content('about')['values'], original['values'])
        self.assertEqual(page_content('about')['image']['url'], original['image']['url'])
        self.assertTrue(old_path.exists())

    def test_stale_preview_cannot_publish_or_restore_over_a_newer_draft(self):
        row = self.save(heading='Version publique')
        baseline = row.versions.get(publication_revision=0)
        row = self.save(publish=False, heading='Brouillon vu dans un ancien aperçu')
        stale_revision = row.revision
        self.save(publish=False, heading='Dernier brouillon à conserver', image=image('green'))
        before, logs = self.state(), LogEntry.objects.count()
        for endpoint in (self.url('page_publish'), self.url('page_restore', pk=baseline.pk)):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.client.post(endpoint, {'revision': stale_revision}).status_code, 409)
                self.assertEqual(self.state(), before)
        with self.assertRaises(EditorialConflict):
            publish_page('about', stale_revision, self.manager)
        with self.assertRaises(EditorialConflict):
            restore_page_version('about', baseline.pk, stale_revision, self.manager)
        self.assertEqual(LogEntry.objects.count(), logs)
        self.assertContains(self.published(), 'Version publique')

    def test_old_edit_form_cannot_overwrite_a_newly_published_page(self):
        self.save(publish=False, heading='Ancien brouillon')
        stale = self.data(action='save_draft', heading='Texte périmé', image=image('blue'))
        row = ContentPage.objects.get(key='about')
        publish_page('about', row.revision, self.manager)
        before, files = self.state(), list(self.media.rglob('*.webp'))
        self.assertEqual(self.client.post(self.url('page_edit'), stale).status_code, 409)
        self.assertEqual(self.state(), before)
        self.assertEqual(list(self.media.rglob('*.webp')), files)

    def test_version_cannot_be_restored_or_previewed_under_a_different_page(self):
        about = self.save(heading='Histoire privée de version')
        version = about.versions.get(publication_revision=about.published_revision)
        store = self.save('store', title_line1='Présentation du magasin')
        before = self.state()
        for name in ('page_version_preview', 'page_version_frame'):
            self.assertEqual(self.client.get(self.url(name, key='store', pk=version.pk)).status_code, 404)
        self.assertEqual(self.client.post(self.url('page_restore', key='store', pk=version.pk),
                                         {'revision': store.revision}).status_code, 404)
        with self.assertRaises(EditorialError):
            restore_page_version('store', version.pk, store.revision, self.manager)
        self.assertEqual(self.state(), before)

    def test_historical_preview_is_read_only_and_does_not_change_current_draft(self):
        row = self.save(heading='Version publiée à consulter')
        version = row.versions.get(publication_revision=row.published_revision)
        self.save(publish=False, heading='Travail en cours à préserver')
        before = self.state()
        historical = self.client.get(self.url('page_version_frame', pk=version.pk))
        self.assertContains(historical, 'Version publiée à consulter')
        self.assertNotContains(historical, 'Travail en cours à préserver')
        self.assertEqual(self.state(), before)
        version.data['heading'] = 'Modification interdite'
        with self.assertRaises(ValidationError):
            version.save()
        self.assertEqual(self.state(), before)

    def test_private_previews_and_history_are_not_cacheable_indexable_or_mutating(self):
        row = self.save(heading='Publication de test')
        version = row.versions.get(publication_revision=row.published_revision)
        self.save(publish=False, heading='Aperçu non public <script>alert(1)</script>')
        before = self.state()
        endpoints = [self.url('page_preview'), self.url('page_preview_frame'), self.url('page_history'),
                     self.url('page_version_preview', pk=version.pk),
                     self.url('page_version_frame', pk=version.pk)]
        for path in endpoints:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn('no-store', response.headers['Cache-Control'])
                self.assertIn('noindex', response.headers['X-Robots-Tag'])
                self.assertEqual(self.client.post(path, {}).status_code, 405)
        for path in (self.url('page_preview_frame'), self.url('page_version_frame', pk=version.pk)):
            self.assertEqual(self.client.get(path).headers.get('X-Frame-Options'), 'SAMEORIGIN')
        frame = self.client.get(self.url('page_preview_frame'))
        self.assertContains(frame, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(frame, '<script>alert(1)</script>')
        self.assertEqual(self.state(), before)
        for path in (reverse('espace:about'), reverse('core:location'), '/'):
            public = Client().get(path, {'preview': '1', 'draft': '1', 'version': version.pk})
            self.assertNotContains(public, 'Aperçu non public')

    def test_workflow_endpoints_require_catalog_access_and_state_changes_require_csrf(self):
        row = self.save(publish=False, heading='Brouillon protégé')
        # A publication supplies an actual history row for the protected routes.
        row = publish_page('about', row.revision, self.manager)
        version = row.versions.get(publication_revision=row.published_revision)
        row = self.save(publish=False, heading='Brouillon toujours protégé')
        get_paths = [self.url('page_preview'), self.url('page_preview_frame'), self.url('page_history'),
                     self.url('page_version_preview', pk=version.pk),
                     self.url('page_version_frame', pk=version.pk)]
        post_paths = [self.url('page_publish'), self.url('page_restore', pk=version.pk)]
        before = self.state()
        for is_staff in (False, True):
            user = get_user_model().objects.create_user('workflow-forbidden-' + str(is_staff),
                email='forbidden-' + str(is_staff) + '@example.test', is_staff=is_staff)
            verify_test_user(user)
            browser = Client()
            browser.force_login(user, backend=self.backend)
            for path in get_paths:
                self.assertEqual(Client().get(path).status_code, 302)
                self.assertEqual(browser.get(path).status_code, 403)
            for path in post_paths:
                self.assertEqual(browser.post(path, {'revision': row.revision}).status_code, 403)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.manager, backend=self.backend)
        for path in post_paths:
            self.assertEqual(csrf.post(path, {'revision': row.revision}).status_code, 403)
            self.assertEqual(self.client.get(path).status_code, 405)
        EmailVerification.objects.filter(user=self.manager).delete()
        with override_settings(LOCAL_SKIP_EMAIL_VERIFICATION=False):
            for path in get_paths:
                self.assertRedirects(self.client.get(path), reverse('connexion:verify_email'),
                                     fetch_redirect_response=False)
        self.assertEqual(self.state(), before)

    def test_publish_and_restore_services_require_catalog_access_and_verified_email(self):
        row = self.save(heading='Version réservée à la vendeuse')
        version = row.versions.get(publication_revision=0)
        row = self.save(publish=False, heading='Ne pas publier sans autorisation')
        customer = get_user_model().objects.create_user('workflow-service-customer', email='customer@example.test')
        verify_test_user(customer)
        before = self.state()
        for actor in (customer, None):
            with self.assertRaises(PermissionDenied):
                publish_page('about', row.revision, actor)
            with self.assertRaises(PermissionDenied):
                restore_page_version('about', version.pk, row.revision, actor)
        EmailVerification.objects.filter(user=self.manager).delete()
        with override_settings(LOCAL_SKIP_EMAIL_VERIFICATION=False):
            with self.assertRaises(PermissionDenied):
                publish_page('about', row.revision, self.manager)
            with self.assertRaises(PermissionDenied):
                restore_page_version('about', version.pk, row.revision, self.manager)
        self.assertEqual(self.state(), before)

    def test_missing_revision_or_unknown_action_never_publishes_or_restores(self):
        row = self.save(heading='Texte public')
        version = row.versions.get(publication_revision=0)
        self.save(publish=False, heading='Texte privé')
        before = self.state()
        for name, kwargs in (('page_publish', {}), ('page_restore', {'pk': version.pk})):
            response = self.client.post(self.url(name, **kwargs), {})
            self.assertNotEqual(response.status_code, 302)
            self.assertEqual(self.state(), before)
        response = self.client.post(self.url('page_edit'), self.data(action='not-a-real-action', heading='Interdit'))
        self.assertNotEqual(response.status_code, 302)
        self.assertEqual(self.state(), before)

    def test_failed_draft_photo_save_does_not_change_publication_or_history_or_leave_files(self):
        self.save(heading='Dernière publication', image=image('red'))
        self.save(publish=False, heading='Dernier brouillon', image=image('blue'))
        before, files = self.state(), sorted(self.media.rglob('*.webp'))
        with patch.object(LogEntry.objects, 'log_actions', side_effect=RuntimeError('Échec simulé')):
            with self.assertRaises(RuntimeError):
                self.save(publish=False, heading='Brouillon à annuler', image=image('green'))
        self.assertEqual(self.state(), before)
        self.assertEqual(sorted(self.media.rglob('*.webp')), files)

    def test_existing_page_without_history_gets_its_previous_publication_preserved(self):
        values = deepcopy(page_content('about')['values'])
        values['heading'] = 'Texte publié avant les brouillons'
        ContentPage.objects.create(key='about', data=values, revision=7, published_revision=7,
                                   updated_by=self.manager)
        row = self.save(publish=False, heading='Première modification après mise à jour')
        baseline = row.versions.get(publication_revision=7)
        self.assertEqual(baseline.data, values)
        self.assertContains(self.published(), 'Texte publié avant les brouillons')
        row = publish_page('about', row.revision, self.manager)
        self.assertEqual(row.versions.count(), 2)
        baseline.refresh_from_db()
        self.assertEqual(baseline.data, values)

    def test_missing_history_photo_does_not_replace_a_valid_current_draft(self):
        row = self.save(heading='Ancienne image', image=image('red'))
        version = row.versions.get(publication_revision=row.published_revision)
        row = self.save(heading='Image publique actuelle', image=image('blue'))
        row = self.save(publish=False, heading='Brouillon à préserver')
        version.image.storage.delete(version.image.name)
        before = self.state()
        with self.assertRaises(EditorialError):
            restore_page_version('about', version.pk, row.revision, self.manager)
        self.assertEqual(self.state(), before)
        self.assertContains(self.published(), 'Image publique actuelle')

    def test_missing_draft_photo_blocks_both_publish_paths_and_can_be_replaced(self):
        self.save(heading='Page publique intacte', image=image('red'))
        row = self.save(publish=False, heading='Brouillon à réparer', image=image('blue'))
        row.draft_image.storage.delete(row.draft_image.name)
        before = self.state()
        with self.assertRaises(EditorialError):
            publish_page('about', row.revision, self.manager)
        with self.assertRaises(EditorialError):
            self.save(heading='Publication avec photo absente')
        self.assertEqual(self.state(), before)
        self.assertContains(self.published(), 'Page publique intacte')
        self.save(heading='Publication avec photo remplacée', image=image('green'))
        self.assertContains(self.published(), 'Publication avec photo remplacée')

    def test_announcement_list_names_the_same_single_winner_as_the_public_banner(self):
        normal = self.announcement(name='Annonce normale', message='Message normal')
        earlier = self.announcement(name='Prioritaire ancienne', priority=10,
                                    starts_at=AT - timedelta(hours=2))
        winner = self.announcement(name='Prioritaire récente', message='Message réellement affiché',
                                   priority=10, starts_at=AT - timedelta(minutes=30))
        upcoming = self.announcement(name='Annonce future', starts_at=AT + timedelta(hours=2),
                                     ends_at=AT + timedelta(hours=3), priority=20)
        draft = self.announcement(name='Brouillon prioritaire', priority=20, enabled=False)
        ended = self.announcement(name='Annonce terminée', starts_at=AT - timedelta(hours=3), ends_at=AT)
        with self.announcement_time():
            response = self.client.get(self.url('announcements', key=None))
            public = Client().get('/')
        rows = {row['announcement'].pk: row for row in response.context['announcement_rows']}
        self.assertEqual(response.context['active_announcement'].pk, winner.pk)
        self.assertEqual(rows[winner.pk]['display_state'], 'displayed')
        self.assertEqual(rows[winner.pk]['display_label'], 'Affichée sur le site')
        self.assertEqual(sum(row['display_state'] == 'displayed' for row in rows.values()), 1)
        for row in (normal, earlier):
            self.assertEqual(rows[row.pk]['display_state'], 'superseded')
            self.assertEqual(rows[row.pk]['display_label'], 'Non affichée · autre annonce prioritaire')
        self.assertEqual(rows[upcoming.pk]['display_state'], 'scheduled')
        self.assertEqual(rows[draft.pk]['display_state'], 'draft')
        self.assertEqual(rows[ended.pk]['display_state'], 'ended')
        self.assertContains(public, winner.message)
        self.assertNotContains(public, normal.message)
        self.assertContains(response, 'Affichée sur le site')
        self.assertContains(response, 'Non affichée · autre annonce prioritaire')

    def test_announcement_display_status_switches_exactly_at_the_end_boundary(self):
        first = self.announcement(name='Avant', starts_at=AT - timedelta(hours=1), ends_at=AT)
        second = self.announcement(name='Après', starts_at=AT, ends_at=AT + timedelta(hours=1))
        with self.announcement_time():
            response = self.client.get(self.url('announcements', key=None))
        rows = {row['announcement'].pk: row for row in response.context['announcement_rows']}
        self.assertEqual(rows[first.pk]['display_state'], 'ended')
        self.assertEqual(rows[second.pk]['display_state'], 'displayed')
        self.assertEqual(rows[first.pk]['overlap_count'], 0)
        self.assertEqual(rows[second.pk]['overlap_count'], 0)

    def test_overlap_warning_excludes_touching_boundaries_drafts_and_ended_conflicts(self):
        active = self.announcement(name='Active', starts_at=AT - timedelta(hours=2))
        upcoming = self.announcement(name='Chevauchement futur', starts_at=AT + timedelta(minutes=30),
                                     ends_at=AT + timedelta(hours=2))
        self.announcement(name='Commence juste après', starts_at=active.ends_at,
                          ends_at=AT + timedelta(hours=3))
        draft = self.announcement(name='Brouillon', enabled=False)
        ended = self.announcement(name='Chevauchement terminé', starts_at=AT - timedelta(hours=3),
                                  ends_at=AT)
        with self.announcement_time():
            response = self.client.get(self.url('announcements', key=None))
        rows = {row['announcement'].pk: row for row in response.context['announcement_rows']}
        self.assertEqual(rows[active.pk]['overlap_count'], 1)
        self.assertEqual(rows[draft.pk]['overlap_count'], 0)
        self.assertEqual(rows[ended.pk]['overlap_count'], 0)
        with self.announcement_time():
            edit = self.client.get(self.url('announcement_edit', key=None, pk=active.pk))
        self.assertEqual([row.pk for row in edit.context['overlaps']], [upcoming.pk])

    def test_overlap_warning_uses_submitted_dates_on_validation_error(self):
        edited = self.announcement(name='Annonce à déplacer', starts_at=AT + timedelta(days=4),
                                   ends_at=AT + timedelta(days=5))
        conflicting = self.announcement(name='Vacances déjà prévues', starts_at=AT + timedelta(days=1),
                                        ends_at=AT + timedelta(days=2))
        before = self.state()
        data = self.announcement_data(edited, name='',
            starts_at=(AT + timedelta(days=1, hours=1)).astimezone(PARIS).strftime('%Y-%m-%dT%H:%M'),
            ends_at=(AT + timedelta(days=1, hours=3)).astimezone(PARIS).strftime('%Y-%m-%dT%H:%M'))
        with self.announcement_time():
            get = self.client.get(self.url('announcement_edit', key=None, pk=edited.pk))
            post = self.client.post(self.url('announcement_edit', key=None, pk=edited.pk), data)
        self.assertEqual(list(get.context['overlaps']), [])
        self.assertEqual(post.status_code, 200)
        self.assertEqual([row.pk for row in post.context['overlaps']], [conflicting.pk])
        self.assertContains(post, conflicting.name)
        self.assertEqual(self.state(), before)

    def test_preview_and_history_of_untouched_page_never_create_a_publication(self):
        before = self.state()
        for name in ('page_preview', 'page_preview_frame', 'page_history'):
            self.assertEqual(self.client.get(self.url(name)).status_code, 200)
        self.assertEqual(self.state(), before)
        self.assertFalse(ContentPage.objects.exists())
        self.assertFalse(ContentPageVersion.objects.exists())

    def test_live_overlap_check_uses_paris_dates_excludes_self_and_touching_windows(self):
        row = self.announcement(name='Vacances à vérifier', starts_at=AT + timedelta(days=1),
                                 ends_at=AT + timedelta(days=1, hours=2))
        self.announcement(name='Juste avant', starts_at=row.starts_at - timedelta(hours=1),
                          ends_at=row.starts_at)
        self.announcement(name='Juste après', starts_at=row.ends_at,
                          ends_at=row.ends_at + timedelta(hours=1))
        self.announcement(name='Brouillon non affiché', starts_at=row.starts_at,
                          ends_at=row.ends_at, enabled=False)
        data = {'starts_at': row.starts_at.astimezone(PARIS).strftime('%Y-%m-%dT%H:%M'),
                'ends_at': row.ends_at.astimezone(PARIS).strftime('%Y-%m-%dT%H:%M')}
        before, logs = self.state(), LogEntry.objects.count()
        with self.announcement_time():
            response = self.client.get(self.url('announcement_overlap_check', key=None), data)
            excluded = self.client.get(self.url('announcement_overlap_check', key=None),
                                       {**data, 'exclude_pk': row.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['name'] for item in response.json()['overlaps']], [row.name])
        self.assertEqual(response.json()['overlaps'][0]['starts_at'], row.starts_at.isoformat())
        self.assertEqual(response.json()['error'], '')
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertIn('noindex', response.headers['X-Robots-Tag'])
        self.assertEqual(excluded.json()['overlaps'], [])
        self.assertEqual(self.state(), before)
        self.assertEqual(LogEntry.objects.count(), logs)

    def test_live_overlap_check_rejects_impossible_ambiguous_reversed_dates_and_bad_ids(self):
        before = self.state()
        path = self.url('announcement_overlap_check', key=None)
        cases = [
            {'starts_at': '2027-03-28T02:30', 'ends_at': '2027-03-28T05:00'},
            {'starts_at': '2027-10-31T02:30', 'ends_at': '2027-10-31T05:00'},
            {'starts_at': '2027-06-10T12:00', 'ends_at': '2027-06-10T12:00'},
            {'starts_at': '2027-06-10T12:00', 'ends_at': '2027-06-10T11:00'},
            {'starts_at': '2027-06-10T12:00', 'ends_at': '2027-06-10T13:00', 'exclude_pk': '-1'},
            {'starts_at': '2027-06-10T12:00', 'ends_at': '2027-06-10T13:00', 'exclude_pk': 'abc'},
        ]
        for data in cases:
            with self.subTest(data=data):
                response = self.client.get(path, data)
                self.assertEqual(response.status_code, 400)
                self.assertTrue(response.json()['error'])
                self.assertEqual(response.json()['overlaps'], [])
        self.assertEqual(self.state(), before)

    def test_live_overlap_check_requires_seller_access_and_accepts_only_get(self):
        path = self.url('announcement_overlap_check', key=None)
        self.assertEqual(Client().get(path).status_code, 302)
        customer = get_user_model().objects.create_user('overlap-customer', email='overlap@example.test')
        verify_test_user(customer)
        browser = Client()
        browser.force_login(customer, backend=self.backend)
        self.assertEqual(browser.get(path).status_code, 403)
        self.assertEqual(self.client.post(path, {}).status_code, 405)
        EmailVerification.objects.filter(user=self.manager).delete()
        with override_settings(LOCAL_SKIP_EMAIL_VERIFICATION=False):
            self.assertRedirects(self.client.get(path), reverse('connexion:verify_email'),
                                 fetch_redirect_response=False)
