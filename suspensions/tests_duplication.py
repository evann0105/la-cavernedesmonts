from datetime import timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from connexion.test_helpers import verify_test_user
from . import services
from .models import PauseConfiguration, PauseEvent, PurchasePause


PARIS = ZoneInfo('Europe/Paris')


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'], EMAIL_DELIVERY_ENABLED=False)
class PauseDuplicationTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user('gestion-duplication-pause', is_staff=True)
        self.actor.user_permissions.add(Permission.objects.get(codename='manage_catalog'))
        verify_test_user(self.actor)
        self.client.force_login(self.actor)
        self.now = timezone.now()
        self.past = PurchasePause.objects.create(name='Vacances de l’an dernier',
            message='Retrouvez la collection pendant notre absence.',
            starts_at=self.now-timedelta(days=20), ends_at=self.now-timedelta(days=10), revision=8)
        self.cancelled = PurchasePause.objects.create(name='Ancienne fermeture annulée',
            message='La boutique prépare son retour.', starts_at=self.now-timedelta(days=8),
            ends_at=self.now-timedelta(days=3), stopped_at=self.now-timedelta(days=9), stop_kind='cancel')
        self.active = PurchasePause.objects.create(name='Pause actuelle', message='À très bientôt.',
            starts_at=self.now-timedelta(days=1), ends_at=self.now+timedelta(days=1))
        self.future = PurchasePause.objects.create(name='Prochaine pause', message='Rendez-vous à notre retour.',
            starts_at=self.now+timedelta(days=3), ends_at=self.now+timedelta(days=5))
        for pause in (self.past, self.cancelled, self.active, self.future):
            PauseEvent.objects.create(pause=pause, kind='create', actor=self.actor,
                actor_label=self.actor.username, before={}, after=services._snapshot(pause))

    def url(self, source=None):
        return reverse('suspensions:duplicate', args=[(source or self.past).pk])

    def data(self, **changes):
        return {'name': self.past.name, 'message': self.past.message, 'mode': 'scheduled',
            'starts_at': (self.now+timedelta(days=10)).astimezone(PARIS).strftime('%Y-%m-%dT%H:%M'),
            'ends_at': (self.now+timedelta(days=12)).astimezone(PARIS).strftime('%Y-%m-%dT%H:%M'),
            'revision': str(services.configuration_revision()), 'confirmed': 'on', **changes}

    def original_rows(self):
        return (list(PurchasePause.objects.order_by('pk').values()), list(PauseEvent.objects.order_by('pk').values()))

    def test_get_copies_only_texts_for_every_source_state_without_any_write(self):
        before = self.original_rows()
        with patch('suspensions.views.services.save_pause') as save:
            for source in (self.past, self.cancelled, self.active, self.future):
                with self.subTest(source=source.status):
                    response = self.client.get(self.url(source), {'mode': 'now', 'starts_at': self.now.isoformat()})
                    self.assertEqual(response.status_code, 200)
                    form = response.context['form']
                    self.assertIsNone(form.pause)
                    self.assertFalse(form.already_started)
                    self.assertEqual(form['name'].value(), source.name)
                    self.assertEqual(form['message'].value(), source.message)
                    self.assertEqual(form['mode'].value(), 'scheduled')
                    self.assertIsNone(form['starts_at'].value())
                    self.assertIsNone(form['ends_at'].value())
                    self.assertFalse(form['confirmed'].value())
                    self.assertEqual(form['revision'].value(), 0)
                    self.assertFalse(form.fields['starts_at'].disabled)
                    self.assertContains(response, 'Les dates ne sont pas reprises')
                    self.assertContains(response, 'Abandonner la duplication')
                    self.assertIn('no-store', response['Cache-Control'])
                    self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')
            save.assert_not_called()
        self.assertEqual(self.original_rows(), before)
        self.assertFalse(PauseConfiguration.objects.exists())

    def test_all_list_tabs_and_finished_detail_offer_duplication(self):
        for state, source in [('past', self.past), ('cancelled', self.cancelled),
                              ('active', self.active), ('upcoming', self.future)]:
            response = self.client.get(reverse('suspensions:overview'), {'state': state})
            self.assertContains(response, self.url(source))
            self.assertContains(response, 'Dupliquer')
        for source in (self.past, self.cancelled):
            response = self.client.get(reverse('suspensions:edit', args=[source.pk]))
            self.assertContains(response, self.url(source))
            self.assertContains(response, 'Dupliquer cette pause')

    def test_missing_dates_past_dates_and_confirmation_cannot_reactivate_the_source(self):
        before = self.original_rows()
        for changes, fields in [({'starts_at': '', 'ends_at': ''}, ['starts_at', 'ends_at']),
                ({'starts_at': self.past.starts_at.astimezone(PARIS).strftime('%Y-%m-%dT%H:%M'),
                  'ends_at': self.past.ends_at.astimezone(PARIS).strftime('%Y-%m-%dT%H:%M')}, ['starts_at', 'ends_at']),
                ({'confirmed': ''}, ['confirmed']),
                ({'mode': 'now', 'starts_at': '', 'ends_at': ''}, ['ends_at'])]:
            with self.subTest(changes=changes):
                response = self.client.post(self.url(), self.data(**changes))
                self.assertEqual(response.status_code, 400)
                for field in fields:
                    self.assertIn(field, response.context['form'].errors)
                self.assertEqual(response.context['source'].pk, self.past.pk)
        self.assertEqual(self.original_rows(), before)
        self.assertFalse(PauseConfiguration.objects.exists())

    def test_valid_duplicate_creates_separate_period_and_history_without_touching_source(self):
        source_before = PurchasePause.objects.filter(pk=self.cancelled.pk).values().get()
        history_before = list(self.cancelled.events.values())
        response = self.client.post(self.url(self.cancelled), self.data(
            name='Vacances suivantes', message='Le magasin vous retrouve bientôt.',
            pk=self.cancelled.pk, pause=self.cancelled.pk, source=self.future.pk,
            stopped_at=self.cancelled.stopped_at.isoformat(), stop_kind='cancel'))
        self.assertEqual(response.status_code, 302)
        created = PurchasePause.objects.get(name='Vacances suivantes')
        self.assertNotEqual(created.pk, self.cancelled.pk)
        self.assertEqual(response.url, reverse('suspensions:edit', args=[created.pk]))
        self.assertEqual(created.status, 'upcoming')
        self.assertIsNone(created.stopped_at)
        self.assertEqual(created.stop_kind, '')
        self.assertEqual(created.revision, 1)
        self.assertEqual(created.message, 'Le magasin vous retrouve bientôt.')
        self.assertEqual(created.events.count(), 1)
        event = created.events.get()
        self.assertEqual(event.kind, 'create')
        self.assertEqual(event.before, {})
        self.assertEqual(event.after['name'], 'Vacances suivantes')
        self.assertEqual(event.actor, self.actor)
        self.assertEqual(PurchasePause.objects.count(), 5)
        self.assertEqual(PurchasePause.objects.filter(pk=self.cancelled.pk).values().get(), source_before)
        self.assertEqual(list(self.cancelled.events.values()), history_before)

    def test_active_source_does_not_force_its_start_or_immediate_mode(self):
        data = self.data(name='Une autre période', message=self.active.message)
        response = self.client.post(self.url(self.active), data)
        self.assertEqual(response.status_code, 302)
        created = PurchasePause.objects.get(name='Une autre période')
        self.assertGreater(created.starts_at, self.now)
        self.assertEqual(created.starts_at.astimezone(PARIS).strftime('%Y-%m-%dT%H:%M'), data['starts_at'])
        self.active.refresh_from_db()
        self.assertEqual(self.active.status, 'active')

    def test_new_period_still_checks_overlapping_pauses(self):
        before = self.original_rows()
        data = self.data(starts_at=(self.future.starts_at+timedelta(hours=1)).astimezone(PARIS).strftime('%Y-%m-%dT%H:%M'),
                         ends_at=(self.future.ends_at+timedelta(hours=1)).astimezone(PARIS).strftime('%Y-%m-%dT%H:%M'))
        response = self.client.post(self.url(), data)
        self.assertContains(response, 'Ces dates chevauchent une autre pause.', status_code=400)
        self.assertEqual(response.context['form']['message'].value(), data['message'])
        self.assertEqual(self.original_rows(), before)

    def test_stale_revision_requires_fresh_confirmation_and_replay_never_duplicates_twice(self):
        data = self.data()
        PauseConfiguration.objects.create(revision=1)
        response = self.client.post(self.url(), data)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(PurchasePause.objects.count(), 4)
        form = response.context['form']
        self.assertEqual(form['starts_at'].value(), data['starts_at'])
        self.assertEqual(form['ends_at'].value(), data['ends_at'])
        self.assertEqual(form['message'].value(), data['message'])
        self.assertEqual(str(form['revision'].value()), '1')
        self.assertFalse(form['confirmed'].value())
        self.assertEqual(response.context['source'].pk, self.past.pk)
        data['revision'] = '1'
        self.assertEqual(self.client.post(self.url(), data).status_code, 302)
        self.assertEqual(self.client.post(self.url(), data).status_code, 409)
        self.assertEqual(PurchasePause.objects.count(), 5)
        self.assertEqual(PauseEvent.objects.filter(kind='create').count(), 5)
        self.past.refresh_from_db()
        self.assertEqual(self.past.revision, 8)
        self.assertEqual(self.past.events.count(), 1)

    def test_missing_source_is_404_even_when_post_supplies_a_valid_source(self):
        for pk in (0, 999999999, 10**30):
            url = reverse('suspensions:duplicate', args=[pk])
            for response in (self.client.get(url), self.client.post(url, self.data(source=self.past.pk))):
                self.assertEqual(response.status_code, 404)
        self.assertEqual(PurchasePause.objects.count(), 4)
        self.assertEqual(PauseEvent.objects.count(), 4)

    def test_catalog_permissions_csrf_and_post_only_mutations_are_preserved(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url()).status_code, 302)
        self.assertEqual(self.client.post(self.url(), self.data()).status_code, 302)
        no_access = get_user_model().objects.create_user('staff-sans-duplication', is_staff=True)
        verify_test_user(no_access)
        self.client.force_login(no_access)
        self.assertEqual(self.client.get(self.url()).status_code, 403)
        self.assertEqual(self.client.post(self.url(), self.data()).status_code, 403)
        self.client.force_login(self.actor)
        self.assertEqual(self.client.delete(self.url()).status_code, 405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.actor)
        self.assertEqual(csrf_client.post(self.url(), self.data()).status_code, 403)
        self.assertEqual(PurchasePause.objects.count(), 4)
        self.assertEqual(PauseEvent.objects.count(), 4)

    def test_source_and_edited_message_are_html_escaped(self):
        self.past.name = '<img src=x onerror=alert(1)>'
        self.past.message = '<script>alert(1)</script>'
        self.past.save(update_fields=['name', 'message'])
        response = self.client.get(self.url())
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertNotContains(response, '<img src=x onerror=alert(1)>')
