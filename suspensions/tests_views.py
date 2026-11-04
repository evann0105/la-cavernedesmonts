from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from connexion.test_helpers import verify_test_user
from .forms import ParisDateTimeField, PauseForm
from .models import PurchasePause
from . import services

PARIS = ZoneInfo('Europe/Paris')
NOW = datetime(2027, 1, 15, 9, 30, tzinfo=dt_timezone.utc)


class PauseCalendarTests(SimpleTestCase):
    def test_ambiguous_and_missing_paris_hours_are_rejected_with_clear_message(self):
        field = ParisDateTimeField()
        for value in ('2027-03-28T02:30', '2027-10-31T02:30'):
            with self.subTest(value=value), self.assertRaisesMessage(ValidationError, 'Cette heure n’existe pas ou se produit deux fois'):
                field.clean(value)

    def test_values_are_in_paris_even_with_other_active_timezone(self):
        field = ParisDateTimeField()
        with timezone.override(ZoneInfo('America/New_York')):
            result = field.clean('2027-07-15T10:00')
            initial = field.prepare_value(datetime(2027, 7, 15, 8, tzinfo=dt_timezone.utc))
        self.assertEqual(result.astimezone(dt_timezone.utc).hour, 8)
        self.assertEqual(initial.hour, 10)

    def data(self, **overrides):
        return {'revision': '0', 'name': 'Vacances', 'message': 'Une petite pause.', 'mode': 'scheduled',
                'starts_at': '2027-01-20T12:00', 'ends_at': '2027-01-25T12:00', 'confirmed': 'on', **overrides}

    def test_immediate_mode_ignores_unused_invalid_start_and_uses_server_now(self):
        form = PauseForm(self.data(mode='now', starts_at='invalid'), now=NOW)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['starts_at'], NOW)

    def test_reverse_dates_past_end_and_missing_scheduled_start_are_rejected(self):
        for values, field in (({'ends_at': '2027-01-19T10:00'}, 'ends_at'),
                              ({'ends_at': '2027-01-12T10:00'}, 'ends_at'),
                              ({'starts_at': ''}, 'starts_at')):
            form = PauseForm(self.data(**values), now=NOW)
            self.assertFalse(form.is_valid())
            self.assertIn(field, form.errors)

    def test_active_pause_start_cannot_be_changed_by_posted_hidden_values(self):
        start = NOW - timedelta(days=1, seconds=12)
        pause = SimpleNamespace(name='Vacances', message='Pause', starts_at=start, ends_at=NOW+timedelta(days=1))
        form = PauseForm(self.data(mode='now', starts_at='2035-01-01T10:00'), pause=pause, now=NOW)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['starts_at'], start)
        self.assertEqual(form.cleaned_data['mode'], 'scheduled')

    def test_confirmation_and_finite_message_are_required(self):
        form = PauseForm(self.data(confirmed='', message='a'*301), now=NOW)
        self.assertFalse(form.is_valid())
        self.assertIn('confirmed', form.errors)
        self.assertIn('message', form.errors)


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'], EMAIL_DELIVERY_ENABLED=False)
class PauseViewsTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user('gestion-pause', is_staff=True)
        self.actor.user_permissions.add(Permission.objects.get(codename='manage_catalog'))
        verify_test_user(self.actor); self.client.force_login(self.actor)
        self.now = timezone.now()
        self.future = PurchasePause.objects.create(name='Vacances à venir', message='Rendez-vous bientôt.',
            starts_at=self.now+timedelta(days=2), ends_at=self.now+timedelta(days=6), updated_by=self.actor, updated_by_label=self.actor.username)
        self.active = PurchasePause.objects.create(name='Pause en cours', message='Pause du moment.',
            starts_at=self.now-timedelta(days=1), ends_at=self.now+timedelta(days=1), updated_by_label=self.actor.username)
        self.past = PurchasePause.objects.create(name='Ancienne pause', message='Ancien message.',
            starts_at=self.now-timedelta(days=10), ends_at=self.now-timedelta(days=8))
        self.cancelled = PurchasePause.objects.create(name='Pause annulée', message='Message annulé.',
            starts_at=self.now+timedelta(days=12), ends_at=self.now+timedelta(days=15),
            stopped_at=self.now, stop_kind='cancel')

    def url(self, name, pause=None, **kwargs):
        if name in ('create', 'overview'):
            return reverse('suspensions:'+name)
        return reverse('suspensions:'+name, kwargs={'pk': (pause or self.future).pk, **kwargs})

    def form_data(self, **changes):
        return {'name': 'Nouvelle pause', 'message': 'Mon message personnel', 'mode': 'scheduled',
            'starts_at': (self.now+timedelta(days=20)).astimezone(PARIS).strftime('%Y-%m-%dT%H:%M'),
            'ends_at': (self.now+timedelta(days=23)).astimezone(PARIS).strftime('%Y-%m-%dT%H:%M'),
            'revision': '0', 'confirmed': 'on', **changes}

    def test_management_urls_have_one_prefix(self):
        self.assertEqual(self.url('overview'), '/gestion/boutique/pause-commandes/')

    def test_permissions_cover_lists_forms_and_confirmation(self):
        urls = [self.url('overview'), self.url('create'), self.url('edit'), self.url('action', action='cancel')]
        self.client.logout()
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 302)
        user = get_user_model().objects.create_user('staff-sans-catalogue', is_staff=True)
        verify_test_user(user); self.client.force_login(user)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_gets_never_pause_resume_or_cancel_and_are_private(self):
        with patch('suspensions.views.services.save_pause') as save, patch('suspensions.views.services.cancel_pause') as cancel, patch('suspensions.views.services.resume_pause') as resume:
            for url in (self.url('overview'), self.url('create'), self.url('edit'), self.url('action', action='cancel'), self.url('action', self.active, action='resume')):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200, url)
                self.assertIn('no-store', response['Cache-Control'])
                self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')
            save.assert_not_called(); cancel.assert_not_called(); resume.assert_not_called()
        self.assertEqual(PurchasePause.objects.count(), 4)

    def test_status_tabs_show_only_the_requested_periods(self):
        for key, item in [('active', self.active), ('upcoming', self.future), ('past', self.past), ('cancelled', self.cancelled)]:
            response = self.client.get(self.url('overview'), {'state': key})
            self.assertEqual([pause.pk for pause in response.context['page_obj']], [item.pk])
            self.assertEqual({tab['key']: tab['count'] for tab in response.context['tabs']}, {'active': 1,'upcoming': 1,'past': 1,'cancelled': 1})

    def test_immediate_link_selects_mode_but_still_requires_confirmation(self):
        response = self.client.get(self.url('create'), {'mode': 'now'})
        self.assertEqual(response.context['form']['mode'].value(), 'now')
        with patch('suspensions.views.services.save_pause') as save:
            response = self.client.post(self.url('create'), self.form_data(mode='now', confirmed=''))
        self.assertEqual(response.status_code, 400)
        save.assert_not_called()

    def test_valid_form_sends_global_revision_and_aware_dates(self):
        with patch('suspensions.views.services.save_pause', return_value=self.future) as save:
            response = self.client.post(self.url('create'), self.form_data(revision='42'))
        self.assertEqual(response.status_code, 302)
        sent, actor = save.call_args.args
        self.assertEqual(sent['revision'], 42)
        self.assertEqual(actor, self.actor)
        self.assertTrue(timezone.is_aware(sent['starts_at']))
        self.assertTrue(timezone.is_aware(sent['ends_at']))
        self.assertIsNone(save.call_args.kwargs['pk'])

    def test_conflict_preserves_text_and_dates_with_fresh_revision_and_unchecked_confirmation(self):
        with patch('suspensions.views.services.save_pause', side_effect=services.PauseConflict('Une autre pause a été modifiée.')), patch('suspensions.views.services.configuration_revision', return_value=17):
            data = self.form_data()
            response = self.client.post(self.url('edit'), data)
        self.assertEqual(response.status_code, 409)
        form = response.context['form']
        for name in ('name', 'message', 'starts_at', 'ends_at'):
            self.assertEqual(form[name].value(), data[name])
        self.assertEqual(str(form['revision'].value()), '17')
        self.assertFalse(form['confirmed'].value())
        self.assertContains(response, 'Message actuellement enregistré', status_code=409)
        self.assertContains(response, self.future.message, status_code=409)

    def test_overlap_error_keeps_customer_text_and_does_not_redirect(self):
        with patch('suspensions.views.services.save_pause', side_effect=services.PauseError('Les dates chevauchent une autre pause.')):
            response = self.client.post(self.url('create'), self.form_data())
        self.assertContains(response, 'Les dates chevauchent une autre pause.', status_code=400)
        self.assertEqual(response.context['form']['message'].value(), 'Mon message personnel')

    def test_ended_and_cancelled_periods_are_read_only(self):
        for pause in (self.past, self.cancelled):
            with self.subTest(pause=pause.pk), patch('suspensions.views.services.save_pause') as save:
                response = self.client.get(self.url('edit', pause))
                self.assertContains(response, 'Cette pause est conservée dans votre historique.')
                self.assertNotContains(response, 'data-pause-submit')
                response = self.client.post(self.url('edit', pause), self.form_data(message='Texte à conserver'))
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.context['form']['message'].value(), 'Texte à conserver')
                save.assert_not_called()

    def test_action_requires_confirmation_and_correct_current_state(self):
        with patch('suspensions.views.services.cancel_pause') as cancel, patch('suspensions.views.services.resume_pause') as resume:
            self.assertEqual(self.client.post(self.url('action', action='cancel'), {'revision': 0}).status_code, 400)
            self.assertEqual(self.client.post(self.url('action', self.active, action='cancel'), {'revision': 0, 'confirmed': 'on'}).status_code, 409)
            self.assertEqual(self.client.post(self.url('action', self.future, action='resume'), {'revision': 0, 'confirmed': 'on'}).status_code, 409)
            cancel.assert_not_called(); resume.assert_not_called()

    def test_valid_actions_dispatch_correct_service(self):
        for action, pause in [('cancel', self.future), ('resume', self.active)]:
            with self.subTest(action=action), patch('suspensions.views.services.' + action + '_pause') as service:
                response = self.client.post(self.url('action', pause, action=action), {'revision': '7', 'confirmed': 'on'})
                self.assertEqual(response.status_code, 302)
                service.assert_called_once_with(pause.pk, 7, self.actor)

    def test_action_conflict_requires_fresh_confirmation(self):
        with patch('suspensions.views.services.cancel_pause', side_effect=services.PauseConflict('La période a changé.')), patch('suspensions.views.services.configuration_revision', return_value=19):
            response = self.client.post(self.url('action', action='cancel'), {'revision': '2', 'confirmed': 'on'})
        self.assertContains(response, 'Les informations ci-dessous ont été actualisées.', status_code=409)
        self.assertEqual(response.context['form']['revision'].value(), 19)
        self.assertFalse(response.context['form']['confirmed'].value())

    def test_csrf_rejects_create_and_actions(self):
        client = Client(enforce_csrf_checks=True); client.force_login(self.actor)
        with patch('suspensions.views.services.save_pause') as save, patch('suspensions.views.services.cancel_pause') as cancel:
            self.assertEqual(client.post(self.url('create'), self.form_data()).status_code, 403)
            self.assertEqual(client.post(self.url('action', action='cancel'), {'revision': '0','confirmed': 'on'}).status_code, 403)
            save.assert_not_called(); cancel.assert_not_called()

    def test_unknown_action_and_giant_identifiers_are_not_found(self):
        self.assertEqual(self.client.get(self.url('action', action='delete')).status_code, 404)
        self.assertEqual(self.client.get(reverse('suspensions:edit', args=[10**30])).status_code, 404)

    def test_explanation_covers_existing_payments_and_orders_and_preview_is_escaped(self):
        response = self.client.post(self.url('create'), self.form_data(message='<script>alert(1)</script>', confirmed=''))
        self.assertContains(response, 'Les commandes déjà payées restent à préparer.', status_code=400)
        self.assertContains(response, 'Les paiements déjà ouverts peuvent encore être confirmés.', status_code=400)
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;', status_code=400)
        self.assertNotContains(response, '<script>alert(1)</script>', status_code=400)

    def test_real_create_edit_cancel_keeps_audit_and_uses_latest_global_revision(self):
        response = self.client.post(self.url('create'), self.form_data())
        self.assertEqual(response.status_code, 302)
        saved = PurchasePause.objects.get(name='Nouvelle pause')
        self.assertEqual(saved.events.get().actor_label, self.actor.username)
        data = self.form_data(message='Texte mis à jour.', revision=str(services.configuration_revision()))
        response = self.client.post(self.url('edit', saved), data)
        self.assertEqual(response.status_code, 302)
        saved.refresh_from_db()
        self.assertEqual(saved.message, 'Texte mis à jour.')
        response = self.client.post(self.url('action', saved, action='cancel'), {'revision': services.configuration_revision(), 'confirmed': 'on'})
        self.assertEqual(response.status_code, 302)
        saved.refresh_from_db()
        self.assertEqual(saved.status, 'cancelled')
        self.assertEqual(list(saved.events.order_by('pk').values_list('kind', flat=True)), ['create', 'edit', 'cancel'])
        detail = self.client.get(self.url('edit', saved))
        self.assertContains(detail, 'Historique de cette pause')
        self.assertContains(detail, self.actor.username)

    def test_resume_uses_past_tab_and_preserves_original_end(self):
        original_end = self.active.ends_at
        response = self.client.post(self.url('action', self.active, action='resume'), {'revision': services.configuration_revision(), 'confirmed': 'on'})
        self.assertEqual(response.status_code, 302)
        self.active.refresh_from_db()
        self.assertEqual(self.active.ends_at, original_end)
        response = self.client.get(self.url('overview'), {'state': 'past'})
        self.assertIn(self.active.pk, [pause.pk for pause in response.context['page_obj']])
        self.assertContains(response, 'Reprise anticipée')
        self.assertContains(response, 'Initialement prévue')

    def test_contiguous_pauses_show_the_effective_global_resume_date(self):
        self.future.starts_at = self.active.ends_at
        self.future.save(update_fields=['starts_at'])
        response = self.client.get(self.url('overview'))
        self.assertEqual(response.context['resumes_at'], self.future.ends_at)
        self.assertContains(response, 'Si deux pauses se suivent sans intervalle')
