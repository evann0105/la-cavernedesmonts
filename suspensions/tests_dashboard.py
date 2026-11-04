from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from connexion.test_helpers import verify_test_user
from .dashboard import dashboard_state
from .models import PauseConfiguration, PauseEvent, PurchasePause

NOW = datetime(2027, 7, 15, 8, 0, tzinfo=dt_timezone.utc)


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'], EMAIL_DELIVERY_ENABLED=False)
class PauseDashboardTests(TestCase):
    def setUp(self):
        self.catalog = get_user_model().objects.create_user('pause-dashboard-catalog', is_staff=True)
        self.catalog.user_permissions.add(Permission.objects.get(content_type__app_label='core', codename='manage_catalog'))
        self.service = get_user_model().objects.create_user('pause-dashboard-service', is_staff=True)
        self.service.user_permissions.add(Permission.objects.get(content_type__app_label='retours', codename='manage_returns'))
        for user in (self.catalog, self.service):
            verify_test_user(user)
        self.client.force_login(self.catalog)
        self.url = reverse('suspensions:dashboard_status')

    def pause(self, start=-1, end=1, **kwargs):
        return PurchasePause.objects.create(name='Nom interne', message='La boutique prend une pause.',
            starts_at=NOW+timedelta(days=start), ends_at=NOW+timedelta(days=end), **kwargs)

    def test_empty_state_does_not_create_configuration_or_events(self):
        state = dashboard_state(at=NOW)
        self.assertEqual(state['state'], 'open')
        self.assertEqual(state['title'], 'Commandes ouvertes')
        self.assertIsNone(state['next_change_at'])
        self.assertFalse(PauseConfiguration.objects.exists())
        self.assertFalse(PauseEvent.objects.exists())

    def test_upcoming_period_and_contiguous_reopening_are_displayed(self):
        first = self.pause(start=1, end=2)
        self.pause(start=2, end=4)
        self.pause(start=0.5, end=0.75, stopped_at=NOW, stop_kind='cancel')
        data = dashboard_state(at=NOW)
        self.assertEqual(data['state'], 'scheduled')
        self.assertEqual(data['next_change_at'], first.starts_at.isoformat())
        self.assertIn('16/07/2027 à 10 h 00', data['schedule'])
        self.assertIn('19/07/2027 à 10 h 00', data['schedule'])
        self.assertEqual(data['message'], '')

    def test_active_period_uses_final_reopening_not_first_boundary(self):
        first = self.pause()
        self.pause(start=1, end=3)
        data = dashboard_state(at=NOW)
        self.assertEqual(data['state'], 'paused')
        self.assertEqual(data['next_change_at'], first.ends_at.isoformat())
        self.assertIn('18/07/2027 à 10 h 00', data['schedule'])
        self.assertEqual(data['message'], first.message)

    def test_past_cancelled_and_early_resumed_pauses_do_not_claim_closed(self):
        self.pause(start=-4, end=-2)
        self.pause(start=2, end=3, stopped_at=NOW, stop_kind='cancel')
        self.pause(stopped_at=NOW-timedelta(minutes=1), stop_kind='resume')
        self.assertEqual(dashboard_state(at=NOW)['state'], 'open')

    def test_display_uses_paris_even_when_request_timezone_is_elsewhere(self):
        self.pause()
        with timezone.override(ZoneInfo('America/New_York')):
            data = dashboard_state(at=NOW)
        self.assertIn('10 h 00 (heure de Paris)', data['schedule'])

    def test_endpoint_is_private_and_get_only_without_writes(self):
        self.pause()
        with patch('suspensions.dashboard.dashboard_state', side_effect=lambda: dashboard_state(at=NOW)), CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['schema'], 1)
        self.assertEqual(response.json()['state'], 'paused')
        self.assertIn('private', response['Cache-Control'])
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')
        self.assertFalse(any(query['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for query in queries))
        self.assertNotIn('Nom interne', response.content.decode())
        self.assertEqual(self.client.post(self.url).status_code, 405)

    def test_endpoint_rejects_anonymous_customer_and_unprivileged_staff(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        for staff in (False, True):
            actor = get_user_model().objects.create_user('pause-no-access-'+str(staff), is_staff=staff)
            verify_test_user(actor)
            self.client.force_login(actor)
            self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_email_access_gate_is_maintained(self):
        with patch('tableau.access.email_access_allowed', return_value=False):
            response = self.client.get(self.url)
        self.assertRedirects(response, reverse('connexion:verify_email'), fetch_redirect_response=False)

    def test_service_seller_can_read_but_is_not_offered_forbidden_settings(self):
        self.client.force_login(self.service)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        response = self.client.get(reverse('tableau:home'))
        self.assertContains(response, 'data-pause-dashboard')
        self.assertNotContains(response, 'Gérer les pauses')

    def test_home_indicator_is_before_tasks_and_has_direct_settings_for_catalog(self):
        response = self.client.get(reverse('tableau:home'))
        html = response.content.decode()
        self.assertLess(html.index('data-pause-dashboard'), html.index('id="today-title"'))
        self.assertContains(response, 'Gérer les pauses')
        self.assertContains(response, 'Les réglages de paiement et de livraison restent applicables.')
        self.assertContains(response, 'suspensions/dashboard.js')
        self.assertContains(response, 'suspensions/dashboard.css')

    def test_refresh_observes_start_end_and_resumption_without_mutation(self):
        self.pause(start=1, end=2)
        counts = (PurchasePause.objects.count(), PauseEvent.objects.count(), PauseConfiguration.objects.count())
        for at, expected in [(NOW, 'scheduled'), (NOW+timedelta(days=1), 'paused'), (NOW+timedelta(days=2), 'open')]:
            with patch('suspensions.dashboard.dashboard_state', side_effect=lambda: dashboard_state(at=at)):
                response = self.client.get(self.url)
            self.assertEqual(response.json()['state'], expected)
        self.assertEqual(counts, (PurchasePause.objects.count(), PauseEvent.objects.count(), PauseConfiguration.objects.count()))

    def test_customer_message_is_escaped_and_does_not_leak_private_names(self):
        pause = self.pause()
        pause.message = '<img src=x onerror=alert(1)>'
        pause.save(update_fields=['message'])
        with patch('tableau.home.purchase_pause_state', side_effect=lambda: dashboard_state(at=NOW)):
            response = self.client.get(reverse('tableau:home'))
        self.assertContains(response, '&lt;img src=x onerror=alert(1)&gt;')
        self.assertNotContains(response, '<img src=x onerror=alert(1)>')
        self.assertNotContains(response, 'Nom interne')
