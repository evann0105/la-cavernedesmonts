from datetime import date, datetime, time, timezone as datetime_timezone
from unittest.mock import patch

from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils.html import escape

from .models import OpeningDay, OpeningHours, OpeningPeriod, OpeningPeriodDay
from .services import public_hours


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True, TIME_ZONE='Europe/Paris')
class OpeningPeriodTests(TestCase):
    backend = 'connexion.backends.EmailOrUsernameBackend'
    time_fields = ('opens_at', 'closes_at', 'reopens_at', 'recloses_at')

    def setUp(self):
        self.manager = get_user_model().objects.create_user('holiday-hours-manager', is_staff=True)
        self.manager.user_permissions.add(Permission.objects.get(
            content_type__app_label='core', codename='manage_catalog'))
        self.client.force_login(self.manager, backend=self.backend)
        self.schedule = OpeningHours.objects.get(pk=1)
        self.add_url = reverse('boutique:period_add')

    def period(self, start=date(2030, 2, 10), end=date(2030, 2, 20), **overrides):
        values = {'schedule': self.schedule, 'name': 'Vacances de février',
                  'start_date': start, 'end_date': end, 'notice': 'Horaires des vacances.'}
        values.update(overrides)
        period = OpeningPeriod.objects.create(**values)
        for day in self.schedule.days.all():
            OpeningPeriodDay.objects.create(
                period=period, weekday=day.weekday, is_closed=day.is_closed,
                **{field: getattr(day, field) for field in self.time_fields})
        period.days.filter(weekday=2).update(
            is_closed=False, opens_at=time(7, 5), closes_at=time(17, 35),
            reopens_at=None, recloses_at=None)
        return period

    def data(self, period=None, **overrides):
        days = period.days.all() if period else self.schedule.days.all()
        data = {
            'name': period.name if period else 'Vacances de février',
            'start_date': str(period.start_date) if period else '2030-02-10',
            'end_date': str(period.end_date) if period else '2030-02-20',
            'notice': period.notice if period else 'Horaires des vacances.',
            'revision': str(period.revision) if period else '0',
            'days-TOTAL_FORMS': '7',
            'days-INITIAL_FORMS': '7' if period else '0',
            'days-MIN_NUM_FORMS': '7',
            'days-MAX_NUM_FORMS': '7',
        }
        for index, day in enumerate(days):
            if period:
                data[f'days-{index}-id'] = str(day.pk)
            if day.is_closed:
                data[f'days-{index}-is_closed'] = 'on'
            for field in self.time_fields:
                value = getattr(day, field)
                data[f'days-{index}-{field}'] = value.strftime('%H:%M') if value else ''
        data.update(overrides)
        return data

    def usual_values(self):
        return (
            OpeningHours.objects.values('notice', 'revision', 'updated_by_id').get(pk=1),
            list(OpeningDay.objects.filter(schedule_id=1).order_by('weekday').values(
                'weekday', 'is_closed', *self.time_fields)),
        )

    def period_values(self):
        return (
            list(OpeningPeriod.objects.order_by('pk').values()),
            list(OpeningPeriodDay.objects.order_by('pk').values()),
        )

    def assert_rejected_without_changes(self, url, data):
        before = self.period_values(), self.usual_values(), LogEntry.objects.count()
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual((self.period_values(), self.usual_values(), LogEntry.objects.count()), before)
        return response

    def test_new_form_copies_usual_week_without_creating_rows(self):
        before = self.period_values(), self.usual_values()
        response = self.client.get(self.add_url)
        self.assertEqual(response.status_code, 200)
        forms = response.context['days_formset']
        self.assertEqual([form.day_label for form in forms],
                         ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche'])
        self.assertEqual(forms.initial_form_count(), 0)
        self.assertEqual(forms.total_form_count(), 7)
        for form, usual in zip(forms, self.schedule.days.all()):
            self.assertEqual(form['is_closed'].value(), usual.is_closed)
            for field in self.time_fields:
                self.assertEqual(form[field].value(), getattr(usual, field))
        self.assertEqual((self.period_values(), self.usual_values()), before)
        self.assertIn('no-store', response['Cache-Control'])

    def test_manager_can_plan_complete_week_without_changing_usual_hours(self):
        before = self.usual_values()
        data = self.data(**{'days-0-is_closed': '', 'days-0-opens_at': '10:15',
                           'days-0-closes_at': '16:45'})
        response = self.client.post(self.add_url, data)
        self.assertEqual(response.status_code, 302)
        period = OpeningPeriod.objects.get()
        self.assertEqual((period.name, period.start_date, period.end_date, period.notice),
                         ('Vacances de février', date(2030, 2, 10), date(2030, 2, 20),
                          'Horaires des vacances.'))
        self.assertEqual(period.updated_by, self.manager)
        self.assertFalse(period.is_cancelled)
        self.assertEqual(list(period.days.values_list('weekday', flat=True)), list(range(7)))
        monday = period.days.get(weekday=0)
        self.assertEqual((monday.is_closed, monday.opens_at, monday.closes_at),
                         (False, time(10, 15), time(16, 45)))
        self.assertEqual(self.usual_values(), before)
        self.assertTrue(LogEntry.objects.filter(
            user=self.manager, action_flag=ADDITION, object_id=str(period.pk),
            content_type=ContentType.objects.get_for_model(OpeningPeriod)).exists())

    def test_first_and_last_date_are_included_then_normal_hours_return_automatically(self):
        period = self.period()
        usual = self.usual_values()
        expected_usual = [{'start': time(9, 30), 'end': time(12)},
                          {'start': time(14, 30), 'end': time(18, 30)}]
        expected_holiday = [{'start': time(7, 5), 'end': time(17, 35)}]
        for day, active in [(date(2030, 2, 9), False), (date(2030, 2, 10), True),
                            (date(2030, 2, 15), True), (date(2030, 2, 20), True),
                            (date(2030, 2, 21), False)]:
            with self.subTest(day=day):
                context = public_hours(today=day)
                self.assertEqual(context['opening_period'], period if active else None)
                self.assertEqual(context['opening_days'][2]['periods'],
                                 expected_holiday if active else expected_usual)
                self.assertEqual(context['opening_notice'], period.notice if active else self.schedule.notice)
        self.assertEqual(self.usual_values(), usual)

    def test_single_day_period_only_applies_on_that_date(self):
        period = self.period(start=date(2030, 3, 3), end=date(2030, 3, 3))
        self.assertEqual(public_hours(today=date(2030, 3, 3))['opening_period'], period)
        self.assertIsNone(public_hours(today=date(2030, 3, 2))['opening_period'])
        self.assertIsNone(public_hours(today=date(2030, 3, 4))['opening_period'])

    def test_usual_hours_changed_during_vacation_are_used_after_its_end(self):
        period = self.period()
        data = self.data(notice='Nouvelle note habituelle', revision=str(self.schedule.revision),
                         **{'days-INITIAL_FORMS': '7', 'days-2-opens_at': '08:25'})
        for index, day in enumerate(self.schedule.days.all()):
            data[f'days-{index}-id'] = str(day.pk)
        self.assertEqual(self.client.post(reverse('boutique:hours'), data).status_code, 302)
        during = public_hours(today=date(2030, 2, 15))
        self.assertEqual(during['opening_days'][2]['periods'][0]['start'], time(7, 5))
        self.assertEqual(during['opening_notice'], period.notice)
        after = public_hours(today=date(2030, 2, 21))
        self.assertIsNone(after['opening_period'])
        self.assertEqual(after['opening_days'][2]['periods'][0]['start'], time(8, 25))
        self.assertEqual(after['opening_notice'], 'Nouvelle note habituelle')

    def test_automatic_selection_uses_paris_calendar_date_instead_of_utc(self):
        period = self.period(start=date(2030, 12, 20), end=date(2030, 12, 20))
        paris_next_day = datetime(2030, 12, 19, 23, 30, tzinfo=datetime_timezone.utc)
        with patch('django.utils.timezone.now', return_value=paris_next_day):
            self.assertEqual(public_hours()['opening_period'], period)
        paris_after_end = datetime(2030, 12, 20, 23, 30, tzinfo=datetime_timezone.utc)
        with patch('django.utils.timezone.now', return_value=paris_after_end):
            self.assertIsNone(public_hours()['opening_period'])

    def test_reversed_missing_and_invalid_dates_cannot_create_partial_period(self):
        for overrides in ({'start_date': '2030-02-21'}, {'start_date': ''},
                          {'end_date': ''}, {'end_date': '2030-02-30'}):
            with self.subTest(overrides=overrides):
                self.assert_rejected_without_changes(self.add_url, self.data(**overrides))

    def test_incomplete_and_overlapping_daily_slots_cannot_create_partial_period(self):
        for overrides in ({'days-2-closes_at': ''}, {'days-2-opens_at': '25:00'},
                          {'days-2-closes_at': '08:00'}, {'days-2-reopens_at': '11:30'},
                          {'days-2-recloses_at': ''}):
            with self.subTest(overrides=overrides):
                self.assert_rejected_without_changes(self.add_url, self.data(**overrides))

    def test_closed_period_day_clears_all_posted_times(self):
        data = self.data(**{'days-2-is_closed': 'on'})
        self.assertEqual(self.client.post(self.add_url, data).status_code, 302)
        wednesday = OpeningPeriod.objects.get().days.get(weekday=2)
        self.assertTrue(wednesday.is_closed)
        self.assertEqual([getattr(wednesday, field) for field in self.time_fields], [None] * 4)

    def test_overlapping_dates_including_shared_boundary_are_rejected(self):
        self.period()
        for start, end in [('2030-02-09', '2030-02-10'), ('2030-02-20', '2030-02-21'),
                           ('2030-02-11', '2030-02-19'), ('2030-02-09', '2030-02-21')]:
            with self.subTest(start=start, end=end):
                self.assert_rejected_without_changes(self.add_url,
                    self.data(start_date=start, end_date=end))

    def test_adjacent_periods_are_allowed_and_selected_on_their_own_dates(self):
        original = self.period()
        response = self.client.post(self.add_url, self.data(
            name='Période suivante', start_date='2030-02-21', end_date='2030-02-28'))
        self.assertEqual(response.status_code, 302)
        following = OpeningPeriod.objects.get(name='Période suivante')
        self.assertEqual(public_hours(today=date(2030, 2, 20))['opening_period'], original)
        self.assertEqual(public_hours(today=date(2030, 2, 21))['opening_period'], following)

    def test_cancelled_period_does_not_prevent_reusing_its_dates(self):
        self.period(is_cancelled=True)
        self.assertEqual(self.client.post(self.add_url, self.data()).status_code, 302)
        self.assertEqual(OpeningPeriod.objects.filter(is_cancelled=False).count(), 1)

    def test_missing_extra_and_forged_initial_days_are_rejected(self):
        for field, value in [('days-TOTAL_FORMS', '6'), ('days-TOTAL_FORMS', '8'),
                             ('days-TOTAL_FORMS', ''), ('days-INITIAL_FORMS', '7')]:
            with self.subTest(field=field, value=value):
                self.assert_rejected_without_changes(self.add_url, self.data(**{field: value}))

    def test_edit_updates_its_week_and_dates_without_touching_usual_schedule(self):
        period = self.period()
        before = self.usual_values()
        url = reverse('boutique:period_edit', args=[period.pk])
        data = self.data(period, name='Vacances prolongées', end_date='2030-02-22',
                         **{'days-2-opens_at': '08:10'})
        self.assertEqual(self.client.post(url, data).status_code, 302)
        previous_revision = period.revision
        period.refresh_from_db()
        self.assertEqual(period.name, 'Vacances prolongées')
        self.assertEqual(period.end_date, date(2030, 2, 22))
        self.assertEqual(period.revision, previous_revision + 1)
        self.assertEqual(period.updated_by, self.manager)
        self.assertEqual(period.days.get(weekday=2).opens_at, time(8, 10))
        self.assertEqual(self.usual_values(), before)

    def test_edit_rejects_overlap_with_another_period_without_partial_save(self):
        period = self.period()
        self.period(start=date(2030, 3, 1), end=date(2030, 3, 10), name='Vacances suivantes')
        self.assert_rejected_without_changes(reverse('boutique:period_edit', args=[period.pk]),
            self.data(period, name='Ne doit pas changer', end_date='2030-03-01'))

    def test_stale_edit_cannot_replace_newer_period(self):
        period = self.period()
        url = reverse('boutique:period_edit', args=[period.pk])
        stale = self.data(period, name='Ancienne version')
        self.assertEqual(self.client.post(url, self.data(period, name='Version récente')).status_code, 302)
        self.assert_rejected_without_changes(url, stale)
        period.refresh_from_db()
        self.assertEqual(period.name, 'Version récente')

    def test_edit_rejects_foreign_duplicated_or_reordered_day_ids(self):
        period = self.period()
        another = self.period(start=date(2030, 4, 1), end=date(2030, 4, 8))
        data = self.data(period)
        cases = ({'days-0-id': ''}, {'days-0-id': '999999999'},
                 {'days-0-id': str(another.days.first().pk)},
                 {'days-0-id': data['days-1-id']},
                 {'days-0-id': data['days-1-id'], 'days-1-id': data['days-0-id']})
        for overrides in cases:
            with self.subTest(overrides=overrides):
                self.assert_rejected_without_changes(reverse('boutique:period_edit', args=[period.pk]),
                                                     self.data(period, **overrides))

    def test_cancel_requires_post_confirmation_then_immediately_restores_usual_hours(self):
        period = self.period()
        url = reverse('boutique:period_cancel', args=[period.pk])
        before = self.period_values(), self.usual_values()
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual((self.period_values(), self.usual_values()), before)
        previous_revision = period.revision
        self.assertEqual(self.client.post(url, {'revision': str(period.revision)}).status_code, 302)
        period.refresh_from_db()
        self.assertTrue(period.is_cancelled)
        self.assertEqual(period.revision, previous_revision + 1)
        self.assertEqual(period.days.count(), 7)
        self.assertIsNone(public_hours(today=date(2030, 2, 15))['opening_period'])
        self.assertEqual(self.usual_values(), before[1])
        self.assertTrue(LogEntry.objects.filter(
            user=self.manager, action_flag=CHANGE, object_id=str(period.pk),
            content_type=ContentType.objects.get_for_model(OpeningPeriod)).exists())

    def test_stale_or_missing_cancellation_revision_does_not_cancel_newer_period(self):
        period = self.period()
        url = reverse('boutique:period_cancel', args=[period.pk])
        OpeningPeriod.objects.filter(pk=period.pk).update(revision=period.revision + 1)
        for data in ({'revision': str(period.revision)}, {}):
            with self.subTest(data=data):
                self.assert_rejected_without_changes(url, data)

    def test_editing_cancelled_period_does_not_reactivate_it_implicitly(self):
        period = self.period(is_cancelled=True)
        url = reverse('boutique:period_edit', args=[period.pk])
        self.assertEqual(self.client.post(url, self.data(period, name='Vacances corrigées')).status_code, 302)
        period.refresh_from_db()
        self.assertEqual(period.name, 'Vacances corrigées')
        self.assertTrue(period.is_cancelled)
        self.assertIsNone(public_hours(today=date(2030, 2, 15))['opening_period'])

    def test_restore_requires_explicit_post_then_reactivates_the_same_period(self):
        period = self.period(is_cancelled=True)
        url = reverse('boutique:period_restore', args=[period.pk])
        before = self.period_values(), self.usual_values()
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual((self.period_values(), self.usual_values()), before)
        previous_revision = period.revision
        self.assertEqual(self.client.post(url, {'revision': str(period.revision)}).status_code, 302)
        period.refresh_from_db()
        self.assertFalse(period.is_cancelled)
        self.assertEqual(period.revision, previous_revision + 1)
        self.assertEqual(period.updated_by, self.manager)
        self.assertEqual(period.days.count(), 7)
        self.assertEqual(public_hours(today=date(2030, 2, 15))['opening_period'], period)
        self.assertEqual(self.usual_values(), before[1])

    def test_restore_rejects_overlap_with_a_period_created_after_cancellation(self):
        period = self.period(is_cancelled=True)
        self.period(name='Vacances de remplacement')
        self.assert_rejected_without_changes(reverse('boutique:period_restore', args=[period.pk]),
                                             {'revision': str(period.revision)})
        period.refresh_from_db()
        self.assertTrue(period.is_cancelled)

    def test_stale_or_missing_restore_revision_cannot_reactivate_period(self):
        period = self.period(is_cancelled=True)
        url = reverse('boutique:period_restore', args=[period.pk])
        OpeningPeriod.objects.filter(pk=period.pk).update(revision=period.revision + 1)
        for data in ({'revision': str(period.revision)}, {}):
            with self.subTest(data=data):
                self.assert_rejected_without_changes(url, data)

    def test_unknown_period_day_is_not_replaced_with_usual_hours(self):
        period = self.period()
        period.days.filter(weekday=2).delete()
        context = public_hours(today=date(2030, 2, 15))
        self.assertEqual(context['opening_period'], period)
        self.assertEqual(context['opening_days'][2],
                         {'name': 'Mercredi', 'closed': False, 'known': False, 'periods': []})

    def test_public_pages_show_active_period_dates_hours_and_escaped_notice(self):
        notice = '<script>alert("vacances")</script> Horaires spéciaux de février.'
        self.period(notice=notice)
        public = Client()
        for route in ('core:location', 'core:location_legacy', 'espace:contact'):
            with self.subTest(route=route), patch('django.utils.timezone.localdate', return_value=date(2030, 2, 15)):
                response = public.get(reverse(route))
                self.assertContains(response, '07:05')
                self.assertContains(response, '17:35')
                self.assertContains(response, 'Vacances de février')
                self.assertContains(response, '10/02/2030')
                self.assertContains(response, '20/02/2030')
                self.assertContains(response, escape(notice))
                self.assertNotContains(response, '<script>alert("vacances")</script>')
                self.assertNotContains(response, self.schedule.notice)

    def test_unprivileged_visitors_cannot_create_edit_or_cancel_periods(self):
        period = self.period()
        urls = (self.add_url, reverse('boutique:period_edit', args=[period.pk]),
                reverse('boutique:period_cancel', args=[period.pk]),
                reverse('boutique:period_restore', args=[period.pk]))
        before = self.period_values(), self.usual_values()
        self.client.logout()
        for url in urls:
            for method in (self.client.get, self.client.post):
                response = method(url, self.data(period))
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.url.startswith(reverse('two_factor:login') + '?next='))
        for username, staff in [('holiday-customer', False), ('holiday-staff', True)]:
            user = get_user_model().objects.create_user(username, is_staff=staff)
            self.client.force_login(user, backend=self.backend)
            for url in urls:
                for method in (self.client.get, self.client.post):
                    self.assertEqual(method(url, self.data(period)).status_code, 403)
        self.assertEqual((self.period_values(), self.usual_values()), before)

    def test_csrf_token_is_required_for_all_period_mutations(self):
        period = self.period()
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.manager, backend=self.backend)
        before = self.period_values(), self.usual_values()
        for url in (self.add_url, reverse('boutique:period_edit', args=[period.pk]),
                    reverse('boutique:period_cancel', args=[period.pk]),
                    reverse('boutique:period_restore', args=[period.pk])):
            self.assertEqual(strict.post(url, self.data(period)).status_code, 403)
        self.assertEqual((self.period_values(), self.usual_values()), before)

    def test_unsupported_methods_cannot_change_periods(self):
        period = self.period()
        before = self.period_values(), self.usual_values()
        for url in (self.add_url, reverse('boutique:period_edit', args=[period.pk]),
                    reverse('boutique:period_cancel', args=[period.pk]),
                    reverse('boutique:period_restore', args=[period.pk])):
            self.assertEqual(self.client.put(url).status_code, 405)
            self.assertEqual(self.client.delete(url).status_code, 405)
        self.assertEqual((self.period_values(), self.usual_values()), before)

    def test_database_rejects_reversed_dates_duplicate_weekdays_and_invalid_slots(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            OpeningPeriod.objects.create(schedule=self.schedule, name='Dates impossibles',
                                         start_date=date(2030, 2, 20), end_date=date(2030, 2, 10))
        period = self.period()
        with self.assertRaises(IntegrityError), transaction.atomic():
            OpeningPeriodDay.objects.create(period=period, weekday=0, is_closed=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            period.days.filter(weekday=2).update(closes_at=time(6))
