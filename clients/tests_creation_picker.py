"""The picker must preserve the draft and the selected customer's identity."""
import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from avoirs.models import CustomerBenefit
from connexion.test_helpers import verify_test_user
from paiement.models import Order
from .models import SupportCase
from .selectors import get_customer
from .services import create_case


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class CaseCreationPickerTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.seller = User.objects.create_user('picker-seller', email='seller@example.test', is_staff=True)
        self.seller.user_permissions.add(Permission.objects.get(codename='manage_returns'))
        verify_test_user(self.seller)
        self.client.force_login(self.seller, backend='connexion.backends.EmailOrUsernameBackend')
        self.customer = User.objects.create_user('camille', email='camille@example.test', first_name='Camille')
        self.order = self.make_order(user=self.customer, email=self.customer.email)
        self.url = reverse('clients:case_create')

    def make_order(self, user=None, email=''):
        return Order.objects.create(user=user, customer_email=email, session_key='picker-test',
            items=[], subtotal=8000, status='paid', total_paid=8000,
            billing_details={'name': 'Camille', 'phone': '0600000000'})

    def data(self, **extra):
        values = {'creation_key': str(uuid.uuid4()), 'customer_name': 'Camille',
                  'customer_email': 'camille@example.test', 'phone': '0600000000',
                  'subject': 'Rappeler pour la livraison', 'description': 'Demande déjà saisie.',
                  'channel': 'phone', 'category': 'delivery', 'priority': 'urgent',
                  'due_date': '2026-11-15', 'related_order': ''}
        values.update(extra)
        return values

    def assert_draft(self, response, data):
        for field in ('creation_key', 'subject', 'description', 'channel', 'category', 'priority', 'due_date'):
            self.assertEqual(str(response.context['form'][field].value()), data[field], field)
        self.assertFalse(SupportCase.objects.exists())

    def test_fallback_search_and_selection_keep_unsaved_request(self):
        data = self.data(picker_action='search', client_query='Camille')
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assert_draft(response, data)
        self.assertEqual(response.context['picker_results'][0]['key'], f'u-{self.customer.pk}')
        data['picker_action'] = f'select:u-{self.customer.pk}'
        response = self.client.post(self.url, data)
        self.assert_draft(response, data)
        self.assertEqual(response.context['form']['customer_email'].value(), self.customer.email)
        self.assertIn(str(self.order.pk), dict(response.context['form'].fields['related_order'].choices))
        self.assertContains(response, 'clients/customer-picker.js')

    def test_new_client_clears_only_identity_fields(self):
        data = self.data(selected_client=f'u-{self.customer.pk}', picker_action='new', related_order=str(self.order.pk))
        response = self.client.post(self.url, data)
        self.assert_draft(response, data)
        for field in ('customer_name', 'customer_email', 'phone', 'related_order'):
            self.assertEqual(response.context['form'][field].value(), '')
        self.assertEqual(response.context['selected_client'], '')

    def test_selected_account_overrides_url_and_tampered_email(self):
        response = self.client.post(self.url + f'?client=u-{self.seller.pk}', self.data(
            selected_client=f'u-{self.customer.pk}', customer_email='tampered@example.test', related_order=str(self.order.pk)))
        self.assertEqual(response.status_code, 302)
        case = SupportCase.objects.get()
        self.assertEqual((case.user_id, case.order_id, case.customer_email),
                         (self.customer.pk, self.order.pk, self.customer.email))

    def test_foreign_order_and_disappeared_selection_never_save(self):
        other = self.make_order(email='other@example.test')
        response = self.client.post(self.url, self.data(selected_client=f'u-{self.customer.pk}', related_order=str(other.pk)))
        self.assertEqual(response.status_code, 400)
        self.assertIn('related_order', response.context['form'].errors)
        for action in ('', 'select:u-999999', 'unknown'):
            data = self.data(selected_client='u-999999', picker_action=action)
            response = self.client.post(self.url, data)
            self.assertEqual(response.status_code, 409)
            self.assert_draft(response, data)
            self.assertTrue(response.context['picker_error'])
        self.assertEqual(self.client.get(self.url, {'client': 'u-999999'}).status_code, 404)

    def test_guest_without_email_stays_in_selected_profile_without_related_order(self):
        guest_order = self.make_order()
        data = self.data(selected_client=f'o-{guest_order.pk}', customer_email='new-guest@example.test')
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 302)
        case = SupportCase.objects.get()
        self.assertIsNone(case.order_id)
        self.assertEqual(case.contact_reference, guest_order.pk)
        self.assertIn(case.pk, get_customer(f'o-{guest_order.pk}').case_ids)
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.assertEqual(SupportCase.objects.count(), 1)

    def test_guest_source_is_revalidated_when_its_order_becomes_bound(self):
        guest_order = self.make_order()
        Order.objects.filter(pk=guest_order.pk).update(user=self.customer)
        data = self.data()
        data.pop('related_order')
        with self.assertRaises(ValidationError):
            create_case(self.seller, data, contact_order=guest_order)
        self.assertFalse(SupportCase.objects.exists())

    def test_old_account_benefit_cannot_attach_case_to_reused_email(self):
        benefit = CustomerBenefit.objects.create(bound_user_id=999999, recipient_email=self.customer.email,
            label='Ancien client', value=Decimal('15'))
        response = self.client.post(self.url, self.data(selected_client=f'b-{benefit.pk}'))
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, 'ancienne fiche', status_code=409)
        self.assertFalse(SupportCase.objects.exists())

    def test_unbound_benefit_selection_keeps_its_email(self):
        benefit = CustomerBenefit.objects.create(recipient_email='guest-only@example.test',
            label='Client sans compte', value=Decimal('15'))
        data = self.data(selected_client=f'b-{benefit.pk}')
        self.assertEqual(self.client.post(self.url, data).status_code, 409)
        data['customer_email'] = benefit.recipient_email
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.assertIn(SupportCase.objects.get().pk, get_customer(f'b-{benefit.pk}').case_ids)
