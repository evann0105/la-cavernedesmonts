from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from connexion.test_helpers import verify_test_user
from .models import PauseEvent, PurchasePause


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'], EMAIL_DELIVERY_ENABLED=False)
class PauseHistoryViewsTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user('historique-pauses', is_staff=True)
        self.actor.user_permissions.add(Permission.objects.get(codename='manage_catalog'))
        verify_test_user(self.actor)
        self.client.force_login(self.actor)
        now = timezone.now()
        self.pause = PurchasePause.objects.create(name='Vacances conservées', message='Message actuel',
            starts_at=now-timedelta(days=5), ends_at=now-timedelta(days=1))
        self.url = reverse('suspensions:edit', args=[self.pause.pk])
        self.snapshot = {'name': self.pause.name, 'message': 'Message précédent',
                         'starts_at': self.pause.starts_at.isoformat(), 'ends_at': self.pause.ends_at.isoformat()}

    def event(self, **changes):
        return PauseEvent.objects.create(pause=self.pause, kind='edit', actor=self.actor,
            actor_label=self.actor.username, before=self.snapshot, after={**self.snapshot, **changes})

    def test_detail_really_shows_escaped_before_and_after_without_changing_the_pause(self):
        self.event(message='<strong>Message nouveau</strong>')
        response = self.client.get(self.url)
        self.assertContains(response, 'Message précédent')
        self.assertContains(response, '&lt;strong&gt;Message nouveau&lt;/strong&gt;')
        self.assertContains(response, 'suspensions/history.css')
        self.assertEqual(response.context['pause_history_page'].paginator.count, 1)
        self.pause.refresh_from_db()
        self.assertEqual(self.pause.message, 'Message actuel')
        self.assertEqual(PauseEvent.objects.count(), 1)

    def test_every_event_remains_reachable_beyond_the_initial_twelve(self):
        for index in range(26):
            self.event(message=f'Événement {index:02d}')
        pages = [self.client.get(self.url, {'history_page': page}) for page in (1, 2, 3)]
        self.assertEqual([len(response.context['pause_history']) for response in pages], [12, 12, 2])
        ids = [row['id'] for response in pages for row in response.context['pause_history']]
        self.assertEqual(len(set(ids)), 26)
        self.assertContains(pages[0], '?history_page=2#pause-history')
        self.assertContains(pages[-1], 'Événement 00')
        self.assertEqual(PauseEvent.objects.count(), 26)

    def test_missing_legacy_snapshots_do_not_prevent_private_detail_or_pagination(self):
        self.event(message='Message nouveau')
        PauseEvent.objects.create(pause=self.pause, kind='edit', actor_label='', before=[], after={})
        response = self.client.get(self.url, {'history_page': 'invalide'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Certaines valeurs de cet ancien événement')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')

    def test_new_and_duplicate_forms_do_not_leak_the_source_history(self):
        self.event(message='Texte réservé à l’historique de l’origine')
        for url in (reverse('suspensions:create'), reverse('suspensions:duplicate', args=[self.pause.pk])):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, 'Texte réservé à l’historique de l’origine')
            self.assertNotContains(response, 'id="pause-history"')
