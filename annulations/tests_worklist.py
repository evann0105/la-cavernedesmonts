"""Follow-up lists stay private, actionable and completely read-only."""
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from connexion.test_helpers import verify_test_user
from expeditions.models import Fulfillment, FulfillmentEvent
from paiement.models import Order
from retours.models import ReturnEvent, ReturnMail, ReturnRequest
from .models import Cancellation
from .worklist import dashboard_attention


BACKEND = 'connexion.backends.EmailOrUsernameBackend'


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=False,
                   EMAIL_DELIVERY_ENABLED=False, EMAIL_TEST_MODE=False,
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class CancellationWorklistTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.seller = users.objects.create_user('cancellation-service', email='service@example.test', is_staff=True)
        self.seller.user_permissions.add(Permission.objects.get(
            content_type__app_label='retours', codename='manage_returns'))
        self.catalog = users.objects.create_user('cancellation-catalog', email='catalog@example.test', is_staff=True)
        self.catalog.user_permissions.add(Permission.objects.get(
            content_type__app_label='core', codename='manage_catalog'))
        self.customer = users.objects.create_user('cancellation-customer', email='customer@example.test')
        for user in (self.seller, self.catalog, self.customer):
            verify_test_user(user)
        self.client.force_login(self.seller, backend=BACKEND)
        self.url = reverse('annulations:overview')
        self.home = reverse('tableau:home')

    def record(self, state='pending', *, status='paid', stock_completed=False, age=0,
               refund_error='', stock_error='', order_id=None):
        now = timezone.now()
        order = Order.objects.create(id=order_id or uuid.uuid4(), user=self.customer,
            session_key='cancellation-worklist', status=status, customer_email='client@example.test',
            subtotal=5000, total_paid=5600, return_shipping_amount=600,
            paid_at=now, paid_line_totals=[5000], items=[{'product': 1, 'name': 'Article test',
                'size': 'M', 'color': 'Vert', 'quantity': 1, 'unit_amount': 5000}],
            shipping_details={'name': 'Client exemple'})
        Fulfillment.objects.create(order=order, state='cancelled')
        refund = ReturnRequest.objects.create(order=order, submission_id=uuid.uuid4(),
            kind='cancellation', amount=5600, shipping_amount=600, refund_state=state)
        record = Cancellation.objects.create(order=order, refund=refund,
            reason='Motif public de test', requested_by=self.seller,
            stock_completed_at=now if stock_completed else None,
            refund_error=refund_error, stock_error=stock_error)
        if age:
            Cancellation.objects.filter(pk=record.pk).update(created_at=now - timedelta(days=age))
            record.refresh_from_db()
        return record

    def ids(self, response):
        return [row['record'].pk for row in response.context['rows']]

    def test_private_get_route_rejects_post(self):
        self.assertEqual(self.url, '/gestion/annulations/')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('private', response['Cache-Control'])
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')
        self.assertFalse(response.context['back_use_history'])
        self.assertEqual(response.context['back_url'], self.home)
        self.assertEqual(self.client.post(self.url).status_code, 405)

    def test_customer_and_catalog_only_cannot_read_list(self):
        for actor in (self.customer, self.catalog):
            self.client.force_login(actor, backend=BACKEND)
            with self.subTest(actor=actor.username):
                self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('next=', response.url)

    def test_unverified_manager_keeps_confirmation_gate(self):
        actor = get_user_model().objects.create_user('unverified-cancellation', email='unverified@example.test', is_staff=True)
        actor.user_permissions.add(Permission.objects.get(content_type__app_label='retours', codename='manage_returns'))
        self.client.force_login(actor, backend=BACKEND)
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse('connexion:verify_email'), fetch_redirect_response=False)

    def test_attention_union_requires_both_refund_and_stock_to_be_complete(self):
        pending = self.record()
        no_refund = self.record('none', stock_completed=True)
        stock = self.record('succeeded')
        finished = self.record('succeeded', stock_completed=True)
        self.record(status='demo')
        self.record(status='pending')
        self.record(status='expired')
        response = self.client.get(self.url)
        self.assertEqual(set(self.ids(response)), {pending.pk, no_refund.pk, stock.pk})
        self.assertNotIn(finished.pk, self.ids(response))
        counts = {tab['key']: tab['count'] for tab in response.context['tabs']}
        self.assertEqual(counts, {'attention': 3, 'pending': 1, 'refund': 1, 'stock': 1, 'completed': 1, 'all': 4})

    def test_filters_match_labels_without_overlap(self):
        waiting = self.record('pending')
        problem = self.record('pending', refund_error='Confirmation à vérifier')
        failed = self.record('failed')
        reviewing = self.record('review')
        uncertain = self.record('submitting')
        not_started = self.record('none')
        stock = self.record('succeeded', stock_error='Variante à vérifier')
        done = self.record('succeeded', stock_completed=True)
        expected = {'pending': {waiting.pk}, 'refund': {problem.pk, failed.pk, reviewing.pk, uncertain.pk, not_started.pk},
                    'stock': {stock.pk}, 'completed': {done.pk},
                    'all': {waiting.pk, problem.pk, failed.pk, reviewing.pk, uncertain.pk, not_started.pk, stock.pk, done.pk}}
        for state, ids in expected.items():
            with self.subTest(state=state):
                response = self.client.get(self.url, {'state': state})
                self.assertEqual(set(self.ids(response)), ids)
                self.assertContains(response, 'aria-current="page"')
        invalid = self.client.get(self.url, {'state': 'unknown'})
        self.assertEqual(invalid.context['state'], 'attention')
        self.assertNotIn(done.pk, self.ids(invalid))

    def test_problems_before_waiting_then_oldest_first(self):
        pending_old = self.record(age=30)
        pending_recent = self.record(age=2)
        failed_old = self.record('failed', age=7)
        stock = self.record('succeeded', age=5)
        failed_recent = self.record('review', age=1)
        self.assertEqual(self.ids(self.client.get(self.url)),
                         [failed_old.pk, stock.pk, failed_recent.pk, pending_old.pk, pending_recent.pk])
        context = dashboard_attention()
        self.assertEqual([row['record'].pk for row in context['rows']],
                         [failed_old.pk, stock.pk, failed_recent.pk, pending_old.pk, pending_recent.pk])

    def test_row_actions_are_links_and_describe_the_exact_followup(self):
        records = [self.record('none'), self.record('pending'), self.record('failed'), self.record('succeeded')]
        response = self.client.get(self.url)
        rows = {row['record'].pk: row for row in response.context['rows']}
        for record, action in zip(records, ('Reprendre le remboursement', 'Suivre le remboursement',
                                           'Vérifier le remboursement', 'Vérifier la remise en stock')):
            self.assertEqual(rows[record.pk]['action'], action)
            self.assertEqual(rows[record.pk]['url'], reverse('annulations:detail', args=[record.order_id]))
            self.assertContains(response, action)
        self.assertNotContains(response, 'name="action"')
        self.assertContains(response, '56,00 €')

    def test_scope_is_paid_by_default_and_demo_is_explicit_only_in_debug(self):
        real = self.record()
        demo = self.record(status='demo')
        self.assertEqual(self.ids(self.client.get(self.url)), [real.pk])
        response = self.client.get(self.url, {'scope': 'demo'})
        self.assertEqual(self.ids(response), [demo.pk])
        self.assertContains(response, 'Commandes de démonstration')
        self.assertContains(response, 'Montant simulé')
        with override_settings(DEBUG=False):
            response = self.client.get(self.url, {'scope': 'demo'})
            self.assertEqual(self.ids(response), [real.pk])
            self.assertEqual(response.context['scope'], 'paid')
            self.assertNotContains(response, 'scope=demo')
            self.assertEqual(dashboard_attention()['total'], 1)

    def test_reference_search_and_pagination_keep_scope_and_filter(self):
        records = [self.record(status='demo', order_id=uuid.UUID(f'aaaaaaaa-0000-0000-0000-{n:012d}')) for n in range(26)]
        self.record(status='demo')
        response = self.client.get(self.url, {'scope': 'demo', 'state': 'pending', 'q': 'AAAAAAAA', 'page': 2})
        self.assertEqual(response.context['page_obj'].paginator.count, 26)
        self.assertEqual(len(response.context['rows']), 1)
        self.assertIn(self.ids(response)[0], [r.pk for r in records])
        self.assertContains(response, 'scope=demo&amp;state=pending&amp;q=AAAAAAAA&amp;page=1')
        self.assertTrue(all('scope=demo' in tab['url'] and 'q=AAAAAAAA' in tab['url'] for tab in response.context['tabs']))
        searched = self.client.get(self.url, {'scope': 'demo', 'q': str(records[0].order_id)})
        self.assertEqual(self.ids(searched), [records[0].pk])
        invalid = self.client.get(self.url, {'q': '<script>client@example.test</script>'})
        self.assertEqual(invalid.context['page_obj'].paginator.count, 0)
        self.assertNotContains(invalid, '<script>client@example.test</script>')

    def test_completed_filter_keeps_newest_first(self):
        older = self.record('succeeded', stock_completed=True, age=20)
        newer = self.record('succeeded', stock_completed=True, age=1)
        response = self.client.get(self.url, {'state': 'completed'})
        self.assertEqual(self.ids(response), [newer.pk, older.pk])
        self.assertContains(response, 'Voir le récapitulatif')
        self.assertNotIn('Reprendre le remboursement', response.content.decode().split('<div class="cancellation-worklist">', 1)[1])

    def test_home_shows_five_prioritized_rows_and_matching_full_list(self):
        waiting = [self.record(age=age) for age in range(7)]
        failed = self.record('failed')
        self.record('succeeded', stock_completed=True)
        self.record(status='demo')
        response = self.client.get(self.home, {'scope': 'demo'})
        context = response.context['cancellation_attention']
        self.assertEqual(context['total'], 8)
        self.assertEqual(len(context['rows']), 5)
        self.assertEqual(context['rows'][0]['record'].pk, failed.pk)
        self.assertEqual(context['rows'][1]['record'].pk, waiting[-1].pk)
        card = next(c for c in response.context['action_cards'] if c['key'] == 'cancellations')
        self.assertEqual(card['count'], 8)
        self.assertEqual(card['url'], self.url)
        self.assertContains(response, 'Voir les 8 annulations à suivre')
        self.assertEqual(self.client.get(card['url']).context['page_obj'].paginator.count, 8)
        self.assertEqual(response.context['shipment_attention']['total'], 0)
        self.assertEqual(response.context['return_attention']['total'], 0)

    def test_home_catalog_role_never_queries_or_displays_cancellations(self):
        self.record()
        self.client.force_login(self.catalog, backend=BACKEND)
        with patch('tableau.home.cancellation_attention') as attention:
            response = self.client.get(self.home)
        attention.assert_not_called()
        self.assertIsNone(response.context['cancellation_attention'])
        self.assertNotIn('cancellations', [card['key'] for card in response.context['action_cards']])
        self.assertNotContains(response, 'id="cancellations-title"')

    def test_get_never_calls_stripe_or_changes_any_followup_record(self):
        self.record('none')
        self.record('pending')
        self.record('failed')
        self.record('succeeded')
        models = (Order, Cancellation, ReturnRequest, Fulfillment, FulfillmentEvent, ReturnEvent, ReturnMail)
        before = [list(model.objects.order_by('pk').values()) for model in models]
        with patch('annulations.services.request_cancellation') as cancel, \
             patch('annulations.services.reconcile_cancellation') as reconcile, \
             patch('annulations.services.retry_stock') as restock, \
             patch('stripe.Refund.create') as create_refund, \
             patch('stripe.Refund.retrieve') as retrieve_refund:
            for url in (self.url, self.home):
                self.assertEqual(self.client.get(url).status_code, 200)
            for function in (cancel, reconcile, restock, create_refund, retrieve_refund):
                function.assert_not_called()
        self.assertEqual(before, [list(model.objects.order_by('pk').values()) for model in models])
