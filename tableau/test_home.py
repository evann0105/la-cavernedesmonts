"""The seller landing page preserves role boundaries and dispatch truth."""
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from clients.models import SupportCase
from expeditions.models import Fulfillment, FulfillmentEvent
from paiement.models import Order
from retours.models import ReturnMail, ReturnRequest
from .access import can_access_seller_home


BACKEND = 'connexion.backends.EmailOrUsernameBackend'


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True,
                   EMAIL_DELIVERY_ENABLED=False,
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class SellerHomeTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.seller = users.objects.create_superuser('daily-seller', 'seller@example.test', 'test-only')
        self.catalog = users.objects.create_user('daily-catalog', is_staff=True)
        self.catalog.user_permissions.add(Permission.objects.get(
            content_type__app_label='core', codename='manage_catalog'))
        self.service = users.objects.create_user('daily-service', is_staff=True)
        self.service.user_permissions.add(Permission.objects.get(
            content_type__app_label='retours', codename='manage_returns'))
        self.client.force_login(self.seller, backend=BACKEND)
        self.url = reverse('tableau:home')
        self.tools_url = reverse('tableau:tools')

    def order(self, status='paid', age=0, state=None, **extra):
        at = timezone.now() - timedelta(days=age)
        values = {
            'session_key': 'seller-home-test', 'status': status, 'paid_at': at,
            'customer_email': 'buyer@example.test', 'subtotal': 3500, 'total_paid': 3500,
            'items': [{'product': 1, 'name': 'Polaire de test', 'size': 'M', 'color': 'Rouge',
                       'quantity': 1, 'unit_amount': 3500}],
            'shipping_details': {'name': 'Client fictif', 'address': {
                'line1': '1 rue Exemple', 'city': 'Lyon', 'postal_code': '69000', 'country': 'FR'}},
        }
        values.update(extra)
        order = Order.objects.create(**values)
        if state:
            Fulfillment.objects.create(
                order=order, state=state,
                ready_at=at if state in ('ready', 'shipped') else None,
                shipped_at=at if state == 'shipped' else None,
            )
        return order

    def test_routes_and_private_read_only_pages(self):
        self.assertEqual(self.url, '/gestion/')
        self.assertEqual(self.tools_url, '/gestion/outils/')
        self.assertEqual(reverse('tableau:dashboard'), '/gestion/tableau-de-bord/')
        self.order()
        before = (Fulfillment.objects.count(), FulfillmentEvent.objects.count(), ReturnMail.objects.count())
        for url in (self.url, self.tools_url):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertIn('private', response['Cache-Control'])
            self.assertIn('no-store', response['Cache-Control'])
            self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')
            self.assertEqual(self.client.post(url).status_code, 405)
        self.assertEqual(before, (Fulfillment.objects.count(), FulfillmentEvent.objects.count(), ReturnMail.objects.count()))

    def test_anonymous_customer_and_unprivileged_staff_are_denied(self):
        users = get_user_model()
        for actor in (users.objects.create_user('daily-customer'),
                      users.objects.create_user('daily-staff', is_staff=True)):
            self.client.force_login(actor, backend=BACKEND)
            for url in (self.url, self.tools_url):
                self.assertEqual(self.client.get(url).status_code, 403)
        self.client.logout()
        for url in (self.url, self.tools_url):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn('next=', response['Location'])

    def test_verification_gate_is_preserved_outside_local_bypass(self):
        with override_settings(LOCAL_SKIP_EMAIL_VERIFICATION=False):
            for url in (self.url, self.tools_url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response['Location'], reverse('connexion:verify_email'))

    def test_permissions_do_not_bypass_staff_or_active_requirements(self):
        for field in ('is_staff', 'is_active'):
            actor = get_user_model().objects.get(pk=self.service.pk)
            setattr(actor, field, False)
            self.assertFalse(can_access_seller_home(actor))

    def test_catalog_only_never_loads_service_data(self):
        self.order()
        self.client.force_login(self.catalog, backend=BACKEND)
        with patch('tableau.home.shipment_attention') as shipments, \
             patch('tableau.home.return_attention') as returns, \
             patch('tableau.home.sav_attention') as sav:
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        shipments.assert_not_called()
        returns.assert_not_called()
        sav.assert_not_called()
        self.assertIsNone(response.context['shipment_attention'])
        self.assertIsNone(response.context['return_attention'])
        self.assertIsNone(response.context['sav_attention'])
        self.assertEqual({card['key'] for card in response.context['action_cards']}, {'stock'})
        self.assertNotContains(response, reverse('clients:directory'))
        self.assertNotContains(response, reverse('expeditions:overview'))
        self.assertEqual(self.client.get(self.tools_url).status_code, 200)

    def test_service_only_can_enter_without_catalog_rights(self):
        self.client.force_login(self.service, backend=BACKEND)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['mail_stock_attention']['stock_count'])
        self.assertEqual({card['key'] for card in response.context['action_cards']},
                         {'preparing', 'ready', 'sav', 'returns', 'emails', 'cancellations'})
        self.assertNotContains(response, reverse('core:catalog_list'))
        self.assertNotContains(response, reverse('tableau:dashboard'))
        self.assertEqual(self.client.get(self.tools_url).status_code, 200)

    def test_paid_preparing_and_ready_match_dispatch_queues(self):
        older = self.order(age=5)
        newer = self.order(age=1, state='preparing')
        ready = self.order(age=3, state='ready')
        self.order(age=8, state='shipped')
        self.order(age=10, status='pending')
        self.order(age=11, status='expired')
        self.order(age=12, status='demo')
        response = self.client.get(self.url, {'annee': '2000', 'scope': 'demo'})
        context = response.context['shipment_attention']
        self.assertEqual((context['preparing'], context['ready'], context['total']), (2, 1, 3))
        self.assertEqual([row['order'].pk for row in context['rows']], [older.pk, ready.pk, newer.pk])
        self.assertEqual([row['shipment']['state'] for row in context['rows']], ['preparing', 'ready', 'preparing'])
        for state, key in (('preparing', 'preparing_url'), ('ready', 'ready_url')):
            self.assertEqual(context[key], reverse('expeditions:overview') + '?state=' + state)
            page = self.client.get(context[key])
            self.assertEqual(page.context['state'], state)
            self.assertEqual(len(page.context['rows']), context[state])

    def test_queue_is_bounded_and_legacy_paid_date_falls_back_to_creation(self):
        orders = [self.order(age=age) for age in range(7)]
        legacy = self.order(paid_at=None)
        Order.objects.filter(pk=legacy.pk).update(created_at=timezone.now() - timedelta(days=12))
        context = self.client.get(self.url).context['shipment_attention']
        self.assertEqual(context['total'], 8)
        self.assertEqual(len(context['rows']), 5)
        self.assertEqual(context['rows'][0]['order'].pk, legacy.pk)
        self.assertEqual(context['rows'][1]['order'].pk, orders[-1].pk)

    def test_blocked_ready_order_remains_visible_without_claiming_it_can_ship(self):
        order = self.order(state='ready', shipping_details={})
        context = self.client.get(self.url).context['shipment_attention']
        row = context['rows'][0]
        self.assertEqual(context['ready'], 1)
        self.assertEqual(row['order'].pk, order.pk)
        self.assertTrue(row['shipment']['blocked_reason'])
        self.assertFalse(row['shipment']['can_ship'])
        self.assertEqual(row['action_label'], 'Vérifier la commande')
        self.assertEqual(row['url'], reverse('expeditions:detail', args=[order.pk]))

    def test_existing_return_and_sav_worklists_are_reused_without_double_counting(self):
        order = self.order(age=6)
        record = ReturnRequest.objects.create(order=order, submission_id=uuid.uuid4(), amount=3500)
        demo = self.order(status='demo')
        ReturnRequest.objects.create(order=demo, submission_id=uuid.uuid4(), amount=3500)
        SupportCase.objects.create(creation_key=uuid.uuid4(), customer_name='Test',
            subject='Rappel de test', description='Une demande fictive',
            due_date=timezone.localdate() - timedelta(days=1), priority='urgent')
        response = self.client.get(self.url)
        self.assertEqual(response.context['return_attention']['total'], 1)
        self.assertEqual(response.context['return_attention']['records'][0].pk, record.pk)
        self.assertEqual(response.context['sav_attention']['total'], 1)
        cards = {card['key']: card for card in response.context['action_cards']}
        self.assertEqual(cards['sav']['count'], 1)
        self.assertEqual(cards['returns']['count'], 1)
        self.assertTrue(response.context['has_attention'])

    def test_zero_state_still_links_to_each_available_tool(self):
        response = self.client.get(self.url)
        self.assertFalse(response.context['has_attention'])
        for card in response.context['action_cards']:
            self.assertEqual(card['count'], 0)
            self.assertEqual(self.client.get(card['url']).status_code, 200)

    def test_sav_card_opens_its_exact_union_and_preserves_filter_during_search(self):
        today = timezone.localdate()
        def case(**values):
            return SupportCase.objects.create(creation_key=uuid.uuid4(), customer_name='Client exemple',
                subject='Rappel de test', description='Une demande fictive', **values)
        overdue_urgent = case(due_date=today - timedelta(days=1), priority='urgent')
        due = case(due_date=today)
        urgent = case(due_date=today + timedelta(days=2), priority='urgent')
        case(due_date=today + timedelta(days=2))
        case()
        case(due_date=today, priority='urgent', state='resolved', resolved_at=timezone.now())
        card = next(card for card in self.client.get(self.url).context['action_cards'] if card['key'] == 'sav')
        self.assertEqual(card['count'], 3)
        response = self.client.get(card['url'])
        self.assertTrue(response.context['attention_only'])
        self.assertEqual({item.pk for item in response.context['page_obj']}, {overdue_urgent.pk, due.pk, urgent.pk})
        tab_counts = {tab['value']: tab['count'] for tab in response.context['state_tabs']}
        self.assertEqual((tab_counts['active'], tab_counts['all'], tab_counts['resolved']), (3, 3, 0))
        self.assertContains(response, '<input type="hidden" name="attention" value="1">', html=True)
        self.assertIn('attention=1', response.context['filter_query'])
        filtered = self.client.get(reverse('clients:case_list'), {
            'attention': '1', 'priorite': 'normal', 'echeance': 'today', 'q': 'exemple'})
        self.assertEqual([item.pk for item in filtered.context['page_obj']], [due.pk])
        self.assertEqual(self.client.get(reverse('clients:case_list')).context['page_obj'].paginator.count, 5)

    def test_returns_card_opens_only_uncompleted_paid_cases(self):
        order = self.order()
        first = ReturnRequest.objects.create(order=order, submission_id=uuid.uuid4(), amount=3500)
        received = ReturnRequest.objects.create(order=order, submission_id=uuid.uuid4(), amount=3500, state='received')
        ReturnRequest.objects.create(order=order, submission_id=uuid.uuid4(), amount=3500, state='completed')
        ReturnRequest.objects.create(order=self.order(status='demo'), submission_id=uuid.uuid4(), amount=3500)
        card = next(card for card in self.client.get(self.url).context['action_cards'] if card['key'] == 'returns')
        self.assertEqual(card['count'], 2)
        response = self.client.get(card['url'])
        self.assertEqual(response.context['action_label'], 'Retours en cours')
        self.assertEqual({item.pk for item in response.context['records']}, {first.pk, received.pk})
        self.assertContains(response, 'aria-current="page">En cours</a>')
        filtered = self.client.get(reverse('retours:manage'), {'action': 'open', 'etat': 'received'})
        self.assertEqual([item.pk for item in filtered.context['records']], [received.pk])
