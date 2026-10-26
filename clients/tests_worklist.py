"""SAV reminders on the seller dashboard, separate from financial reporting."""
import uuid
from datetime import date, datetime, timedelta, timezone as datetime_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from connexion.test_helpers import verify_test_user
from .models import SupportCase, SupportEvent
from .worklist import dashboard_attention


@override_settings(
    TIME_ZONE='Europe/Paris',
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    LOCAL_SKIP_EMAIL_VERIFICATION=False,
)
class SupportDashboardTests(TestCase):
    def case(self, **extra):
        values = {'creation_key': uuid.uuid4(), 'creation_fingerprint': 'a' * 64,
                  'customer_name': 'Camille Montagne', 'customer_email': 'camille@example.test',
                  'phone': '', 'subject': 'Vérifier la livraison', 'description': 'Rappeler le client.'}
        values.update(extra)
        return SupportCase.objects.create(**values)

    def user(self, username, *, catalog=False, support=False, staff=True):
        user = get_user_model().objects.create_user(username=username, email=f'{username}@example.test', is_staff=staff)
        if catalog:
            user.user_permissions.add(Permission.objects.get(codename='manage_catalog', content_type__app_label='core'))
        if support:
            user.user_permissions.add(Permission.objects.get(codename='manage_returns', content_type__app_label='retours'))
        verify_test_user(user)
        return user

    def login(self, user):
        self.client.force_login(user, backend='connexion.backends.EmailOrUsernameBackend')

    def test_counts_exclude_resolved_and_total_counts_each_case_once(self):
        today = timezone.localdate()
        due = self.case(due_date=today, state='waiting_customer', priority='urgent')
        late = self.case(due_date=today - timedelta(days=1), state='in_progress')
        urgent = self.case(priority='urgent')
        self.case(due_date=today + timedelta(days=1))
        self.case()
        self.case(due_date=today, priority='urgent', state='resolved', resolved_at=timezone.now())
        self.case(due_date=today - timedelta(days=1), state='resolved', resolved_at=timezone.now())
        result = dashboard_attention()
        self.assertEqual({card['key']: card['count'] for card in result['cards']}, {'today': 1, 'overdue': 1, 'urgent': 2})
        self.assertEqual(result['total'], 3)
        self.assertCountEqual([case.pk for case in result['records']], [due.pk, late.pk, urgent.pk])

    def test_queue_prioritizes_late_then_today_then_other_urgencies_and_limits_to_five(self):
        today = timezone.localdate()
        late = self.case(due_date=today - timedelta(days=3))
        late_urgent = self.case(due_date=today - timedelta(days=1), priority='urgent')
        due = self.case(due_date=today)
        due_urgent = self.case(due_date=today, priority='urgent')
        future_urgent = self.case(due_date=today + timedelta(days=3), priority='urgent')
        undated_urgent = self.case(priority='urgent')
        result = dashboard_attention()
        self.assertEqual(result['total'], 6)
        self.assertEqual([case.pk for case in result['records']],
                         [late_urgent.pk, late.pk, due_urgent.pk, due.pk, future_urgent.pk])
        self.assertNotIn(undated_urgent.pk, [case.pk for case in result['records']])

    def test_read_only_bounded_queries_and_no_notification(self):
        self.case(due_date=timezone.localdate())
        before = SupportCase.objects.values().get()
        with CaptureQueriesContext(connection) as queries:
            result = dashboard_attention()
            for case in result['records']:
                _ = (case.reference, case.customer_name, case.subject, case.due_date,
                     case.priority, case.get_state_display())
        self.assertEqual(len(queries), 2)
        self.assertFalse(any(query['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for query in queries))
        self.assertEqual(SupportCase.objects.values().get(), before)
        self.assertFalse(SupportEvent.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_empty_queue_counts_zero_without_loading_contact_details(self):
        self.case(due_date=timezone.localdate() + timedelta(days=1))
        with self.assertNumQueries(1):
            result = dashboard_attention()
        self.assertEqual(result['total'], 0)
        self.assertEqual(result['records'], [])
        self.assertTrue(all(card['count'] == 0 for card in result['cards']))

    def test_reminder_dates_follow_paris_day_not_utc_in_summer(self):
        self.case(due_date=date(2026, 6, 1))
        self.case(due_date=date(2026, 6, 2))
        instant = datetime(2026, 6, 1, 22, 30, tzinfo=datetime_timezone.utc)
        with timezone.override('Europe/Paris'), patch('clients.worklist.timezone.now', return_value=instant):
            result = dashboard_attention()
        self.assertEqual(result['today'], date(2026, 6, 2))
        self.assertEqual({card['key']: card['count'] for card in result['cards']}, {'today': 1, 'overdue': 1, 'urgent': 0})

    def test_reminder_dates_follow_paris_day_after_winter_time_change(self):
        self.case(due_date=date(2026, 10, 26))
        self.case(due_date=date(2026, 10, 27))
        instant = datetime(2026, 10, 26, 23, 30, tzinfo=datetime_timezone.utc)
        with timezone.override('Europe/Paris'), patch('clients.worklist.timezone.now', return_value=instant):
            result = dashboard_attention()
        self.assertEqual(result['today'], date(2026, 10, 27))
        self.assertEqual({card['key']: card['count'] for card in result['cards']}, {'today': 1, 'overdue': 1, 'urgent': 0})

    def test_dashboard_permissions_do_not_disclose_support_to_catalog_only_staff(self):
        self.case(due_date=timezone.localdate(), subject='Sujet confidentiel SAV')
        url = reverse('tableau:dashboard')
        self.assertEqual(self.client.get(url).status_code, 302)
        for username, staff in [('customer', False), ('staff', True)]:
            self.login(self.user(username, staff=staff))
            self.assertEqual(self.client.get(url).status_code, 403)
        self.login(self.user('catalog-only', catalog=True))
        with patch('clients.worklist.dashboard_attention', side_effect=AssertionError('SAV queried without permission')):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['sav_attention'])
        self.assertNotContains(response, 'SAV : les rappels à prévoir')
        self.assertNotContains(response, 'Sujet confidentiel SAV')
        self.login(self.user('seller', catalog=True, support=True))
        response = self.client.get(url)
        self.assertContains(response, 'SAV : les rappels à prévoir')
        self.assertContains(response, 'Sujet confidentiel SAV')

    def test_sales_period_filters_do_not_change_support_counts_and_links(self):
        self.login(self.user('seller', catalog=True, support=True))
        case = self.case(due_date=timezone.localdate())
        url = reverse('tableau:dashboard')
        for filters in ({'annee': 2001, 'mois': 2}, {'annee': 2026, 'mois': 0}, {'annee': 'bad', 'mois': 99}):
            with self.subTest(filters=filters):
                response = self.client.get(url, filters)
                self.assertEqual(response.context['sav_attention']['total'], 1)
                self.assertEqual(response.context['sav_attention']['records'][0].pk, case.pk)
                destination = reverse('clients:case_list')
                for query in ('echeance=today', 'echeance=overdue', 'priorite=urgent'):
                    self.assertContains(response, f'href="{destination}?{query}"')
                self.assertContains(response, reverse('clients:case_detail', args=[case.pk]))

    def test_dashboard_empty_state_does_not_claim_all_support_cases_are_closed(self):
        self.login(self.user('seller', catalog=True, support=True))
        self.case()
        response = self.client.get(reverse('tableau:dashboard'))
        self.assertContains(response, 'Vos rappels sont à jour.')
        self.assertContains(response, 'Les autres demandes restent accessibles')
        self.assertContains(response, reverse('clients:case_list'))

    def test_dashboard_escapes_customer_name_and_subject(self):
        self.login(self.user('seller', catalog=True, support=True))
        script = '<script>alert(1)</script>'
        self.case(customer_name=script, subject=script, due_date=timezone.localdate())
        response = self.client.get(reverse('tableau:dashboard'))
        self.assertNotContains(response, script)
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')

    def test_full_dashboard_response_with_contacts_is_private_and_not_indexable(self):
        self.login(self.user('seller', catalog=True, support=True))
        self.case(due_date=timezone.localdate())
        response = self.client.get(reverse('tableau:dashboard'))
        self.assertContains(response, 'Camille Montagne')
        directives = {value.strip() for value in response['Cache-Control'].split(',')}
        self.assertTrue({'private', 'no-store', 'max-age=0'} <= directives)
        self.assertNotIn('public', directives)
        self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')
