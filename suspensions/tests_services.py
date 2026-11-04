from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from connexion.test_helpers import verify_test_user
from paiement.models import Order
from .exceptions import PauseConflict, PauseError, PurchasesPaused
from .models import PauseConfiguration, PauseEvent, PurchaseAdmission, PurchasePause
from .services import (admit_order, assert_purchases_open, cancel_pause,
    configuration_revision, public_state, resume_pause, save_pause)


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class PauseServiceTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user('pause-manager', is_staff=True, is_superuser=True)
        verify_test_user(self.manager)
        self.now = timezone.now().replace(microsecond=0)

    def data(self, **changes):
        data = {'name': 'Vacances privées', 'message': 'Notre catalogue reste à votre disposition.',
                'mode': 'scheduled', 'starts_at': self.now + timedelta(days=1),
                'ends_at': self.now + timedelta(days=5), 'revision': configuration_revision()}
        data.update(changes)
        return data

    def save(self, **changes):
        with patch('suspensions.services.timezone.now', return_value=self.now):
            return save_pause(self.data(**changes), self.manager)

    def test_public_reads_do_not_create_settings_or_change_records(self):
        with CaptureQueriesContext(connection) as queries:
            state = public_state(at=self.now)
        self.assertFalse(state['paused'])
        self.assertIsNone(state['next_change_at'])
        self.assertEqual(state['revision'], 0)
        self.assertFalse(PauseConfiguration.objects.exists())
        self.assertFalse([q for q in queries if q['sql'].lstrip().startswith(('INSERT', 'UPDATE', 'DELETE'))])

    def test_scheduled_pause_starts_at_inclusive_boundary_and_ends_exclusively(self):
        pause = self.save()
        self.assertFalse(public_state(at=pause.starts_at-timedelta(microseconds=1))['paused'])
        self.assertTrue(public_state(at=pause.starts_at)['paused'])
        self.assertTrue(public_state(at=pause.ends_at-timedelta(microseconds=1))['paused'])
        self.assertFalse(public_state(at=pause.ends_at)['paused'])
        self.assertEqual(PauseEvent.objects.count(), 1)
        self.assertEqual(PauseConfiguration.objects.get(pk=1).revision, 1)

    def test_immediate_start_uses_server_clock_not_forged_browser_date(self):
        pause = self.save(mode='now', starts_at=self.now-timedelta(days=100))
        self.assertEqual(pause.starts_at, self.now)
        self.assertTrue(public_state(at=self.now)['paused'])

    def test_touching_pauses_are_continuous_and_next_transition_updates_message(self):
        first = self.save()
        second = self.save(name='Deuxième période', starts_at=first.ends_at, ends_at=first.ends_at+timedelta(days=2))
        state = public_state(at=first.starts_at)
        self.assertEqual(state['resumes_at'], second.ends_at.isoformat())
        self.assertEqual(state['next_change_at'], first.ends_at.isoformat())
        self.assertTrue(public_state(at=first.ends_at)['paused'])
        self.assertFalse(public_state(at=second.ends_at)['paused'])

    def test_overlaps_and_containment_refused_atomically(self):
        first = self.save()
        for start, end in [(first.starts_at+timedelta(hours=1),first.ends_at),
                           (first.starts_at-timedelta(hours=1),first.ends_at+timedelta(hours=1)),
                           (first.starts_at-timedelta(hours=1),first.starts_at+timedelta(hours=1))]:
            with self.subTest(start=start), self.assertRaises(PauseError): self.save(starts_at=start, ends_at=end)
        self.assertEqual(PurchasePause.objects.count(), 1)
        self.assertEqual(configuration_revision(), 1)
        self.assertEqual(PauseEvent.objects.count(), 1)

    def test_active_pause_keeps_original_start_when_editing_and_extends_reopening(self):
        pause = self.save(mode='now')
        data = self.data(starts_at=self.now+timedelta(days=50), ends_at=self.now+timedelta(days=9))
        with patch('suspensions.services.timezone.now', return_value=self.now+timedelta(hours=1)):
            changed = save_pause(data, self.manager, pk=pause.pk)
        self.assertEqual(changed.starts_at, self.now)
        self.assertEqual(changed.ends_at, data['ends_at'])
        self.assertEqual(changed.revision, 2)

    def test_old_tab_cannot_replace_or_create_after_other_change(self):
        stale = self.data()
        self.save()
        with self.assertRaises(PauseConflict): save_pause(stale, self.manager)
        self.assertEqual(PurchasePause.objects.count(), 1)

    def test_noop_does_not_pollute_revision_or_journal_across_timezone_offsets(self):
        pause = self.save()
        data = self.data(starts_at=pause.starts_at.astimezone(ZoneInfo('Europe/Paris')),
                         ends_at=pause.ends_at.astimezone(ZoneInfo('Europe/Paris')))
        with patch('suspensions.services.timezone.now', return_value=self.now): save_pause(data, self.manager, pk=pause.pk)
        self.assertEqual(configuration_revision(), 1)
        self.assertEqual(PauseEvent.objects.count(), 1)

    def test_cancel_upcoming_and_resume_active_keep_history_and_reopen(self):
        future = self.save()
        with patch('suspensions.services.timezone.now', return_value=self.now):
            cancel_pause(future.pk, configuration_revision(), self.manager)
        future.refresh_from_db(); self.assertEqual(future.status, 'cancelled')
        active = self.save(mode='now')
        with patch('suspensions.services.timezone.now', return_value=self.now+timedelta(hours=1)):
            resume_pause(active.pk, configuration_revision(), self.manager)
        active.refresh_from_db()
        self.assertEqual(active.stop_kind, 'resume')
        self.assertFalse(public_state(at=self.now+timedelta(hours=1))['paused'])
        self.assertEqual(PauseEvent.objects.count(), 4)

    def test_actions_do_not_change_wrong_state_and_double_submit_conflicts(self):
        pause = self.save()
        revision = configuration_revision()
        with patch('suspensions.services.timezone.now', return_value=self.now):
            with self.assertRaises(PauseConflict): resume_pause(pause.pk, revision, self.manager)
            cancel_pause(pause.pk, revision, self.manager)
            with self.assertRaises(PauseConflict): cancel_pause(pause.pk, revision, self.manager)
        self.assertEqual(PauseEvent.objects.count(), 2)

    def test_finished_and_cancelled_periods_cannot_be_rewritten(self):
        pause = self.save()
        with patch('suspensions.services.timezone.now', return_value=pause.ends_at):
            with self.assertRaises(PauseError): save_pause(self.data(), self.manager, pk=pause.pk)
        with patch('suspensions.services.timezone.now', return_value=self.now):
            cancel_pause(pause.pk, configuration_revision(), self.manager)
            with self.assertRaises(PauseError): save_pause(self.data(), self.manager, pk=pause.pk)

    def test_invalid_ranges_types_modes_or_naive_dates_do_not_write(self):
        for change in ({'ends_at':self.now}, {'mode':'hack'}, {'starts_at':datetime(2027,1,1)},
                       {'ends_at':'demain'}, {'revision':True}, {'name':''}, {'message':'x'*301}):
            with self.subTest(change=change), self.assertRaises(PauseError): self.save(**change)
        self.assertFalse(PurchasePause.objects.exists())
        self.assertFalse(PauseConfiguration.objects.exists())

    def test_only_authorized_verified_catalogue_manager_can_change(self):
        customer = get_user_model().objects.create_user('pause-customer')
        staff = get_user_model().objects.create_user('other-staff',is_staff=True)
        unverified = get_user_model().objects.create_user('unverified-manager',is_staff=True,is_superuser=True)
        for user in (None,customer,staff,unverified):
            with self.subTest(user=user), self.assertRaises(PermissionDenied): save_pause(self.data(),user)
        self.assertFalse(PurchasePause.objects.exists())

    def test_guard_enforces_server_clock_and_does_not_modify_orders(self):
        pause=self.save(mode='now')
        with patch('suspensions.services.timezone.now',return_value=self.now), self.assertRaises(PurchasesPaused) as caught:
            assert_purchases_open(lock=True)
        self.assertIn('heure de Paris',str(caught.exception))
        self.assertIn('panier est conservé',str(caught.exception))
        self.assertFalse(Order.objects.exists())
        with patch('suspensions.services.timezone.now',return_value=pause.ends_at):
            self.assertFalse(assert_purchases_open(lock=True)['paused'])

    def test_durable_admission_allows_reconciliation_but_never_a_new_attempt_during_pause(self):
        order=Order.objects.create(session_key='admission',items=[],subtotal=100)
        with transaction.atomic(), patch('suspensions.services.timezone.now',return_value=self.now):
            admission=admit_order(order)
        self.save(mode='now')
        other=Order.objects.create(session_key='blocked',items=[],subtotal=100)
        with transaction.atomic(), patch('suspensions.services.timezone.now',return_value=self.now):
            self.assertEqual(admit_order(order).pk,admission.pk)
            with self.assertRaises(PurchasesPaused): admit_order(other)
        self.assertEqual(PurchaseAdmission.objects.count(),1)
        order.refresh_from_db();self.assertEqual(order.status,'pending')

    def test_journal_failure_rolls_back_period_and_revision(self):
        with patch('suspensions.services.PauseEvent.objects.create',side_effect=RuntimeError('indisponible')):
            with self.assertRaises(RuntimeError):self.save()
        self.assertFalse(PurchasePause.objects.exists());self.assertEqual(configuration_revision(),0)

    def test_dst_boundary_is_an_instant_with_paris_reopening_not_server_local_hour(self):
        self.now = datetime(2027,3,27,12,tzinfo=dt_timezone.utc)
        starts=datetime(2027,3,28,0,30,tzinfo=dt_timezone.utc)
        ends=datetime(2027,3,28,1,30,tzinfo=dt_timezone.utc)
        pause=self.save(starts_at=starts,ends_at=ends)
        with patch('suspensions.services.timezone.now',return_value=starts):
            with self.assertRaises(PurchasesPaused) as error:assert_purchases_open()
        self.assertIn('03:30',str(error.exception))
