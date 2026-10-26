"""Seller-facing customer/SAV workflows, exercised through their HTTP boundary."""
import uuid
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from avoirs.models import CustomerBenefit
from connexion.test_helpers import verify_test_user
from espace.models import CreditNote, Voucher
from facturation.models import Invoice
from paiement.models import Order
from retours.models import ReturnRequest
from .models import SupportCase, SupportEvent


@override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    LOCAL_SKIP_EMAIL_VERIFICATION=False,
)
class CustomerWorkspaceViewsTests(TestCase):
    def setUp(self):
        self.manager = self.user('seller', 'seller@example.test', is_staff=True)
        self.manager.user_permissions.add(Permission.objects.get(codename='manage_returns'))
        verify_test_user(self.manager)
        self.login(self.manager)

    def user(self, username, email, **extra):
        return get_user_model().objects.create_user(username=username, email=email, **extra)

    def login(self, user, client=None):
        (client or self.client).force_login(user, backend='connexion.backends.EmailOrUsernameBackend')

    def order(self, *, user=None, email='guest@example.test', name='Camille Montagne', **extra):
        values = {'session_key': 'customer-workspace-test', 'items': [], 'subtotal': 8000,
                  'customer_email': email, 'user': user, 'status': 'paid',
                  'billing_details': {'name': name, 'phone': '04 50 00 00 00'},
                  'shipping_details': {}, 'total_paid': 7200, 'paid_at': timezone.now()}
        values.update(extra)
        return Order.objects.create(**values)

    def case(self, *, user=None, order=None, email='guest@example.test', name='Camille Montagne', **extra):
        values = {'creation_key': uuid.uuid4(), 'creation_fingerprint': 'a' * 64,
                  'customer_name': name, 'customer_email': email, 'phone': '06 00 00 00 00',
                  'subject': 'Une demande par téléphone', 'description': 'Rappeler le client.',
                  'user': user, 'order': order}
        values.update(extra)
        return SupportCase.objects.create(**values)

    def create_data(self, **extra):
        data = {'creation_key': str(uuid.uuid4()), 'customer_name': 'Camille Montagne',
                'customer_email': 'guest@example.test', 'phone': '06 00 00 00 00',
                'subject': 'Vérifier le délai de livraison', 'description': 'Le client souhaite être rappelé.',
                'channel': 'phone', 'category': 'delivery', 'priority': 'normal', 'due_date': ''}
        data.update(extra)
        return data

    def update_data(self, case, **extra):
        data = {'revision': case.revision, 'state': case.state, 'priority': case.priority,
                'due_date': case.due_date.isoformat() if case.due_date else '', 'note': ''}
        data.update(extra)
        return data

    def urls(self, user, case):
        return [reverse('clients:directory'), reverse('clients:case_list'),
                reverse('clients:profile', args=[f'u-{user.pk}']), reverse('clients:case_create'),
                reverse('clients:case_detail', args=[case.pk])]

    def test_all_screens_require_an_authenticated_returns_manager(self):
        customer = self.user('customer', 'customer@example.test')
        staff = self.user('staff', 'staff@example.test', is_staff=True)
        case = self.case(user=customer, email=customer.email)
        urls = self.urls(customer, case)
        self.client.logout()
        for url in urls:
            with self.subTest(actor='anonymous', url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn('next=', response.url)
        for actor in (customer, staff):
            self.login(actor)
            for url in urls:
                with self.subTest(actor=actor.username, url=url):
                    self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(reverse('clients:case_create'), self.create_data()).status_code, 403)
            self.assertEqual(self.client.post(reverse('clients:case_detail', args=[case.pk]),
                                             self.update_data(case, note='Forbidden')).status_code, 403)
        self.assertEqual(SupportCase.objects.count(), 1)
        self.assertFalse(SupportEvent.objects.exists())

    def test_manager_without_confirmed_email_uses_existing_verification_gate(self):
        self.manager.email_verification.delete()
        response = self.client.get(reverse('clients:directory'))
        self.assertRedirects(response, reverse('connexion:verify_email'), fetch_redirect_response=False)

    def test_allowed_screens_are_private_and_not_indexable(self):
        case = self.case(user=self.manager, email=self.manager.email)
        for url in self.urls(self.manager, case):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')
                self.assertIn('no-store', response['Cache-Control'])
                self.assertIn('private', response['Cache-Control'])
                self.assertEqual(response['Referrer-Policy'], 'same-origin')

    def test_directory_search_finds_accents_and_partial_email_and_escapes_names(self):
        customer = self.user('elodie', 'elodie@example.test', first_name='Élodie', last_name='Montagne')
        self.user('someone', 'other@example.test', first_name='Autre')
        response = self.client.get(reverse('clients:directory'), {'q': 'ELODIE'})
        self.assertEqual([row.user_id for row in response.context['page_obj']], [customer.pk])
        response = self.client.get(reverse('clients:directory'), {'q': 'lodie@exam'})
        self.assertEqual([row.user_id for row in response.context['page_obj']], [customer.pk])
        customer.first_name = '<script>alert(1)</script>'
        customer.save(update_fields=['first_name'])
        response = self.client.get(reverse('clients:directory'), {'q': 'elodie@'})
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')

    def test_directory_pagination_retains_query(self):
        for index in range(27):
            self.user(f'paginated-{index}', f'pagination-{index}@example.test', first_name='Pagination')
        response = self.client.get(reverse('clients:directory'), {'q': 'Pagination', 'page': 2})
        self.assertEqual(response.context['page_obj'].paginator.count, 27)
        self.assertEqual(len(response.context['page_obj']), 2)
        self.assertContains(response, 'q=Pagination&amp;page=1')

    def test_profile_tabs_consolidate_linked_records_and_preserve_other_customers(self):
        customer = self.user('customer', 'customer@example.test', first_name='Camille')
        other = self.user('other', 'other@example.test')
        order = self.order(user=customer, email='old-customer@example.test')
        other_order = self.order(user=other, email=customer.email)
        invoice = Invoice.objects.create(order=order, number='FAC-TEST-001', issued_at=timezone.now(), snapshot={})
        other_invoice = Invoice.objects.create(order=other_order, number='FAC-OTHER-001', issued_at=timezone.now(), snapshot={})
        retour = ReturnRequest.objects.create(order=order, submission_id=uuid.uuid4(), amount=1000)
        other_retour = ReturnRequest.objects.create(order=other_order, submission_id=uuid.uuid4(), amount=2000)
        benefit = CustomerBenefit.objects.create(user=customer, bound_user_id=customer.pk,
            recipient_email='old-customer@example.test', label='Geste commercial', value=Decimal('15'))
        other_benefit = CustomerBenefit.objects.create(user=other, bound_user_id=other.pk,
            recipient_email=customer.email, label='Autre client', value=Decimal('20'))
        credit = CreditNote.objects.create(order=order, reference='AV-TEST', amount=Decimal('10'), reason='Retour contrôlé')
        voucher = Voucher.objects.create(user=customer, code='LEGACY-TEST', description='Bon existant')
        case = self.case(user=customer, email=customer.email, order=order)
        other_case = self.case(user=other, email=customer.email, order=other_order)
        url = reverse('clients:profile', args=[f'u-{customer.pk}'])
        expected = {'orders': (order, other_order), 'invoices': (invoice, other_invoice),
                    'returns': (retour, other_retour), 'benefits': (benefit, other_benefit), 'cases': (case, other_case)}
        for tab, (own, excluded) in expected.items():
            with self.subTest(tab=tab):
                response = self.client.get(url, {'onglet': tab})
                self.assertEqual(response.status_code, 200)
                self.assertEqual([row.pk for row in response.context['page_obj']], [own.pk])
                self.assertNotIn(excluded.pk, [row.pk for row in response.context['page_obj']])
                if tab == 'benefits':
                    self.assertContains(response, credit.reference)
                    self.assertContains(response, voucher.code)
                    self.assertNotContains(response, other_benefit.code)
        overview = self.client.get(url)
        self.assertEqual(overview.context['customer'].paid_total, Decimal('72.00'))
        self.assertEqual(dict((key, count) for key, label, count in overview.context['tabs'])['benefits'], 3)
        self.assertEqual(self.client.get(url, {'onglet': 'unknown'}).context['tab'], 'overview')

    def test_profile_keeps_guest_homonyms_separate(self):
        first = self.order(email='first@example.test', name='Camille Martin')
        second = self.order(email='second@example.test', name='Camille Martin')
        response = self.client.get(reverse('clients:profile', args=[f'o-{first.pk}']), {'onglet': 'orders'})
        self.assertEqual([row.pk for row in response.context['page_obj']], [first.pk])
        self.assertNotContains(response, str(second.pk))
        self.assertContains(response, 'Client sans compte')

    def test_unknown_customer_and_case_references_are_not_found(self):
        for key in ('u-999999', 'not-a-customer', 'person@example.test'):
            self.assertEqual(self.client.get(reverse('clients:profile', args=[key])).status_code, 404)
        self.assertEqual(self.client.get(reverse('clients:case_detail', args=[uuid.uuid4()])).status_code, 404)
        self.assertEqual(self.client.get(reverse('clients:case_create'), {'client': 'u-999999'}).status_code, 404)

    def test_create_get_does_not_create_a_customer_case_or_event(self):
        before = (get_user_model().objects.count(), SupportCase.objects.count(), SupportEvent.objects.count())
        response = self.client.get(reverse('clients:case_create'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form']['creation_key'].value())
        self.assertEqual((get_user_model().objects.count(), SupportCase.objects.count(), SupportEvent.objects.count()), before)

    def test_create_unknown_phone_only_client_and_idempotent_resubmit(self):
        data = self.create_data(customer_email='', subject='Client sans compte')
        url = reverse('clients:case_create')
        first = self.client.post(url, data)
        case = SupportCase.objects.get()
        self.assertRedirects(first, reverse('clients:case_detail', args=[case.pk]), fetch_redirect_response=False)
        self.assertIsNone(case.user_id)
        self.assertIsNone(case.order_id)
        self.assertEqual(case.customer_email, '')
        self.assertEqual(case.channel, 'phone')
        self.assertEqual(case.state, 'open')
        self.assertEqual(case.created_by_id, self.manager.pk)
        self.assertEqual(SupportEvent.objects.filter(case=case, kind='created').count(), 1)
        second = self.client.post(url, data)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(SupportCase.objects.count(), 1)
        self.assertEqual(SupportEvent.objects.count(), 1)
        response = self.client.get(reverse('clients:directory'), {'q': 'Camille Montagne'})
        self.assertEqual([row.case_ids for row in response.context['page_obj']], [[case.pk]])

    def test_create_rejects_no_contact_and_missing_creation_key_without_writes(self):
        for change, expected_field in (({'customer_email': '', 'phone': ''}, '__all__'), ({'creation_key': ''}, 'creation_key')):
            response = self.client.post(reverse('clients:case_create'), self.create_data(**change))
            self.assertEqual(response.status_code, 400)
            self.assertIn(expected_field, response.context['form'].errors)
        self.assertFalse(SupportCase.objects.exists())

    def test_new_requests_from_phone_only_profile_reuse_the_same_contact(self):
        first = self.client.post(reverse('clients:case_create'), self.create_data(customer_email=''))
        self.assertEqual(first.status_code, 302)
        source = SupportCase.objects.get()
        profile_url = reverse('clients:profile', args=[f'c-{source.pk}'])
        create_url = reverse('clients:case_create') + '?' + urlencode({'client': f'c-{source.pk}'})
        self.assertContains(self.client.get(profile_url), create_url.replace('&', '&amp;'))
        second = self.client.post(create_url, self.create_data(customer_email='', subject='Une deuxième demande'))
        self.assertEqual(second.status_code, 302)
        third = self.client.post(create_url, self.create_data(customer_email='callback@example.test', subject='Une troisième demande'))
        self.assertEqual(third.status_code, 302)
        linked = list(SupportCase.objects.exclude(pk=source.pk))
        self.assertTrue(all(case.contact_reference == source.pk for case in linked))
        response = self.client.get(profile_url, {'onglet': 'cases'})
        self.assertEqual(response.context['customer'].key, f'c-{source.pk}')
        self.assertEqual(response.context['customer'].email, 'callback@example.test')
        self.assertEqual(response.context['page_obj'].paginator.count, 3)
        response = self.client.get(reverse('clients:directory'), {'q': 'Camille Montagne'})
        self.assertEqual(response.context['page_obj'].paginator.count, 1)
        self.assertEqual(len(response.context['page_obj'][0].case_ids), 3)

    def test_new_request_from_email_guest_profile_keeps_original_contact_group(self):
        source = self.case(email='guest@example.test')
        url = reverse('clients:case_create') + '?' + urlencode({'client': f'c-{source.pk}'})
        response = self.client.post(url, self.create_data(customer_email='callback@example.test'))
        self.assertEqual(response.status_code, 302)
        response = self.client.get(reverse('clients:profile', args=[f'c-{source.pk}']), {'onglet': 'cases'})
        self.assertEqual(response.context['page_obj'].paginator.count, 2)
        self.assertEqual(response.context['customer'].key, f'c-{source.pk}')

    def test_create_from_customer_selects_own_order_and_ignores_disabled_email_tampering(self):
        customer = self.user('customer', 'customer@example.test', first_name='Camille')
        order = self.order(user=customer, email='historical@example.test')
        url = reverse('clients:case_create') + '?' + urlencode({'client': f'u-{customer.pk}', 'commande': order.pk})
        response = self.client.get(url)
        self.assertTrue(response.context['form'].fields['customer_email'].disabled)
        self.assertEqual(response.context['form']['related_order'].value(), str(order.pk))
        response = self.client.post(url, self.create_data(customer_email='attacker@example.test', related_order=str(order.pk)))
        self.assertEqual(response.status_code, 302)
        case = SupportCase.objects.get()
        self.assertEqual(case.user_id, customer.pk)
        self.assertEqual(case.customer_email, customer.email)
        self.assertEqual(case.order_id, order.pk)

    def test_create_rejects_an_order_from_another_customer(self):
        customer = self.user('customer', 'customer@example.test')
        other = self.user('other', 'other@example.test')
        order = self.order(user=other, email=other.email)
        url = reverse('clients:case_create') + '?' + urlencode({'client': f'u-{customer.pk}'})
        response = self.client.post(url, self.create_data(customer_email=customer.email, related_order=str(order.pk)))
        self.assertEqual(response.status_code, 400)
        self.assertIn('related_order', response.context['form'].errors)
        self.assertFalse(SupportCase.objects.exists())

    def test_account_without_email_can_open_phone_case_using_its_historical_order(self):
        customer = self.user('no-email', '', first_name='Camille')
        order = self.order(user=customer, email='historical@example.test')
        url = reverse('clients:case_create') + '?' + urlencode({'client': f'u-{customer.pk}'})
        response = self.client.get(url)
        self.assertTrue(response.context['form'].fields['customer_email'].disabled)
        self.assertEqual(response.context['form']['customer_email'].value(), '')
        response = self.client.post(url, self.create_data(customer_email='historical@example.test', related_order=str(order.pk)))
        self.assertEqual(response.status_code, 302)
        case = SupportCase.objects.get()
        self.assertEqual(case.user_id, customer.pk)
        self.assertEqual(case.order_id, order.pk)
        self.assertEqual(case.customer_email, '')
        self.assertEqual(case.phone, '06 00 00 00 00')

    def test_create_cannot_reassign_identity_with_hidden_user_or_order_values(self):
        other = self.user('other', 'other@example.test')
        order = self.order(user=other, email=other.email)
        response = self.client.post(reverse('clients:case_create'), self.create_data(user=other.pk, user_id=other.pk,
                                    order=str(order.pk), order_id=str(order.pk), related_order=str(order.pk)))
        self.assertEqual(response.status_code, 400)
        self.assertIn('related_order', response.context['form'].errors)
        self.assertFalse(SupportCase.objects.exists())
        response = self.client.post(reverse('clients:case_create'), self.create_data(user=other.pk, user_id=other.pk,
                                    order=str(order.pk), order_id=str(order.pk), related_order=''))
        self.assertEqual(response.status_code, 302)
        case = SupportCase.objects.get()
        self.assertIsNone(case.user_id)
        self.assertIsNone(case.order_id)

    def test_csrf_required_for_create_and_update(self):
        client = Client(enforce_csrf_checks=True)
        self.login(self.manager, client)
        create_url = reverse('clients:case_create')
        self.assertEqual(client.post(create_url, self.create_data()).status_code, 403)
        client.get(create_url)
        data = self.create_data(csrfmiddlewaretoken=client.cookies['csrftoken'].value)
        self.assertEqual(client.post(create_url, data).status_code, 302)
        case = SupportCase.objects.get()
        update_url = reverse('clients:case_detail', args=[case.pk])
        self.assertEqual(client.post(update_url, self.update_data(case, note='Refusé')).status_code, 403)
        client.get(update_url)
        data = self.update_data(case, note='Rappel effectué', csrfmiddlewaretoken=client.cookies['csrftoken'].value)
        self.assertEqual(client.post(update_url, data).status_code, 302)
        self.assertEqual(case.events.count(), 2)

    def test_update_requires_revision_and_solution_before_closing(self):
        case = self.case()
        url = reverse('clients:case_detail', args=[case.pk])
        response = self.client.post(url, self.update_data(case, revision=''))
        self.assertEqual(response.status_code, 400)
        self.assertIn('revision', response.context['form'].errors)
        response = self.client.post(url, self.update_data(case, state='resolved'))
        self.assertEqual(response.status_code, 400)
        self.assertIn('note', response.context['form'].errors)
        case.refresh_from_db()
        self.assertEqual(case.revision, 1)
        self.assertEqual(case.state, 'open')
        self.assertFalse(case.events.exists())

    def test_update_closes_with_a_note_and_can_reopen(self):
        case = self.case()
        url = reverse('clients:case_detail', args=[case.pk])
        response = self.client.post(url, self.update_data(case, state='resolved', note='Le client a reçu son colis.'))
        self.assertEqual(response.status_code, 302)
        case.refresh_from_db()
        self.assertEqual(case.state, 'resolved')
        self.assertIsNotNone(case.resolved_at)
        self.assertEqual(case.revision, 2)
        self.assertEqual(case.events.get().note, 'Le client a reçu son colis.')
        response = self.client.post(url, self.update_data(case, state='in_progress', priority='urgent', note='Le client rappelle.'))
        self.assertEqual(response.status_code, 302)
        case.refresh_from_db()
        self.assertEqual(case.state, 'in_progress')
        self.assertIsNone(case.resolved_at)
        self.assertEqual(case.priority, 'urgent')
        self.assertEqual(case.revision, 3)
        self.assertEqual(case.events.count(), 2)

    def test_stale_update_preserves_note_without_overwriting_current_state(self):
        case = self.case()
        url = reverse('clients:case_detail', args=[case.pk])
        stale = self.update_data(case, state='resolved', note='Ma note à conserver')
        self.assertEqual(self.client.post(url, self.update_data(case, state='waiting_customer', note='Pièce demandée')).status_code, 302)
        response = self.client.post(url, stale)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.context['form']['note'].value(), 'Ma note à conserver')
        self.assertContains(response, 'Ma note à conserver', status_code=409)
        case.refresh_from_db()
        self.assertEqual(case.state, 'waiting_customer')
        self.assertEqual(case.revision, 2)
        self.assertEqual(case.events.count(), 1)

    def test_case_detail_escapes_description_and_history_notes(self):
        script = '<script>alert(1)</script>'
        case = self.case(description=script, subject=script, name=script)
        url = reverse('clients:case_detail', args=[case.pk])
        self.assertEqual(self.client.post(url, self.update_data(case, note=script)).status_code, 302)
        response = self.client.get(url)
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(response, script)

    def test_case_tracking_never_sends_mail_or_changes_financial_records(self):
        customer = self.user('customer', 'customer@example.test')
        order = self.order(user=customer, email=customer.email)
        benefit = CustomerBenefit.objects.create(user=customer, bound_user_id=customer.pk,
            recipient_email=customer.email, label='Geste commercial', value=Decimal('15'))
        before_order = Order.objects.values().get(pk=order.pk)
        before_benefit = CustomerBenefit.objects.values().get(pk=benefit.pk)
        url = reverse('clients:case_create') + '?' + urlencode({'client': f'u-{customer.pk}'})
        response = self.client.post(url, self.create_data(customer_email=customer.email, related_order=str(order.pk), category='return'))
        self.assertEqual(response.status_code, 302)
        case = SupportCase.objects.get()
        self.assertEqual(self.client.post(reverse('clients:case_detail', args=[case.pk]),
            self.update_data(case, state='resolved', note='Information transmise au client par téléphone.')).status_code, 302)
        self.assertEqual(Order.objects.values().get(pk=order.pk), before_order)
        self.assertEqual(CustomerBenefit.objects.values().get(pk=benefit.pk), before_benefit)
        self.assertFalse(ReturnRequest.objects.exists())
        self.assertFalse(Invoice.objects.exists())
        self.assertFalse(CreditNote.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_case_list_filters_and_sorts_callbacks_then_urgent_requests(self):
        today = timezone.localdate()
        due = self.case(name='Élodie Montagne', subject='Colis en retard', due_date=today)
        urgent = self.case(name='Élodie Montagne', priority='urgent')
        ordinary = self.case(name='Élodie Montagne')
        closed = self.case(name='Élodie Montagne', state='resolved', resolved_at=timezone.now(), due_date=today - timedelta(days=1))
        url = reverse('clients:case_list')
        response = self.client.get(url, {'q': 'ELODIE'})
        self.assertEqual([case.pk for case in response.context['page_obj']], [due.pk, urgent.pk, ordinary.pk])
        response = self.client.get(url, {'q': 'elodie', 'priorite': 'urgent'})
        self.assertEqual([case.pk for case in response.context['page_obj']], [urgent.pk])
        response = self.client.get(url, {'relance': '1', 'etat': 'all'})
        self.assertEqual([case.pk for case in response.context['page_obj']], [due.pk])
        response = self.client.get(url, {'etat': 'resolved'})
        self.assertEqual([case.pk for case in response.context['page_obj']], [closed.pk])
        response = self.client.get(url, {'q': due.reference})
        self.assertEqual([case.pk for case in response.context['page_obj']], [due.pk])
        response = self.client.get(url, {'etat': 'invalid', 'priorite': 'invalid'})
        self.assertEqual(response.context['state'], 'active')
        self.assertEqual(response.context['priority'], '')

    def test_case_pagination_preserves_active_filters(self):
        for index in range(26):
            self.case(name=f'Pagination {index}', priority='urgent', due_date=timezone.localdate())
        response = self.client.get(reverse('clients:case_list'),
                                   {'q': 'Pagination', 'priorite': 'urgent', 'relance': '1', 'etat': 'open', 'page': 2})
        self.assertEqual(response.context['page_obj'].paginator.count, 26)
        self.assertEqual(len(response.context['page_obj']), 1)
        self.assertContains(response, 'q=Pagination&amp;etat=open&amp;priorite=urgent&amp;relance=1&amp;page=1')

    def test_read_only_endpoints_reject_posts(self):
        urls = [reverse('clients:directory'), reverse('clients:case_list'),
                reverse('clients:profile', args=[f'u-{self.manager.pk}'])]
        for url in urls:
            self.assertEqual(self.client.post(url, self.create_data()).status_code, 405)
        self.assertFalse(SupportCase.objects.exists())

    def test_admin_account_shows_workspace_link_but_customer_account_does_not(self):
        url = reverse('espace:account')
        destination = reverse('clients:directory')
        response = self.client.get(url)
        self.assertContains(response, f'href="{destination}"', count=2)
        self.assertContains(response, 'Clients et SAV')
        customer = self.user('customer', 'customer@example.test')
        verify_test_user(customer)
        self.login(customer)
        response = self.client.get(url)
        self.assertNotContains(response, f'href="{destination}"')

    def test_workspace_back_links_use_stable_parents_even_after_a_post(self):
        case = self.case()
        expected = {
            reverse('clients:directory'): reverse('espace:account'),
            reverse('clients:profile', args=[f'u-{self.manager.pk}']): reverse('clients:directory'),
            reverse('clients:case_list'): reverse('clients:directory'),
            reverse('clients:case_create'): reverse('clients:case_list'),
            reverse('clients:case_detail', args=[case.pk]): reverse('clients:case_list'),
        }
        for url, parent in expected.items():
            with self.subTest(url=url):
                response = self.client.get(url, HTTP_REFERER='http://testserver' + url)
                self.assertEqual(response.context['back_url'], parent)
                self.assertEqual(response.context['back_fallback'], parent)
                self.assertFalse(response.context['back_use_history'])

    def test_order_tab_pagination_keeps_selected_tab(self):
        customer = self.user('customer', 'customer@example.test')
        for index in range(26):
            self.order(user=customer, email=customer.email)
        response = self.client.get(reverse('clients:profile', args=[f'u-{customer.pk}']), {'onglet': 'orders', 'page': 2})
        self.assertEqual(response.context['page_obj'].paginator.count, 26)
        self.assertEqual(len(response.context['page_obj']), 1)
        self.assertContains(response, 'onglet=orders&amp;page=1')
