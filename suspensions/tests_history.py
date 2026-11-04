from datetime import datetime, timedelta, timezone as datetime_timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from .history import event_details, event_rows, history_context
from .models import PauseEvent, PurchasePause


NOW = datetime(2027, 1, 10, 9, 12, 34, tzinfo=datetime_timezone.utc)


def snapshot(**changes):
    values = dict(name='Vacances de février', message='Les commandes reprendront bientôt.',
        starts_at=(NOW + timedelta(days=1)).replace(second=0).isoformat(),
        ends_at=(NOW + timedelta(days=8)).replace(second=0).isoformat(),
        stop_kind='', stopped_at=None, revision=1)
    values.update(changes)
    return values


def event(**changes):
    values = dict(pk=1, kind='edit', before=snapshot(), after=snapshot(),
                  created_at=NOW, actor_label='Élodie')
    values.update(changes)
    return SimpleNamespace(**values)


class PauseHistoryPresentationTests(SimpleTestCase):
    def test_creation_shows_initial_settings_without_empty_stop_or_revision_noise(self):
        details = event_details(event(kind='create', before={}, after=snapshot()))
        self.assertEqual(details['label'], 'Création de la pause')
        self.assertEqual([row['key'] for row in details['rows']], ['name', 'message', 'starts_at', 'ends_at'])
        self.assertTrue(all(row['before']['text'] == '—' for row in details['rows']))
        self.assertEqual(details['rows'][0]['after']['text'], 'Vacances de février')
        self.assertFalse(details['incomplete'])

    def test_edit_only_shows_changed_business_fields_and_ignores_metadata(self):
        before = snapshot()
        after = {**before, 'name': 'Pause de février', 'message': 'Nouveau message\nÀ bientôt.',
                 'revision': 2, 'updated_at': 'private timestamp', 'secret': 'must-not-be-displayed'}
        details = event_details(event(before=before, after=after))
        self.assertEqual([row['key'] for row in details['rows']], ['name', 'message'])
        self.assertEqual(details['rows'][0]['before']['text'], before['name'])
        self.assertEqual(details['rows'][0]['after']['text'], after['name'])
        self.assertEqual(details['rows'][1]['after']['text'], after['message'])
        self.assertNotIn('must-not-be-displayed', str(details))
        self.assertNotIn('private timestamp', str(details))

    def test_same_instant_with_a_different_iso_offset_is_not_a_changed_date(self):
        before = snapshot()
        after = {**before, 'starts_at': datetime.fromisoformat(before['starts_at']).astimezone(ZoneInfo('Europe/Paris')).isoformat(), 'revision': 2}
        details = event_details(event(before=before, after=after))
        self.assertEqual(details['rows'], [])
        self.assertFalse(details['incomplete'])
        rendered = render_to_string('suspensions/_history.html', {'pause_history': [details]})
        self.assertIn('Aucune modification détaillée', rendered)
        self.assertNotIn('Avant</span>', rendered)

    def test_paris_dates_are_independent_of_the_active_timezone_and_language(self):
        from django.utils.translation import override
        with timezone.override(ZoneInfo('America/New_York')), override('en'):
            details = event_details(event(kind='create', before={}))
        self.assertEqual(details['created']['text'], '10/01/2027 à 10:12:34')
        self.assertEqual(details['created']['title'], 'Heure de Paris · UTC+01:00')
        self.assertEqual(details['rows'][2]['after']['text'], '11/01/2027 à 10:12')
        self.assertTrue(details['created']['iso'].endswith('+01:00'))

    def test_repeated_autumn_hour_distinguishes_two_real_instants_visibly(self):
        before = snapshot()
        before['starts_at'] = '2026-10-25T00:30:00+00:00'
        after = {**before, 'starts_at': '2026-10-25T01:30:00+00:00'}
        rows = event_details(event(before=before, after=after))['rows']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['before']['text'], '25/10/2026 à 02:30 (UTC+02:00)')
        self.assertEqual(rows[0]['after']['text'], '25/10/2026 à 02:30 (UTC+01:00)')
        self.assertNotEqual(rows[0]['before']['iso'], rows[0]['after']['iso'])

    def test_spring_clock_change_converts_instants_without_inventing_a_nonexistent_hour(self):
        before = snapshot()
        before['starts_at'] = '2027-03-28T00:30:00+00:00'
        after = {**before, 'starts_at': '2027-03-28T01:30:00+00:00'}
        row = event_details(event(before=before, after=after))['rows'][0]
        self.assertEqual(row['before']['text'], '28/03/2027 à 01:30')
        self.assertEqual(row['after']['text'], '28/03/2027 à 03:30')
        self.assertEqual(row['after']['title'], 'Heure de Paris · UTC+02:00')

    def test_cancel_and_resume_show_the_decision_and_exact_stop_instant(self):
        for kind, label, date_label in (
                ('cancel', 'Pause programmée annulée', 'Date de l’annulation'),
                ('resume', 'Pause terminée plus tôt', 'Date de reprise anticipée')):
            with self.subTest(kind=kind):
                before = snapshot()
                after = {**before, 'stop_kind': kind, 'stopped_at': NOW.isoformat(), 'revision': 2}
                details = event_details(event(kind=kind, before=before, after=after))
                self.assertEqual([row['key'] for row in details['rows']], ['stop_kind', 'stopped_at'])
                self.assertEqual(details['rows'][0]['after']['text'], label)
                self.assertEqual(details['rows'][1]['label'], date_label)
                self.assertEqual(details['rows'][1]['before']['text'], 'Aucune')
                self.assertEqual(details['rows'][1]['after']['text'], '10/01/2027 à 10:12:34')

    def test_seconds_are_kept_when_the_pause_starts_immediately(self):
        after = snapshot()
        after['starts_at'] = NOW.isoformat()
        details = event_details(event(kind='create', before={}, after=after))
        self.assertEqual(details['rows'][2]['after']['text'], '10/01/2027 à 10:12:34')

    def test_missing_and_malformed_old_snapshots_never_crash_or_invent_previous_values(self):
        for old in (None, [], 'old text', 17, True):
            with self.subTest(old=old):
                details = event_details(event(before=old, after=snapshot()))
                self.assertTrue(details['incomplete'])
                self.assertTrue(all(row['before']['text'] == 'Valeur non conservée' for row in details['rows']))
                self.assertIn('Certaines valeurs', render_to_string('suspensions/_history.html', {'pause_history': [details]}))
        details = event_details(event(before=None, after=None, created_at=None, actor_label=None))
        self.assertTrue(details['incomplete'])
        self.assertEqual(details['rows'], [])
        self.assertEqual(details['actor_label'], 'Auteur non conservé')
        self.assertFalse(details['created']['iso'])

    def test_invalid_dates_and_naive_legacy_hours_are_not_guessed(self):
        for value in ('2027-13-50T12:00:00+00:00', '2027-01-10T10:12:34', 'not-a-date',
                      {'html': '<script>alert(1)</script>'}, [], 3):
            with self.subTest(value=value):
                after = snapshot()
                after['ends_at'] = value
                details = event_details(event(after=after))
                self.assertTrue(details['incomplete'])
                self.assertFalse(details['rows'][0]['after']['iso'])
                self.assertEqual(details['rows'][0]['after']['text'], 'Date non conservée ou illisible')

    def test_null_text_is_explicitly_empty_and_unknown_decision_does_not_echo_data(self):
        after = snapshot()
        after.update(message=None, stop_kind='<script>alert(1)</script>')
        details = event_details(event(after=after))
        self.assertEqual(details['rows'][0]['after']['text'], 'Non renseigné')
        self.assertEqual(details['rows'][1]['after']['text'], 'Décision ancienne non reconnue')
        self.assertNotIn('<script>', str(details))

    def test_all_names_messages_and_actor_labels_are_html_escaped(self):
        payload = '<img src=x onerror="alert(1)"></details><script>alert(2)</script>'
        before = snapshot()
        before['message'] = payload
        after = {**before, 'name': payload, 'message': payload + '\nNouveau'}
        details = event_details(event(before=before, after=after, actor_label=payload))
        html = render_to_string('suspensions/_history.html', {'pause_history': [details]})
        self.assertIn('&lt;img', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>', html)
        self.assertNotIn('<img ', html)
        self.assertNotIn('onerror="', html)

    def test_list_order_is_stable_for_equal_event_timestamps(self):
        events = [event(pk=1), event(pk=3), event(pk=2, created_at=NOW + timedelta(seconds=1))]
        self.assertEqual([row['id'] for row in event_rows(events)], [2, 3, 1])


class PauseHistoryPaginationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.pause = PurchasePause.objects.create(name='Pause actuelle', message='Message actuel',
            starts_at=NOW, ends_at=NOW + timedelta(days=1))
        cls.events = PauseEvent.objects.bulk_create([
            PauseEvent(pause=cls.pause, kind='edit', actor_label='Ancienne vendeuse',
                       before=snapshot(), after={**snapshot(), 'name': f'Nom historique {index}'})
            for index in range(26)
        ])
        PauseEvent.objects.filter(pause=cls.pause).update(created_at=NOW)

    def test_queryset_history_is_complete_and_paginates_with_a_stable_tie_order(self):
        seen = []
        for page in (1, 2, 3):
            context = history_context(self.pause.events.all(), page)
            seen.extend(row['id'] for row in context['pause_history'])
            self.assertEqual(context['pause_history_page'].paginator.count, 26)
        self.assertEqual(seen, sorted([row.pk for row in self.events], reverse=True))
        self.assertEqual(len(set(seen)), 26)

    def test_pagination_links_include_the_history_anchor_and_never_drop_older_events(self):
        context = history_context(self.pause.events.all(), 2)
        html = render_to_string('suspensions/_history.html', context)
        self.assertIn('?history_page=1#pause-history', html)
        self.assertIn('?history_page=3#pause-history', html)
        self.assertIn('Page 2 sur 3', html)
        self.assertIn('26 événements', html)
        self.assertIn('Nom historique', html)
        self.assertNotIn('Pause actuelle', html)

    def test_invalid_page_is_safe_and_last_page_remains_available(self):
        self.assertEqual(history_context(self.pause.events.all(), 'bad')['pause_history_page'].number, 1)
        context = history_context(self.pause.events.all(), 999999)
        self.assertEqual(context['pause_history_page'].number, 3)
        self.assertEqual(len(context['pause_history']), 2)

    def test_loading_one_page_needs_only_count_and_page_queries(self):
        with self.assertNumQueries(2):
            context = history_context(self.pause.events.all(), 1)
        self.assertEqual(len(context['pause_history']), 12)

    def test_presentation_does_not_update_or_replace_frozen_events(self):
        before = list(self.pause.events.values('pk', 'before', 'after', 'actor_label', 'created_at'))
        html = render_to_string('suspensions/_history.html', history_context(self.pause.events.all(), 2))
        self.assertIn('Ancienne vendeuse', html)
        self.assertEqual(list(self.pause.events.values('pk', 'before', 'after', 'actor_label', 'created_at')), before)
