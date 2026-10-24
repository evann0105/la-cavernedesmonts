"""Seller editorial tools: scheduled publication, safe text, and atomic photo changes."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone as dt_timezone
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from connexion.models import EmailVerification
from connexion.test_helpers import verify_test_user
from .configuration import configuration as shop_configuration
from .models import ContentPage, FAQEntry, SiteAnnouncement, ShopConfiguration
from .editorial_configuration import active_announcement, page_content


AT = datetime(2027, 6, 10, 10, tzinfo=dt_timezone.utc)
PARIS = ZoneInfo('Europe/Paris')


def image(color='green'):
    buffer = BytesIO()
    Image.new('RGB', (240, 180), color).save(buffer, 'PNG')
    return SimpleUploadedFile('presentation.png', buffer.getvalue(), content_type='image/png')


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True,
                   EMAIL_DELIVERY_ENABLED=False, TIME_ZONE='Europe/Paris')
class EditorialTests(TestCase):
    backend = 'connexion.backends.EmailOrUsernameBackend'

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.media = Path(directory.name)
        settings = override_settings(MEDIA_ROOT=directory.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.manager = get_user_model().objects.create_user(
            'editorial-manager', email='editorial@example.test', is_staff=True)
        self.permission = Permission.objects.get(content_type__app_label='core', codename='manage_catalog')
        self.manager.user_permissions.add(self.permission)
        verify_test_user(self.manager)
        self.client.force_login(self.manager, backend=self.backend)

    def url(self, name, **kwargs):
        return reverse('editorial:' + name, kwargs=kwargs or None)

    def page_data(self, key='about', **changes):
        current = page_content(key)
        values = dict(current['values'], revision=str(current['revision']), action='publish')
        values.update(changes)
        return values

    def faq_data(self, row=None, **changes):
        values = {'revision': str(row.revision if row else 0),
                  'question': row.question if row else 'Comment préparer ma visite ?',
                  'answer': row.answer if row else 'Retrouvez les informations pratiques de la boutique.',
                  'target': row.target if row else '', 'link_label': row.link_label if row else '',
                  'position': str(row.position if row else 1),
                  'enabled': 'on' if row is None or row.enabled else ''}
        values.update(changes)
        return values

    def announcement_data(self, row=None, **changes):
        starts = row.starts_at if row else AT - timedelta(hours=1)
        ends = row.ends_at if row else AT + timedelta(hours=1)
        values = {'revision': str(row.revision if row else 0),
                  'name': row.name if row else 'Information de test',
                  'kind': row.kind if row else 'fermeture',
                  'message': row.message if row else 'Le magasin ferme exceptionnellement demain.',
                  'starts_at': starts.astimezone(PARIS).strftime('%Y-%m-%dT%H:%M'),
                  'ends_at': ends.astimezone(PARIS).strftime('%Y-%m-%dT%H:%M'),
                  'enabled': 'on' if row is None or row.enabled else '',
                  'priority': str(row.priority if row else 10),
                  'target': row.target if row else '', 'link_label': row.link_label if row else ''}
        values.update(changes)
        return values

    def announcement(self, **changes):
        values = {'name': 'Information interne', 'kind': 'fermeture',
                  'message': 'FERMETURE TEST PUBLIQUE', 'starts_at': AT - timedelta(hours=1),
                  'ends_at': AT + timedelta(hours=1), 'enabled': True, 'priority': 10,
                  'target': '', 'link_label': ''}
        values.update(changes)
        return SiteAnnouncement.objects.create(**values)

    def values(self):
        return tuple(list(model.objects.order_by('pk').values())
                     for model in (ContentPage, FAQEntry, SiteAnnouncement, ShopConfiguration))

    def endpoints(self):
        return [self.url('overview'), self.url('page_edit', key='about'),
                self.url('page_edit', key='store'), self.url('faq_list'),
                self.url('faq_create'), self.url('announcements'), self.url('announcement_create')]

    def test_default_pages_and_get_requests_are_read_only(self):
        before, logs = self.values(), LogEntry.objects.count()
        self.assertFalse(ContentPage.objects.exists())
        for key in ('about', 'store'):
            self.assertEqual(page_content(key)['revision'], 0)
            self.assertTrue(page_content(key)['values'])
        for path in self.endpoints():
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn('no-store', response.headers['Cache-Control'])
        for path in ('/', reverse('espace:about'), reverse('core:location'), self.url('faq'), '/panier/'):
            with self.subTest(public=path):
                self.assertEqual(Client().get(path).status_code, 200)
        self.assertEqual(self.values(), before)
        self.assertEqual(LogEntry.objects.count(), logs)

    def test_management_permissions_exclude_anonymous_customers_plain_staff_and_other_roles(self):
        before = self.values()
        for path in self.endpoints():
            self.assertEqual(Client().get(path).status_code, 302)
            self.assertEqual(Client().post(path, {}).status_code, 302)
        roles = [('editorial-customer', False, None), ('editorial-staff', True, None),
                 ('editorial-nonstaff', False, self.permission),
                 ('editorial-returns', True, Permission.objects.get(
                     content_type__app_label='retours', codename='manage_returns'))]
        for username, staff, permission in roles:
            user = get_user_model().objects.create_user(username, email=username + '@example.test', is_staff=staff)
            if permission:
                user.user_permissions.add(permission)
            verify_test_user(user)
            browser = Client()
            browser.force_login(user, backend=self.backend)
            for path in self.endpoints():
                with self.subTest(user=username, path=path):
                    self.assertEqual(browser.get(path).status_code, 403)
                    self.assertEqual(browser.post(path, {}).status_code, 403)
        self.assertEqual(self.values(), before)

    def test_management_requires_email_confirmation_outside_local_mode(self):
        EmailVerification.objects.filter(user=self.manager).delete()
        before = self.values()
        for flags in ({'DEBUG': False, 'LOCAL_SKIP_EMAIL_VERIFICATION': True},
                      {'DEBUG': True, 'LOCAL_SKIP_EMAIL_VERIFICATION': False}):
            with self.subTest(flags=flags), override_settings(**flags):
                for path in self.endpoints():
                    self.assertRedirects(self.client.get(path), reverse('connexion:verify_email'),
                                         fetch_redirect_response=False)
        self.assertEqual(self.values(), before)

    def test_form_posts_require_csrf_and_accept_a_real_form_token(self):
        browser = Client(enforce_csrf_checks=True)
        browser.force_login(self.manager, backend=self.backend)
        before = self.values()
        submissions = [(self.url('page_edit', key='about'), self.page_data()),
                       (self.url('faq_create'), self.faq_data()),
                       (self.url('announcement_create'), self.announcement_data())]
        for path, data in submissions:
            self.assertEqual(browser.post(path, data).status_code, 403)
        self.assertEqual(self.values(), before)
        self.assertEqual(browser.get(self.url('page_edit', key='about')).status_code, 200)
        self.assertEqual(browser.post(self.url('page_edit', key='about'), self.page_data(),
            HTTP_X_CSRFTOKEN=browser.cookies['csrftoken'].value).status_code, 302)

    def test_unsupported_methods_and_unknown_page_never_mutate_content(self):
        before = self.values()
        for path in self.endpoints():
            self.assertEqual(self.client.put(path, {}).status_code, 405)
            self.assertEqual(self.client.delete(path, {}).status_code, 405)
        self.assertEqual(self.client.get(self.url('page_edit', key='inconnue')).status_code, 404)
        self.assertEqual(self.values(), before)

    def test_independent_page_texts_are_escaped_and_do_not_overwrite_shop_details(self):
        before = deepcopy(shop_configuration())
        hostile = '<script>alert("visite")</script>'
        response = self.client.post(self.url('page_edit', key='about'), self.page_data(
            heading='Une histoire de boutique', body=hostile + '\n\nÀ bientôt !',
            email='injection@example.test', store_address_line1='Adresse injectée'))
        self.assertEqual(response.status_code, 302)
        about = ContentPage.objects.get(key='about')
        self.assertEqual(about.revision, 1)
        self.assertEqual(about.updated_by_id, self.manager.pk)
        self.assertEqual(about.data['body'], hostile + '\n\nÀ bientôt !')
        self.assertEqual(shop_configuration(), before)
        self.assertFalse(ContentPage.objects.filter(key='store').exists())
        public = Client().get(reverse('espace:about'))
        self.assertContains(public, 'Une histoire de boutique')
        self.assertContains(public, '&lt;script&gt;')
        self.assertNotContains(public, hostile)
        self.assertEqual(self.client.post(self.url('page_edit', key='store'), self.page_data(
            'store', title_line1='Le magasin vous accueille', story_body='Une présentation personnalisée.')).status_code, 302)
        store = Client().get(reverse('core:location'))
        self.assertContains(store, 'Le magasin vous accueille')
        self.assertContains(store, 'Une présentation personnalisée.')
        self.assertContains(store, before['phone'])
        about.refresh_from_db()
        self.assertEqual(about.data['heading'], 'Une histoire de boutique')
        self.assertEqual(shop_configuration(), before)

    def test_page_photo_is_optimized_and_can_be_reset_without_deleting_its_original_file(self):
        default = page_content('about')['image']['url']
        response = self.client.post(self.url('page_edit', key='about'), self.page_data(
            image=image(), image_alt='Façade de la boutique au soleil'))
        self.assertEqual(response.status_code, 302)
        row = ContentPage.objects.get(key='about')
        saved_path = Path(row.image.path)
        with Image.open(saved_path) as uploaded:
            self.assertEqual(uploaded.format, 'WEBP')
            self.assertEqual(uploaded.size, (240, 180))
        public = Client().get(reverse('espace:about'))
        self.assertContains(public, row.image.url)
        self.assertContains(public, 'Façade de la boutique au soleil')
        self.assertEqual(self.client.post(self.url('page_edit', key='about'), self.page_data(
            **{'image-clear': 'on'})).status_code, 302)
        row.refresh_from_db()
        self.assertFalse(row.image)
        self.assertEqual(page_content('about')['image']['url'], default)
        self.assertTrue(saved_path.exists())

    def test_invalid_photo_or_text_never_partially_saves_page_or_files(self):
        bad = SimpleUploadedFile('presentation.svg', b'<svg onload="alert(1)"/>', content_type='image/svg+xml')
        for changes in ({'image': bad}, {'heading': '', 'image': image()},
                        {'body': 'x' * 6001, 'image': image()}):
            with self.subTest(fields=list(changes)):
                response = self.client.post(self.url('page_edit', key='about'), self.page_data(**changes))
                self.assertEqual(response.status_code, 200)
                self.assertFalse(ContentPage.objects.exists())
                self.assertFalse(list(self.media.rglob('*.webp')))

    def test_stale_page_upload_and_duplicate_initial_submission_never_overwrite(self):
        stale = self.page_data(heading='Ancien onglet', image=image())
        self.assertEqual(self.client.post(self.url('page_edit', key='about'), self.page_data(
            heading='Le dernier texte enregistré')).status_code, 302)
        before, logs = self.values(), LogEntry.objects.count()
        response = self.client.post(self.url('page_edit', key='about'), stale)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.values(), before)
        self.assertEqual(LogEntry.objects.count(), logs)
        self.assertFalse(list(self.media.rglob('*.webp')))
        self.assertEqual(page_content('about')['values']['heading'], 'Le dernier texte enregistré')

    def test_faq_can_be_created_ordered_hidden_and_reactivated_without_losing_its_answer(self):
        FAQEntry.objects.all().delete()
        payload = self.faq_data(question='Question B', answer='Réponse à conserver.', position='20')
        self.assertEqual(self.client.post(self.url('faq_create'), payload).status_code, 302)
        second = FAQEntry.objects.get(question='Question B')
        self.assertEqual(second.updated_by_id, self.manager.pk)
        self.assertEqual(self.client.post(self.url('faq_create'), self.faq_data(
            question='Question A', answer='Autre réponse.', position='10',
            target='livraison', link_label='Consulter la livraison')).status_code, 302)
        public = Client().get(self.url('faq'))
        self.assertContains(public, 'Question A')
        self.assertContains(public, 'Question B')
        self.assertLess(public.content.index(b'Question A'), public.content.index(b'Question B'))
        self.assertContains(public, reverse('boutique:shipping_information'))
        self.assertEqual(self.client.post(self.url('faq_edit', pk=second.pk), self.faq_data(
            second, enabled='')).status_code, 302)
        second.refresh_from_db()
        self.assertFalse(second.enabled)
        self.assertEqual(second.answer, 'Réponse à conserver.')
        self.assertNotContains(Client().get(self.url('faq')), 'Question B')
        self.assertEqual(self.client.post(self.url('faq_edit', pk=second.pk), self.faq_data(
            second, enabled='on', position='1')).status_code, 302)
        public = Client().get(self.url('faq'))
        self.assertLess(public.content.index(b'Question B'), public.content.index(b'Question A'))

    def test_faq_plain_text_is_escaped_and_stale_form_cannot_restore_hidden_entry(self):
        FAQEntry.objects.all().delete()
        row = FAQEntry.objects.create(question='<em>Question test</em>', answer='<script>evil()</script>', position=1)
        public = Client().get(self.url('faq'))
        self.assertContains(public, '&lt;em&gt;Question test&lt;/em&gt;')
        self.assertContains(public, '&lt;script&gt;evil()&lt;/script&gt;')
        self.assertNotContains(public, '<script>evil()</script>')
        stale = self.faq_data(row, answer='Réponse ancienne')
        self.assertEqual(self.client.post(self.url('faq_edit', pk=row.pk), self.faq_data(
            row, enabled='')).status_code, 302)
        before = self.values()
        self.assertEqual(self.client.post(self.url('faq_edit', pk=row.pk), stale).status_code, 409)
        self.assertEqual(self.values(), before)
        self.assertNotContains(Client().get(self.url('faq')), 'Question test')

    def test_question_link_and_position_validation_prevents_unsafe_or_incomplete_links(self):
        before = self.values()
        for changes in ({'target': 'javascript:alert(1)', 'link_label': 'Ouvrir'},
                        {'target': 'https://evil.example/', 'link_label': 'Ouvrir'},
                        {'target': 'contact', 'link_label': ''}, {'target': '', 'link_label': 'Ouvrir'},
                        {'position': '0'}, {'position': '1001'}, {'question': ''}, {'answer': ''}):
            with self.subTest(changes=changes):
                response = self.client.post(self.url('faq_create'), self.faq_data(**changes))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.values(), before)

    def test_announcement_window_is_start_inclusive_end_exclusive_without_writes(self):
        row = self.announcement(starts_at=AT, ends_at=AT + timedelta(hours=1))
        before, logs = self.values(), LogEntry.objects.count()
        self.assertIsNone(active_announcement(at=AT - timedelta(microseconds=1)))
        self.assertEqual(active_announcement(at=AT).pk, row.pk)
        self.assertEqual(active_announcement(at=row.ends_at - timedelta(microseconds=1)).pk, row.pk)
        self.assertIsNone(active_announcement(at=row.ends_at))
        self.assertEqual(self.values(), before)
        self.assertEqual(LogEntry.objects.count(), logs)

    def test_active_announcement_priority_and_ties_are_deterministic(self):
        normal = self.announcement(name='Normale', priority=0)
        earlier = self.announcement(name='Importante ancienne', priority=10, starts_at=AT - timedelta(hours=2))
        latest = self.announcement(name='Importante récente', priority=10, starts_at=AT - timedelta(minutes=30))
        self.assertEqual(active_announcement(at=AT).pk, latest.pk)
        same_start = self.announcement(name='Même début créée après', priority=10, starts_at=latest.starts_at)
        self.assertEqual(active_announcement(at=AT).pk, same_start.pk)
        urgent = self.announcement(name='Prioritaire', priority=20, starts_at=earlier.starts_at)
        self.assertEqual(active_announcement(at=AT).pk, urgent.pk)
        urgent.enabled = False
        urgent.save(update_fields=['enabled'])
        self.assertEqual(active_announcement(at=AT).pk, same_start.pk)
        self.assertEqual(normal.priority, 0)

    def test_future_expired_and_disabled_announcements_are_never_public(self):
        self.announcement(message='ANNONCE FUTURE TEST', starts_at=AT + timedelta(hours=1), ends_at=AT + timedelta(hours=2))
        self.announcement(message='ANNONCE TERMINÉE TEST', starts_at=AT - timedelta(hours=2), ends_at=AT)
        self.announcement(message='ANNONCE MASQUÉE TEST', enabled=False, priority=20)
        self.assertIsNone(active_announcement(at=AT))
        with patch('boutique.editorial_configuration.timezone.now', return_value=AT):
            for path in ('/', reverse('core:location'), '/panier/'):
                public = Client().get(path)
                for marker in ('ANNONCE FUTURE TEST', 'ANNONCE TERMINÉE TEST', 'ANNONCE MASQUÉE TEST'):
                    self.assertNotContains(public, marker)

    def test_active_banner_is_global_escapes_text_and_has_only_a_safe_internal_link(self):
        row = self.announcement(message='Fermeture <script>alerte()</script>', name='NOM INTERNE NON PUBLIC',
                                 target='contact', link_label='Nous écrire <b>ici</b>')
        with patch('boutique.editorial_configuration.timezone.now', return_value=AT):
            for path in ('/', reverse('core:location'), reverse('espace:about'), '/panier/', self.url('faq')):
                with self.subTest(path=path):
                    response = Client().get(path)
                    self.assertEqual(response.status_code, 200)
                    self.assertContains(response, 'Fermeture &lt;script&gt;alerte()&lt;/script&gt;')
                    self.assertContains(response, 'Nous écrire &lt;b&gt;ici&lt;/b&gt;')
                    self.assertContains(response, reverse('espace:contact'))
                    self.assertNotContains(response, '<script>alerte()</script>')
                    self.assertNotContains(response, row.name)
        with patch('boutique.editorial_configuration.timezone.now', return_value=row.ends_at):
            self.assertNotContains(Client().get('/'), 'Fermeture &lt;script&gt;alerte()&lt;/script&gt;')

    def test_announcement_schedule_is_saved_in_paris_time_even_under_another_current_zone(self):
        from django.utils import timezone
        data = self.announcement_data(starts_at='2027-07-10T09:30', ends_at='2027-07-10T18:00')
        with timezone.override('America/New_York'):
            self.assertEqual(self.client.post(self.url('announcement_create'), data).status_code, 302)
        row = SiteAnnouncement.objects.get(name=data['name'])
        self.assertEqual(row.starts_at, datetime(2027, 7, 10, 7, 30, tzinfo=dt_timezone.utc))
        self.assertEqual(row.ends_at, datetime(2027, 7, 10, 16, 0, tzinfo=dt_timezone.utc))
        self.assertEqual(row.updated_by_id, self.manager.pk)
        self.assertEqual(row.revision, 1)

    def test_impossible_ambiguous_and_reversed_dates_are_rejected_without_publication(self):
        before = self.values()
        periods = [
            ('2027-03-28T02:30', '2027-03-28T05:00'),
            ('2027-10-31T02:30', '2027-10-31T05:00'),
            ('2027-07-10T09:30', '2027-07-10T09:30'),
            ('2027-07-10T09:30', '2027-07-09T09:30'),
        ]
        for starts, ends in periods:
            with self.subTest(starts=starts, ends=ends):
                response = self.client.post(self.url('announcement_create'), self.announcement_data(
                    starts_at=starts, ends_at=ends))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.values(), before)
                self.assertTrue(response.context['form'].errors)

    def test_unsafe_banner_targets_kinds_priorities_and_unpaired_links_never_save(self):
        before = self.values()
        for changes in ({'target': 'javascript:alert(1)', 'link_label': 'Ouvrir'},
                        {'target': '//evil.example/', 'link_label': 'Ouvrir'},
                        {'target': 'commandes', 'link_label': 'Ouvrir'},
                        {'target': 'contact', 'link_label': ''},
                        {'target': '', 'link_label': 'Nous contacter'}, {'priority': '100'},
                        {'kind': '<script>evil()</script>'}, {'message': ''}, {'message': 'x' * 301}):
            with self.subTest(changes=changes):
                response = self.client.post(self.url('announcement_create'), self.announcement_data(**changes))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.values(), before)

    def test_stale_announcement_cannot_reactivate_or_overwrite_a_newer_message(self):
        row = self.announcement()
        stale = self.announcement_data(row, message='Ancien message à ne pas publier')
        self.assertEqual(self.client.post(self.url('announcement_edit', pk=row.pk), self.announcement_data(
            row, message='Message enregistré en dernier', enabled='')).status_code, 302)
        before, logs = self.values(), LogEntry.objects.count()
        self.assertEqual(self.client.post(self.url('announcement_edit', pk=row.pk), stale).status_code, 409)
        self.assertEqual(self.values(), before)
        self.assertEqual(LogEntry.objects.count(), logs)
        self.assertIsNone(active_announcement(at=AT))

    def test_missing_revision_and_editing_missing_entries_never_create_or_overwrite(self):
        before = self.values()
        submissions = [(self.url('page_edit', key='about'), self.page_data()),
                       (self.url('faq_create'), self.faq_data()),
                       (self.url('announcement_create'), self.announcement_data())]
        for path, data in submissions:
            data.pop('revision')
            self.assertEqual(self.client.post(path, data).status_code, 200)
            self.assertEqual(self.values(), before)
        self.assertEqual(self.client.get(self.url('faq_edit', pk=999999)).status_code, 404)
        self.assertEqual(self.client.get(self.url('announcement_edit', pk=999999)).status_code, 404)
        self.assertEqual(self.values(), before)

    def test_services_enforce_management_permission_and_real_confirmation_too(self):
        from django.core.exceptions import PermissionDenied
        from .editorial_forms import AnnouncementForm, FAQForm, PageContentForm
        from .editorial_services import save_announcement, save_faq, save_page
        page = PageContentForm('about', self.page_data())
        faq = FAQForm(self.faq_data())
        banner = AnnouncementForm(self.announcement_data())
        for form in (page, faq, banner):
            self.assertTrue(form.is_valid(), form.errors)
        customer = get_user_model().objects.create_user('editorial-service-customer', email='service@example.test')
        verify_test_user(customer)
        before = self.values()
        calls = [(save_page, ('about', page.cleaned_data)),
                 (save_faq, (faq.cleaned_data,)), (save_announcement, (banner.cleaned_data,))]
        for save, args in calls:
            with self.subTest(service=save.__name__), self.assertRaises(PermissionDenied):
                save(*args, actor=customer)
        EmailVerification.objects.filter(user=self.manager).delete()
        with override_settings(LOCAL_SKIP_EMAIL_VERIFICATION=False):
            for save, args in calls:
                with self.subTest(unverified=save.__name__), self.assertRaises(PermissionDenied):
                    save(*args, actor=self.manager)
        self.assertEqual(self.values(), before)

    def test_services_revalidate_untrusted_payloads_instead_of_trusting_cleaned_data(self):
        from .editorial_forms import AnnouncementForm, FAQForm, PageContentForm
        from .editorial_services import EditorialError, save_announcement, save_faq, save_page
        forms = [PageContentForm('about', self.page_data()), FAQForm(self.faq_data()),
                 AnnouncementForm(self.announcement_data())]
        for form in forms:
            self.assertTrue(form.is_valid(), form.errors)
        page, faq, banner = [form.cleaned_data for form in forms]
        calls = [
            (save_page, ('about', {**page, 'body': 'x' * 6001})),
            (save_page, ('about', {**page, 'image': SimpleUploadedFile('fake.png', b'not an image', content_type='image/png')})),
            (save_page, ('about', {**page, 'store_address_line1': 'Injected'})),
            (save_page, ('about', {**page, 'revision': True})),
            (save_faq, ({**faq, 'target': 'javascript:alert(1)', 'link_label': 'Lien'},)),
            (save_faq, ({**faq, 'position': 0},)),
            (save_faq, ({**faq, 'enabled': 'on'},)),
            (save_announcement, ({**banner, 'target': 'https://evil.example/', 'link_label': 'Lien'},)),
            (save_announcement, ({**banner, 'starts_at': AT.replace(tzinfo=None)},)),
            (save_announcement, ({**banner, 'ends_at': banner['starts_at']},)),
            (save_announcement, ({**banner, 'priority': 21},)),
        ]
        before, logs = self.values(), LogEntry.objects.count()
        for save, args in calls:
            with self.subTest(service=save.__name__, fields=tuple(args[-1])):
                with self.assertRaises(EditorialError):
                    save(*args, actor=self.manager)
                self.assertEqual(self.values(), before)
                self.assertEqual(LogEntry.objects.count(), logs)
                self.assertFalse(list(self.media.rglob('*.webp')))

    def test_failed_photo_save_rolls_back_revision_and_removes_only_the_new_file(self):
        self.assertEqual(self.client.post(self.url('page_edit', key='about'), self.page_data(
            image=image('red'))).status_code, 302)
        row = ContentPage.objects.get(key='about')
        old_path = Path(row.image.path)
        before, logs = self.values(), LogEntry.objects.count()
        original = ContentPage.save

        def write_then_fail(instance, *args, **kwargs):
            original(instance, *args, **kwargs)
            raise RuntimeError('Échec simulé après l’enregistrement de la nouvelle photo')

        with patch.object(ContentPage, 'save', autospec=True, side_effect=write_then_fail):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url('page_edit', key='about'), self.page_data(
                    image=image('blue'), heading='Ce texte doit être annulé'))
        self.assertEqual(self.values(), before)
        self.assertEqual(LogEntry.objects.count(), logs)
        self.assertTrue(old_path.exists())
        self.assertEqual(list(self.media.rglob('*.webp')), [old_path])
