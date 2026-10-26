"""Private customer picker: synthetic identities, bounded results and no writes."""
import json
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from connexion.test_helpers import verify_test_user
from paiement.models import Order
from .customer_picker import picker_context
from .models import SupportCase
from .selectors import get_customer


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'], LOCAL_SKIP_EMAIL_VERIFICATION=False)
class CustomerPickerTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.seller = User.objects.create_user('picker-seller', email='picker-seller@example.test', is_staff=True)
        self.seller.user_permissions.add(Permission.objects.get(codename='manage_returns'))
        verify_test_user(self.seller)
        self.customer = User.objects.create_user('camille', email='camille@example.test', first_name='Camille', last_name='Été')
        self.order = Order.objects.create(user=self.customer, session_key='picker-test', customer_email='old@example.test',
            status='paid', subtotal=8000, total_paid=8000, paid_at=timezone.now(), items=[],
            billing_details={'name': 'Camille Été', 'phone': '06 12 34 56 78'}, shipping_details={})
        self.client.force_login(self.seller, backend='connexion.backends.EmailOrUsernameBackend')

    def case(self, name, phone='06 00 00 00 00', email=''):
        return SupportCase.objects.create(creation_key=uuid.uuid4(), creation_fingerprint='x'*64,
            customer_name=name, customer_email=email, phone=phone,
            subject='Demande synthétique', description='Ne contient aucun document réel.')

    def search(self, q):
        return self.client.post(reverse('clients:customer_search'), {'q': q})

    def pick(self, key):
        return self.client.post(reverse('clients:customer_pick'), {'client': key})

    def test_search_matches_names_current_or_historical_emails_without_case_or_accents(self):
        for query in ('camille ete', 'CAMILLE@', 'old@example'):
            with self.subTest(query=query):
                response = self.search(query)
                self.assertEqual(response.status_code, 200)
                data = response.json()['results']
                self.assertEqual(len(data), 1)
                self.assertEqual(data[0]['key'], f'u-{self.customer.pk}')
                self.assertEqual(data[0]['email'], 'camille@example.test')
                self.assertNotIn('orders', data[0])

    def test_phone_search_ignores_separators_and_does_not_merge_homonyms(self):
        other = self.case('Autre Camille', phone='06.12.34.56.78')
        for query in ('0612345678', '06 12 34', '06.12.34', '06-12-34'):
            with self.subTest(query=query):
                keys = {row['key'] for row in self.search(query).json()['results']}
                self.assertEqual(keys, {f'u-{self.customer.pk}', f'c-{other.pk}'})

    def test_no_query_does_not_expose_directory_and_does_not_read_records(self):
        with patch('clients.customer_picker.find_customers', side_effect=AssertionError('unbounded read')):
            for query in ('', ' ', 'a', '06', '0-6', '+( )'):
                self.assertEqual(self.search(query).json(), {'results': [], 'more': False})

    def test_results_are_limited_and_more_indicated_without_automatic_selection(self):
        for index in range(12):
            self.case(f'Exemple partagé {index}', phone=f'06000000{index:02}')
        response = self.search('exemple partage')
        self.assertEqual(len(response.json()['results']), 10)
        self.assertTrue(response.json()['more'])
        self.assertNotIn('customer', response.json())
        self.assertFalse(any(case.user_id for case in SupportCase.objects.all()))

    def test_pick_returns_only_selected_customers_orders_and_authoritative_account_email(self):
        other = get_user_model().objects.create_user('another', email='another@example.test')
        Order.objects.create(user=other, session_key='other', customer_email=self.customer.email,
            subtotal=1200, items=[], status='demo')
        payload = self.pick(f'u-{self.customer.pk}').json()['customer']
        self.assertEqual(payload['email'], 'camille@example.test')
        self.assertTrue(payload['email_locked'])
        self.assertEqual([row['value'] for row in payload['orders']], [str(self.order.pk)])
        self.assertNotIn('items', json.dumps(payload))
        self.assertNotIn('billing_details', json.dumps(payload))
        self.assertNotIn('codes', payload)

    def test_phone_only_guest_and_context_use_stable_explicit_key(self):
        case = self.case('Client téléphone')
        customer = get_customer(f'c-{case.pk}')
        payload = self.pick(customer.key).json()['customer']
        self.assertEqual(payload['key'], customer.key)
        self.assertFalse(payload['email_locked'])
        self.assertEqual(payload['orders'], [])
        self.assertEqual(picker_context(customer)['selected_client'], customer.key)
        self.assertIsNone(picker_context()['picker_initial'])

    def test_lookups_are_post_only_private_and_dont_create_records(self):
        counts = (get_user_model().objects.count(), SupportCase.objects.count(), Order.objects.count())
        for url in (reverse('clients:customer_search'), reverse('clients:customer_pick')):
            self.assertEqual(self.client.get(url).status_code, 405)
        for response in (self.search('camille'), self.pick(f'u-{self.customer.pk}')):
            self.assertIn('private', response['Cache-Control'])
            self.assertIn('no-store', response['Cache-Control'])
            self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')
            self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(counts, (get_user_model().objects.count(), SupportCase.objects.count(), Order.objects.count()))

    def test_invalid_or_disappeared_selection_is_not_resolved_by_email(self):
        for key in ('', self.customer.email, 'u-999999', 'c-'+str(uuid.uuid4()), '../../'):
            self.assertEqual(self.pick(key).status_code, 404)

    def test_access_is_checked_before_search_or_selection(self):
        unprivileged = get_user_model().objects.create_user('staff-no-sav', email='staff@example.test', is_staff=True)
        verify_test_user(unprivileged)
        for actor in (None, self.customer, unprivileged):
            with self.subTest(actor=actor), patch('clients.customer_picker.find_customers', side_effect=AssertionError('private lookup')), patch('clients.customer_picker.get_customer', side_effect=AssertionError('private lookup')):
                if actor:
                    self.client.force_login(actor, backend='connexion.backends.EmailOrUsernameBackend')
                else:
                    self.client.logout()
                expected = 403 if actor else 302
                self.assertEqual(self.search('camille').status_code, expected)
                self.assertEqual(self.pick(f'u-{self.customer.pk}').status_code, expected)

    def test_csrf_is_required_and_unverified_manager_cannot_read_contacts(self):
        secured = Client(enforce_csrf_checks=True)
        secured.force_login(self.seller, backend='connexion.backends.EmailOrUsernameBackend')
        self.assertEqual(secured.post(reverse('clients:customer_search'), {'q': 'camille'}).status_code, 403)
        self.assertEqual(secured.post(reverse('clients:customer_pick'), {'client': f'u-{self.customer.pk}'}).status_code, 403)
        self.seller.email_verification.delete()
        with patch('clients.customer_picker.find_customers', side_effect=AssertionError('private lookup')):
            response = self.search('camille')
            self.assertEqual(response.status_code, 302)
            self.assertIn('verification-email', response.url)

    def test_markup_like_names_are_data_and_never_html(self):
        case = self.case('<img src=x onerror=alert(1)> Exemple')
        response = self.search('exemple')
        self.assertEqual(response['Content-Type'], 'application/json')
        self.assertEqual(response.json()['results'][0]['name'], case.customer_name)
        # The UI creates text nodes. Its progressive-enhancement version uses
        # Django autoescaping rather than serializing contact markup as HTML.
        from django.template.loader import render_to_string
        html = render_to_string('clients/customer_picker.html', picker_context(query='exemple', results=response.json()['results']))
        self.assertNotIn('<img src=x', html)
        self.assertIn('&lt;img', html)
