"""Seller organisation actions remain reversible, private, and safe across tabs."""
import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from connexion.models import EmailVerification
from connexion.test_helpers import verify_test_user
from .editorial_configuration import active_announcement, announcement_overlaps, public_faq
from .editorial_forms import AnnouncementForm, FAQForm
from .editorial_services import (EditorialConflict, EditorialError, archive_announcement,
    duplicate_announcement, faq_order_snapshot, move_faq, pause_announcement, reorder_faq, restore_announcement,
    save_announcement, save_faq, set_faq_visibility)
from .models import FAQEntry, SiteAnnouncement
from .test_editorial import AT, PARIS


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True,
                   EMAIL_DELIVERY_ENABLED=False, TIME_ZONE='Europe/Paris')
class EditorialOrganisationTests(TestCase):
    backend = 'connexion.backends.EmailOrUsernameBackend'

    def setUp(self):
        # Session expiry and all calendar decisions share this deterministic clock.
        clock = patch('django.utils.timezone.now', return_value=AT)
        clock.start()
        self.addCleanup(clock.stop)
        self.manager = get_user_model().objects.create_user(
            'editorial-organisation-manager', email='organisation@example.test', is_staff=True)
        self.permission = Permission.objects.get(content_type__app_label='core', codename='manage_catalog')
        self.manager.user_permissions.add(self.permission)
        verify_test_user(self.manager)
        self.client.force_login(self.manager, backend=self.backend)
        FAQEntry.objects.all().delete()

    def url(self, name, **kwargs):
        return reverse('editorial:' + name, kwargs=kwargs or None)

    def announcement(self, **changes):
        values = {'name': 'Annonce à organiser', 'kind': 'vacances', 'message': 'Une information pour les clients.',
            'starts_at': AT - timedelta(hours=1), 'ends_at': AT + timedelta(hours=1),
            'enabled': True, 'priority': 10, 'target': 'contact', 'link_label': 'Nous contacter'}
        values.update(changes)
        return SiteAnnouncement.objects.create(**values)

    def announcement_data(self, row, **changes):
        data = {name: getattr(row, name) for name in
                ('name', 'kind', 'message', 'priority', 'target', 'link_label', 'revision')}
        data.update({'starts_at': row.starts_at.astimezone(PARIS).strftime('%Y-%m-%dT%H:%M'),
                     'ends_at': row.ends_at.astimezone(PARIS).strftime('%Y-%m-%dT%H:%M'),
                     'enabled': 'on' if row.enabled else ''})
        data.update(changes)
        return data

    def question(self, label, **changes):
        values = {'question': 'Question ' + label, 'answer': 'Réponse ' + label,
                  'position': 1, 'enabled': True, 'target': '', 'link_label': ''}
        values.update(changes)
        return FAQEntry.objects.create(**values)

    def faq_data(self, row, **changes):
        values = {name: getattr(row, name) for name in
                  ('question', 'answer', 'position', 'target', 'link_label', 'revision')}
        values['enabled'] = 'on' if row.enabled else ''
        values.update(changes)
        return values

    def snapshot(self, actor=None):
        return faq_order_snapshot(list(FAQEntry.objects.order_by('position', 'pk')), actor or self.manager)

    def state(self):
        return tuple(list(model.objects.order_by('pk').values()) for model in (FAQEntry, SiteAnnouncement))

    def post_order(self, order, snapshot=None):
        return self.client.post(self.url('faq_reorder'), {
            'order': json.dumps(order), 'snapshot': snapshot if snapshot is not None else self.snapshot()})

    def test_duplicate_active_or_ended_announcement_is_always_a_private_copy(self):
        for ended in (False, True):
            with self.subTest(ended=ended):
                source = self.announcement(name='A' * 120,
                    starts_at=AT - timedelta(days=2) if ended else AT,
                    ends_at=AT - timedelta(days=1) if ended else AT + timedelta(hours=2))
                before = SiteAnnouncement.objects.filter(pk=source.pk).values().get()
                copied = duplicate_announcement(source.pk, source.revision, self.manager)
                self.assertNotEqual(copied.pk, source.pk)
                self.assertFalse(copied.enabled)
                self.assertIsNone(copied.archived_at)
                self.assertLessEqual(len(copied.name), 120)
                self.assertTrue(copied.name.startswith('Copie de '))
                for field in ('kind', 'message', 'starts_at', 'ends_at', 'priority', 'target', 'link_label'):
                    self.assertEqual(getattr(copied, field), getattr(source, field))
                self.assertEqual(copied.updated_by_id, self.manager.pk)
                self.assertEqual(copied.revision, 1)
                self.assertEqual(SiteAnnouncement.objects.filter(pk=source.pk).values().get(), before)
                self.assertTrue(LogEntry.objects.filter(user=self.manager, object_id=str(copied.pk)).exists())

    def test_archive_removes_current_banner_and_overlaps_and_restore_stays_disabled(self):
        fallback = self.announcement(name='Information normale', message='Message de remplacement', priority=0)
        source = self.announcement(name='Information prioritaire', message='Message à archiver', priority=20)
        self.assertEqual(active_announcement().pk, source.pk)
        old_revision = source.revision
        archived = archive_announcement(source.pk, old_revision, self.manager)
        self.assertIsNotNone(archived.archived_at)
        self.assertFalse(archived.enabled)
        self.assertGreater(archived.revision, old_revision)
        self.assertEqual(active_announcement().pk, fallback.pk)
        self.assertNotIn(archived.pk, announcement_overlaps(source.starts_at, source.ends_at).values_list('pk', flat=True))
        public = Client().get('/')
        self.assertNotContains(public, source.message)
        self.assertContains(public, fallback.message)
        restored = restore_announcement(archived.pk, archived.revision, self.manager)
        self.assertIsNone(restored.archived_at)
        self.assertFalse(restored.enabled)
        self.assertEqual(restored.message, source.message)
        self.assertEqual(active_announcement().pk, fallback.pk)

    def test_archive_filters_are_read_only_and_separate_current_items_from_archives(self):
        current = self.announcement(name='À conserver dans la liste')
        archived = archive_announcement(self.announcement(name='Archive à retrouver').pk, 1, self.manager)
        before, logs = self.state(), LogEntry.objects.count()
        listing = self.client.get(self.url('announcements'))
        archives = self.client.get(self.url('announcements'), {'view': 'archives'})
        self.assertEqual([row.pk for row in listing.context['announcements']], [current.pk])
        self.assertEqual([row.pk for row in archives.context['announcements']], [archived.pk])
        self.assertFalse(listing.context['showing_archives'])
        self.assertTrue(archives.context['showing_archives'])
        self.assertEqual(listing.context['counts']['current'], 1)
        self.assertEqual(listing.context['counts']['archives'], 1)
        self.assertEqual(self.state(), before)
        self.assertEqual(LogEntry.objects.count(), logs)

    def test_archive_can_be_duplicated_without_restoring_or_reactivating_the_source(self):
        archived = archive_announcement(self.announcement().pk, 1, self.manager)
        before = SiteAnnouncement.objects.filter(pk=archived.pk).values().get()
        response = self.client.post(self.url('announcement_duplicate', pk=archived.pk), {'revision': archived.revision})
        self.assertEqual(response.status_code, 302)
        copy = SiteAnnouncement.objects.exclude(pk=archived.pk).get()
        self.assertFalse(copy.enabled)
        self.assertIsNone(copy.archived_at)
        self.assertEqual(SiteAnnouncement.objects.filter(pk=archived.pk).values().get(), before)
        self.assertIsNone(active_announcement())

    def test_stale_duplicate_archive_and_restore_do_not_overwrite_newer_state(self):
        row = self.announcement()
        stale = row.revision
        row = archive_announcement(row.pk, row.revision, self.manager)
        before, logs = self.state(), LogEntry.objects.count()
        for name in ('announcement_duplicate', 'announcement_archive', 'announcement_restore', 'announcement_pause'):
            with self.subTest(name=name):
                response = self.client.post(self.url(name, pk=row.pk), {'revision': stale})
                self.assertEqual(response.status_code, 409)
                self.assertEqual(self.state(), before)
        self.assertEqual(LogEntry.objects.count(), logs)

    def test_stale_edit_cannot_reactivate_an_archived_announcement(self):
        row = self.announcement()
        old_form = self.announcement_data(row, message='Ancien formulaire dangereux', enabled='on')
        archived = archive_announcement(row.pk, row.revision, self.manager)
        before = self.state()
        response = self.client.post(self.url('announcement_edit', pk=row.pk), old_form)
        self.assertNotEqual(response.status_code, 302)
        self.assertEqual(self.state(), before)
        form = AnnouncementForm(self.announcement_data(archived, enabled='on'))
        self.assertTrue(form.is_valid(), form.errors)
        with self.assertRaises(EditorialError):
            save_announcement(form.cleaned_data, self.manager, pk=archived.pk)
        self.assertEqual(self.state(), before)
        self.assertIsNone(active_announcement())

    def test_archive_and_duplicate_roll_back_when_audit_recording_fails(self):
        row = self.announcement()
        before = self.state()
        for action in (archive_announcement, duplicate_announcement, pause_announcement):
            with self.subTest(action=action.__name__), patch.object(
                    LogEntry.objects, 'log_actions', side_effect=RuntimeError('Échec simulé')):
                with self.assertRaises(RuntimeError):
                    action(row.pk, row.revision, self.manager)
                self.assertEqual(self.state(), before)

    def test_pause_active_or_scheduled_announcement_preserves_dates_and_content(self):
        for scheduled in (False, True):
            with self.subTest(scheduled=scheduled):
                row = self.announcement(name='Annonce à mettre en pause',
                    starts_at=AT + timedelta(days=1) if scheduled else AT,
                    ends_at=AT + timedelta(days=2) if scheduled else AT + timedelta(hours=1))
                before = SiteAnnouncement.objects.filter(pk=row.pk).values().get()
                response = self.client.post(self.url('announcement_pause', pk=row.pk), {'revision': row.revision})
                self.assertEqual(response.status_code, 302)
                row.refresh_from_db()
                self.assertFalse(row.enabled)
                self.assertIsNone(row.archived_at)
                self.assertGreater(row.revision, before['revision'])
                for field in ('name', 'kind', 'message', 'starts_at', 'ends_at', 'priority', 'target', 'link_label'):
                    self.assertEqual(getattr(row, field), before[field])
                self.assertNotIn(row.pk, announcement_overlaps(row.starts_at, row.ends_at).values_list('pk', flat=True))

    def test_pausing_an_inactive_announcement_is_a_noop_and_an_archive_cannot_be_paused(self):
        row = self.announcement(enabled=False)
        before, logs = self.state(), LogEntry.objects.count()
        pause_announcement(row.pk, row.revision, self.manager)
        self.assertEqual(self.state(), before)
        self.assertEqual(LogEntry.objects.count(), logs)
        row = archive_announcement(row.pk, row.revision, self.manager)
        before = self.state()
        with self.assertRaises(EditorialError):
            pause_announcement(row.pk, row.revision, self.manager)
        self.assertEqual(self.state(), before)

    def test_calendar_filters_use_exact_boundaries_and_keep_archives_separate(self):
        active = self.announcement(name='Commence maintenant', starts_at=AT)
        future = self.announcement(name='À venir', starts_at=AT + timedelta(hours=1), ends_at=AT + timedelta(hours=2))
        draft = self.announcement(name='Brouillon', enabled=False)
        ended = self.announcement(name='Termine maintenant', ends_at=AT)
        archived = archive_announcement(self.announcement(name='Archivée').pk, 1, self.manager)
        before = self.state()
        for state, expected in (('active', active), ('scheduled', future), ('draft', draft), ('ended', ended)):
            with self.subTest(state=state):
                response = self.client.get(self.url('announcements'), {'state': state})
                self.assertEqual([row.pk for row in response.context['announcements']], [expected.pk])
                self.assertEqual(response.context['selected_state'], state)
                self.assertEqual(response.context['counts'][state], 1)
                self.assertEqual(response.context['counts']['current'], 4)
                self.assertEqual(response.context['counts']['archives'], 1)
        all_rows = self.client.get(self.url('announcements'), {'state': 'inconnue'})
        self.assertEqual({row.pk for row in all_rows.context['announcements']}, {active.pk, future.pk, draft.pk, ended.pk})
        archive_list = self.client.get(self.url('announcements'), {'view': 'archives', 'state': 'active'})
        self.assertEqual([row.pk for row in archive_list.context['announcements']], [archived.pk])
        self.assertEqual(self.state(), before)

    def test_filtering_active_announcements_keeps_warnings_about_future_overlaps(self):
        active = self.announcement(name='Active', starts_at=AT, ends_at=AT + timedelta(hours=2))
        self.announcement(name='Période suivante', starts_at=AT + timedelta(hours=1), ends_at=AT + timedelta(hours=3))
        response = self.client.get(self.url('announcements'), {'state': 'active'})
        self.assertEqual([row.pk for row in response.context['announcements']], [active.pk])
        self.assertEqual(response.context['announcement_rows'][0]['overlap_count'], 1)

    def test_reorder_includes_hidden_questions_resolves_ties_and_preserves_content(self):
        first = self.question('A', position=10)
        hidden = self.question('B', position=10, enabled=False)
        last = self.question('C', position=30)
        snapshot, logs = self.snapshot(), LogEntry.objects.count()
        expected = [last.pk, hidden.pk, first.pk]
        response = self.post_order(expected, snapshot)
        self.assertEqual(response.status_code, 302)
        entries = list(FAQEntry.objects.order_by('position', 'pk'))
        self.assertEqual([entry.pk for entry in entries], expected)
        self.assertEqual([entry.position for entry in entries], [1, 2, 3])
        self.assertTrue(all(entry.updated_by_id == self.manager.pk for entry in entries))
        hidden.refresh_from_db()
        self.assertFalse(hidden.enabled)
        self.assertEqual(hidden.answer, 'Réponse B')
        self.assertEqual([entry.pk for entry in public_faq()], [last.pk, first.pk])
        public = Client().get(self.url('faq'))
        self.assertNotContains(public, hidden.question)
        self.assertLess(public.content.index(last.question.encode()), public.content.index(first.question.encode()))
        self.assertGreater(LogEntry.objects.count(), logs)

    def test_reorder_changes_revisions_only_for_rows_whose_position_changed(self):
        first = self.question('A', position=1)
        second = self.question('B', position=2)
        third = self.question('C', position=3)
        reorder_faq([second.pk, first.pk, third.pk], self.snapshot(), self.manager)
        first.refresh_from_db(); second.refresh_from_db(); third.refresh_from_db()
        self.assertGreater(first.revision, 1)
        self.assertGreater(second.revision, 1)
        self.assertEqual(third.revision, 1)
        before = self.state()
        reorder_faq([second.pk, first.pk, third.pk], self.snapshot(), self.manager)
        self.assertEqual(self.state(), before)

    def test_reorder_rejects_nonpermutations_without_changing_positions_or_audit(self):
        first, second, third = [self.question(letter, position=index) for index, letter in enumerate('ABC', 1)]
        token, before, logs = self.snapshot(), self.state(), LogEntry.objects.count()
        orders = [[first.pk, second.pk], [first.pk, first.pk, third.pk],
                  [first.pk, second.pk, 99999999], [str(first.pk), second.pk, third.pk],
                  [True, second.pk, third.pk], [], {'first': first.pk}]
        for order in orders:
            with self.subTest(order=order), self.assertRaises(EditorialError):
                reorder_faq(order, token, self.manager)
            self.assertEqual(self.state(), before)
        self.assertEqual(LogEntry.objects.count(), logs)

    def test_reorder_rejects_invalid_or_another_sellers_signed_snapshot(self):
        first, second = self.question('A', position=1), self.question('B', position=2)
        token, before = self.snapshot(), self.state()
        other = get_user_model().objects.create_user('other-editorial-manager', email='other@example.test', is_staff=True)
        other.user_permissions.add(self.permission)
        verify_test_user(other)
        for snapshot, actor in ((token + 'broken', self.manager), ('', self.manager), (token, other)):
            with self.subTest(actor=actor.username), self.assertRaises(EditorialError):
                reorder_faq([second.pk, first.pk], snapshot, actor)
            self.assertEqual(self.state(), before)

    def test_reorder_snapshot_becomes_stale_after_an_edit_a_creation_or_a_removal(self):
        first, second = self.question('A', position=1), self.question('B', position=2)
        token = self.snapshot()
        form = FAQForm(self.faq_data(first, answer='Réponse plus récente'))
        self.assertTrue(form.is_valid(), form.errors)
        save_faq(form.cleaned_data, self.manager, pk=first.pk)
        before = self.state()
        self.assertEqual(self.post_order([second.pk, first.pk], token).status_code, 409)
        self.assertEqual(self.state(), before)
        token = self.snapshot()
        third = self.question('C', position=3)
        before = self.state()
        self.assertEqual(self.post_order([second.pk, first.pk], token).status_code, 409)
        self.assertEqual(self.state(), before)
        token = self.snapshot()
        third_pk = third.pk
        third.delete()
        before = self.state()
        self.assertEqual(self.post_order([second.pk, first.pk, third_pk], token).status_code, 409)
        self.assertEqual(self.state(), before)

    def test_reorder_rolls_back_all_positions_and_revisions_on_audit_failure(self):
        first, second, third = [self.question(letter, position=index) for index, letter in enumerate('ABC', 1)]
        token, before, logs = self.snapshot(), self.state(), LogEntry.objects.count()
        with patch.object(LogEntry.objects, 'log_actions', side_effect=RuntimeError('Échec simulé')):
            with self.assertRaises(RuntimeError):
                reorder_faq([third.pk, second.pk, first.pk], token, self.manager)
        self.assertEqual(self.state(), before)
        self.assertEqual(LogEntry.objects.count(), logs)

    def test_list_visibility_action_changes_publication_without_reordering_or_losing_answer(self):
        row = self.question('À conserver', position=15)
        response = self.client.post(self.url('faq_toggle', pk=row.pk), {'revision': row.revision, 'enabled': '0'})
        self.assertEqual(response.status_code, 302)
        row.refresh_from_db()
        self.assertFalse(row.enabled)
        self.assertEqual(row.position, 15)
        self.assertEqual(row.answer, 'Réponse À conserver')
        self.assertNotContains(Client().get(self.url('faq')), row.question)
        before = self.state()
        self.assertEqual(self.client.post(self.url('faq_toggle', pk=row.pk), {
            'revision': 1, 'enabled': '1'}).status_code, 409)
        self.assertEqual(self.state(), before)
        set_faq_visibility(row.pk, row.revision, True, self.manager)
        self.assertContains(Client().get(self.url('faq')), row.question)

    def test_visibility_requires_explicit_boolean_and_does_not_create_revisions_for_no_change(self):
        row = self.question('A')
        before = self.state()
        for enabled in ('yes', 'on', 'true', '', '2'):
            with self.subTest(enabled=enabled):
                response = self.client.post(self.url('faq_toggle', pk=row.pk), {
                    'revision': row.revision, 'enabled': enabled})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.state(), before)
        with self.assertRaises(EditorialError):
            set_faq_visibility(row.pk, row.revision, '1', self.manager)
        set_faq_visibility(row.pk, row.revision, True, self.manager)
        self.assertEqual(self.state(), before)

    def test_visibility_change_invalidates_an_open_reorder_snapshot(self):
        first, second = self.question('A', position=1), self.question('B', position=2)
        token = self.snapshot()
        set_faq_visibility(first.pk, first.revision, False, self.manager)
        before = self.state()
        with self.assertRaises(EditorialConflict):
            reorder_faq([second.pk, first.pk], token, self.manager)
        self.assertEqual(self.state(), before)

    def test_direct_list_moves_reorder_public_answers_and_keep_hidden_answers_hidden(self):
        first = self.question('A', position=1)
        hidden = self.question('B', position=2, enabled=False)
        third = self.question('C', position=3)
        for expected in ([hidden.pk, first.pk, third.pk], [hidden.pk, third.pk, first.pk]):
            listing = self.client.get(self.url('faq_list'))
            response = self.client.post(self.url('faq_move'), {
                'move': f'{first.pk}:down', 'snapshot': listing.context['move_snapshot']})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.url, self.url('faq_list') + f'#faq-{first.pk}')
            self.assertEqual(list(FAQEntry.objects.order_by('position', 'pk').values_list('pk', flat=True)), expected)
        hidden.refresh_from_db()
        self.assertFalse(hidden.enabled)
        public = Client().get(self.url('faq'))
        self.assertNotContains(public, hidden.question)
        self.assertLess(public.content.index(third.question.encode()), public.content.index(first.question.encode()))

    def test_direct_list_move_rejects_stale_snapshot_without_undoing_visibility_change(self):
        first, second = self.question('A', position=1), self.question('B', position=2)
        token = self.snapshot()
        set_faq_visibility(second.pk, second.revision, False, self.manager)
        before = self.state()
        response = self.client.post(self.url('faq_move'), {'move': f'{first.pk}:down', 'snapshot': token})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.state(), before)

    def test_direct_list_move_boundaries_and_malformed_commands_never_modify_order(self):
        first, second = self.question('A', position=1), self.question('B', position=2)
        before, token = self.state(), self.snapshot()
        for move in (f'{first.pk}:up', f'{second.pk}:down', f'{first.pk}:sideways', 'no-id:up', '-3:down', f'{first.pk}:down:extra'):
            with self.subTest(move=move):
                response = self.client.post(self.url('faq_move'), {'move': move, 'snapshot': token})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.state(), before)

    def test_new_actions_require_permissions_csrf_and_post(self):
        row = self.announcement()
        faq = self.question('A')
        actions = [(self.url(name, pk=row.pk), {'revision': row.revision}) for name in
                   ('announcement_duplicate', 'announcement_archive', 'announcement_restore', 'announcement_pause')]
        actions.extend([(self.url('faq_toggle', pk=faq.pk), {'revision': faq.revision, 'enabled': '0'}),
                        (self.url('faq_reorder'), {'order': json.dumps([faq.pk]), 'snapshot': self.snapshot()}),
                        (self.url('faq_move'), {'move': f'{faq.pk}:down', 'snapshot': self.snapshot()})])
        user = get_user_model().objects.create_user('organisation-staff-without-role', email='norole@example.test', is_staff=True)
        verify_test_user(user)
        restricted = Client()
        restricted.force_login(user, backend=self.backend)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.manager, backend=self.backend)
        before = self.state()
        for path, data in actions:
            with self.subTest(path=path):
                self.assertEqual(Client().post(path, data).status_code, 302)
                self.assertEqual(restricted.post(path, data).status_code, 403)
                self.assertEqual(csrf.post(path, data).status_code, 403)
                if path != self.url('faq_reorder'):
                    self.assertEqual(self.client.get(path).status_code, 405)
        self.assertEqual(restricted.get(self.url('faq_reorder')).status_code, 403)
        response = self.client.get(self.url('faq_reorder'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertIn('noindex', response.headers['X-Robots-Tag'])
        self.assertEqual(self.state(), before)

    def test_raw_services_require_management_permission_and_verified_email(self):
        row = self.announcement()
        faq = self.question('A')
        token = self.snapshot()
        customer = get_user_model().objects.create_user('organisation-customer', email='customer@example.test')
        verify_test_user(customer)
        calls = [(duplicate_announcement, (row.pk, row.revision)),
                 (archive_announcement, (row.pk, row.revision)),
                 (restore_announcement, (row.pk, row.revision)),
                 (pause_announcement, (row.pk, row.revision)),
                 (reorder_faq, ([faq.pk], token)),
                 (move_faq, (faq.pk, 'down', token)),
                 (set_faq_visibility, (faq.pk, faq.revision, False))]
        before = self.state()
        for service, args in calls:
            with self.subTest(service=service.__name__), self.assertRaises(PermissionDenied):
                service(*args, actor=customer)
        EmailVerification.objects.filter(user=self.manager).delete()
        with override_settings(LOCAL_SKIP_EMAIL_VERIFICATION=False):
            for service, args in calls:
                with self.subTest(unverified=service.__name__), self.assertRaises(PermissionDenied):
                    service(*args, actor=self.manager)
        self.assertEqual(self.state(), before)
