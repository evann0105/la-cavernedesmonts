from datetime import time

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils.html import escape

from connexion.models import EmailVerification
from connexion.test_helpers import verify_test_user

from .models import OpeningDay, OpeningHours
from .services import public_hours


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True)
class OpeningHoursTests(TestCase):
    backend = 'connexion.backends.EmailOrUsernameBackend'
    time_fields = ('opens_at', 'closes_at', 'reopens_at', 'recloses_at')

    def setUp(self):
        self.manager = get_user_model().objects.create_user('hours-manager', is_staff=True)
        self.manager.user_permissions.add(Permission.objects.get(
            content_type__app_label='core', codename='manage_catalog'))
        self.client.force_login(self.manager, backend=self.backend)
        self.schedule = OpeningHours.objects.get(pk=1)
        self.url = reverse('boutique:hours')

    def data(self, **overrides):
        schedule = OpeningHours.objects.get(pk=1)
        data = {
            'notice': schedule.notice,
            'revision': str(schedule.revision),
            'days-TOTAL_FORMS': '7',
            'days-INITIAL_FORMS': '7',
            'days-MIN_NUM_FORMS': '7',
            'days-MAX_NUM_FORMS': '7',
        }
        for index, day in enumerate(schedule.days.order_by('weekday')):
            data[f'days-{index}-id'] = str(day.pk)
            if day.is_closed:
                data[f'days-{index}-is_closed'] = 'on'
            for field in self.time_fields:
                value = getattr(day, field)
                data[f'days-{index}-{field}'] = value.strftime('%H:%M') if value else ''
        data.update(overrides)
        return data

    def saved_values(self):
        return (
            OpeningHours.objects.values('notice', 'revision', 'updated_by_id').get(pk=1),
            list(OpeningDay.objects.filter(schedule_id=1).order_by('weekday').values(
                'id', 'weekday', 'is_closed', *self.time_fields)),
        )

    def assert_rejected_without_changes(self, data):
        before = self.saved_values()
        log_count = LogEntry.objects.count()
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['hours_form'].errors or
                        any(response.context['days_formset'].errors) or
                        response.context['days_formset'].non_form_errors())
        self.assertEqual(self.saved_values(), before)
        self.assertEqual(LogEntry.objects.count(), log_count)
        return response

    def test_initial_schedule_preserves_existing_hours_and_weekday_order(self):
        days = list(self.schedule.days.all())
        self.assertEqual([day.weekday for day in days], list(range(7)))
        self.assertTrue(all(day.is_closed for day in days[:2]))
        for day in days[2:6]:
            self.assertEqual((day.is_closed, day.opens_at, day.closes_at,
                              day.reopens_at, day.recloses_at),
                             (False, time(9, 30), time(12), time(14, 30), time(18, 30)))
        self.assertEqual((days[6].is_closed, days[6].opens_at, days[6].closes_at,
                          days[6].reopens_at, days[6].recloses_at),
                         (False, time(9, 30), time(12), None, None))
        self.assertIn('haute saison', self.schedule.notice)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([form.day_label for form in response.context['days_formset']],
                         ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche'])
        self.assertIn('no-store', response['Cache-Control'])

    def test_catalog_manager_saves_whole_week_note_and_audit(self):
        data = self.data(notice='Horaires des vacances : appelez avant de venir.')
        data.update({'days-0-is_closed': '', 'days-0-opens_at': '08:15',
                     'days-0-closes_at': '11:45', 'days-0-reopens_at': '14:00',
                     'days-0-recloses_at': '19:10'})
        original_revision = self.schedule.revision
        self.assertRedirects(self.client.post(self.url, data), self.url)
        self.schedule.refresh_from_db()
        monday = self.schedule.days.get(weekday=0)
        self.assertEqual((monday.is_closed, monday.opens_at, monday.closes_at,
                          monday.reopens_at, monday.recloses_at),
                         (False, time(8, 15), time(11, 45), time(14), time(19, 10)))
        self.assertEqual(self.schedule.notice, data['notice'])
        self.assertEqual(self.schedule.revision, original_revision + 1)
        self.assertEqual(self.schedule.updated_by, self.manager)
        self.assertEqual(self.schedule.days.count(), 7)
        self.assertTrue(LogEntry.objects.filter(
            user=self.manager, action_flag=CHANGE, object_id='1',
            content_type=ContentType.objects.get_for_model(OpeningHours)).exists())

    def test_closed_day_clears_previously_entered_times(self):
        data = self.data(**{'days-2-is_closed': 'on'})
        self.assertRedirects(self.client.post(self.url, data), self.url)
        wednesday = self.schedule.days.get(weekday=2)
        self.assertTrue(wednesday.is_closed)
        self.assertEqual([getattr(wednesday, field) for field in self.time_fields], [None] * 4)

    def test_one_continuous_opening_period_and_empty_notice_are_allowed(self):
        data = self.data(notice='', **{'days-2-opens_at': '08:00',
                        'days-2-closes_at': '18:00', 'days-2-reopens_at': '',
                        'days-2-recloses_at': ''})
        self.assertRedirects(self.client.post(self.url, data), self.url)
        wednesday = self.schedule.days.get(weekday=2)
        self.assertEqual((wednesday.opens_at, wednesday.closes_at,
                          wednesday.reopens_at, wednesday.recloses_at),
                         (time(8), time(18), None, None))
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.notice, '')

    def test_incomplete_reversed_overlapping_and_invalid_times_do_not_partially_save(self):
        invalid_days = (
            {'opens_at': ''},
            {'closes_at': ''},
            {'opens_at': '25:00'},
            {'closes_at': '09:30'},
            {'closes_at': '08:30'},
            {'reopens_at': ''},
            {'recloses_at': ''},
            {'recloses_at': '14:00'},
            {'reopens_at': '11:30'},
        )
        for overrides in invalid_days:
            with self.subTest(overrides=overrides):
                data = self.data(notice='Cette note ne doit pas être enregistrée.')
                data.update({f'days-2-{field}': value for field, value in overrides.items()})
                response = self.assert_rejected_without_changes(data)
                self.assertEqual(response.context['hours_form']['notice'].value(), data['notice'])

    def test_overlong_notice_prevents_all_day_changes(self):
        data = self.data(notice='x' * 601, **{'days-2-opens_at': '08:15'})
        self.assert_rejected_without_changes(data)

    def test_missing_or_forged_form_management_cannot_drop_or_add_days(self):
        for field, value in (
            ('days-TOTAL_FORMS', '6'),
            ('days-TOTAL_FORMS', '8'),
            ('days-TOTAL_FORMS', ''),
            ('days-INITIAL_FORMS', '6'),
            ('days-INITIAL_FORMS', '0'),
        ):
            with self.subTest(field=field, value=value):
                self.assert_rejected_without_changes(self.data(**{field: value}))
        data = self.data()
        del data['days-TOTAL_FORMS']
        self.assert_rejected_without_changes(data)

    def test_missing_unknown_duplicate_or_reordered_day_ids_are_rejected(self):
        initial = self.data()
        cases = (
            {'days-0-id': ''},
            {'days-0-id': '999999999'},
            {'days-0-id': initial['days-1-id']},
            {'days-0-id': initial['days-1-id'], 'days-1-id': initial['days-0-id']},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                self.assert_rejected_without_changes(self.data(**overrides))

    def test_stale_form_cannot_overwrite_newer_hours(self):
        stale = self.data(notice='Ancienne note')
        self.assertRedirects(self.client.post(self.url, self.data(notice='Nouvelle note')), self.url)
        response = self.assert_rejected_without_changes(stale)
        self.assertTrue(response.context['hours_form'].non_field_errors())
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.notice, 'Nouvelle note')

    def test_missing_revision_is_rejected(self):
        data = self.data()
        del data['revision']
        self.assert_rejected_without_changes(data)

    def test_anonymous_customer_and_staff_without_catalog_permission_cannot_edit(self):
        self.client.logout()
        for method in (self.client.get, self.client.post):
            response = method(self.url, self.data())
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.url.startswith(reverse('two_factor:login') + '?next='))
        for name, staff in [('hours-customer', False), ('hours-staff', True)]:
            user = get_user_model().objects.create_user(name, is_staff=staff)
            self.client.force_login(user, backend=self.backend)
            for method in (self.client.get, self.client.post):
                self.assertEqual(method(self.url, self.data()).status_code, 403)
        self.schedule.refresh_from_db()
        self.assertIsNone(self.schedule.updated_by)

    @override_settings(LOCAL_SKIP_EMAIL_VERIFICATION=False)
    def test_manager_must_verify_email_outside_local_bypass(self):
        EmailVerification.objects.filter(user=self.manager).delete()
        before = self.saved_values()
        for method in (self.client.get, self.client.post):
            self.assertRedirects(method(self.url, self.data()), reverse('connexion:verify_email'),
                                 fetch_redirect_response=False)
        self.assertEqual(self.saved_values(), before)
        verify_test_user(self.manager)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_csrf_required_but_form_token_allows_authorized_save(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.manager, backend=self.backend)
        before = self.saved_values()
        self.assertEqual(strict.post(self.url, self.data()).status_code, 403)
        self.assertEqual(self.saved_values(), before)
        self.assertEqual(strict.get(self.url).status_code, 200)
        token = strict.cookies['csrftoken'].value
        response = strict.post(self.url, self.data(notice='Enregistré avec le jeton du formulaire'),
                               HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 302)

    def test_reading_does_not_change_schedule_and_unsupported_methods_are_rejected(self):
        before = self.saved_values()
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.put(self.url).status_code, 405)
        self.assertEqual(self.client.delete(self.url).status_code, 405)
        self.assertEqual(self.saved_values(), before)

    def test_updates_appear_on_all_public_hours_pages_with_escaped_notice(self):
        urls = [reverse('core:location'), reverse('core:location_legacy'), reverse('espace:contact')]
        public = Client()
        for url in urls:
            self.assertEqual(public.get(url).status_code, 200)
        notice = '<script>alert("horaires")</script> Fermeture exceptionnelle dimanche.'
        data = self.data(notice=notice, **{'days-2-opens_at': '07:45', 'days-2-closes_at': '10:15'})
        self.assertRedirects(self.client.post(self.url, data), self.url)
        for url in urls:
            with self.subTest(url=url):
                response = public.get(url)
                self.assertContains(response, '07:45')
                self.assertContains(response, '10:15')
                self.assertContains(response, escape(notice))
                self.assertNotContains(response, '<script>alert("horaires")</script>')

    def test_public_reader_returns_ordered_periods_and_closed_days(self):
        context = public_hours()
        self.assertEqual([day['name'] for day in context['opening_days']],
                         ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche'])
        monday, wednesday = context['opening_days'][0], context['opening_days'][2]
        self.assertEqual((monday['known'], monday['closed'], monday['periods']), (True, True, []))
        self.assertEqual((wednesday['known'], wednesday['closed']), (True, False))
        self.assertEqual(wednesday['periods'], [
            {'start': time(9, 30), 'end': time(12)},
            {'start': time(14, 30), 'end': time(18, 30)},
        ])
        self.assertEqual(context['opening_notice'], self.schedule.notice)

    def test_missing_public_day_is_unknown_instead_of_inventing_a_closure(self):
        self.schedule.days.filter(weekday=0).delete()
        monday = public_hours()['opening_days'][0]
        self.assertEqual((monday['name'], monday['known'], monday['closed'], monday['periods']),
                         ('Lundi', False, False, []))
        self.assertContains(Client().get(reverse('core:location')), 'À confirmer')

    def test_missing_public_schedule_is_handled_without_recreating_data(self):
        self.schedule.delete()
        self.assertEqual(public_hours(), {'opening_days': [], 'opening_notice': ''})
        self.assertEqual(Client().get(reverse('core:location')).status_code, 200)
        self.assertEqual(Client().get(reverse('espace:contact')).status_code, 200)
        self.assertFalse(OpeningHours.objects.exists())

    def test_account_and_native_admin_link_to_single_friendly_editor(self):
        self.assertContains(self.client.get(reverse('espace:account')), self.url)
        changelist = reverse('admin:boutique_openinghours_changelist')
        self.assertContains(self.client.get(reverse('admin:index')), changelist)
        self.assertRedirects(self.client.get(changelist), self.url)
        self.assertRedirects(self.client.get(reverse('admin:boutique_openinghours_change', args=[1])), self.url)
        self.assertEqual(self.client.get(reverse('admin:boutique_openinghours_add')).status_code, 403)
        self.assertEqual(self.client.get(reverse('admin:boutique_openinghours_delete', args=[1])).status_code, 403)
        customer = get_user_model().objects.create_user('hours-nav-customer')
        self.client.force_login(customer, backend=self.backend)
        self.assertNotContains(self.client.get(reverse('espace:account')), self.url)

    def test_database_rejects_duplicate_days_and_invalid_periods(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            OpeningHours.objects.create(pk=2)
        with self.assertRaises(IntegrityError), transaction.atomic():
            OpeningDay.objects.create(schedule=self.schedule, weekday=0, is_closed=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.schedule.days.filter(weekday=2).update(closes_at=time(8))
