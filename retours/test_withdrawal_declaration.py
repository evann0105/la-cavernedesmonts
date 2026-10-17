import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.db import connection
from django.core import mail
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from paiement.models import Order
from .access import order_token
from .forms import RequestForm
from .models import ReturnMail, ReturnRequest
from .services import ReturnError, submit
from .mail import deliver_pending


class WithdrawalDeclarationFormTests(SimpleTestCase):
    def make_order(self, **changes):
        values = {'user_id':None, 'customer_email':'commande@example.test',
                  'billing_details':{'name':'Nom Facturation'},
                  'shipping_details':{'name':'Nom Livraison'}}
        values.update(changes)
        return SimpleNamespace(**values)

    def make_form(self, order, data=None):
        with patch('retours.forms.remaining_items', return_value=[]):
            return RequestForm(order, data)

    def test_name_is_required_and_known_identity_is_prefilled(self):
        form = self.make_form(self.make_order())
        self.assertEqual(form['customer_name'].value(), 'Nom Facturation')
        invalid = self.make_form(self.make_order(), {
            'submission_id':uuid.uuid4(), 'kind':'withdrawal', 'confirm':'on',
        })
        self.assertFalse(invalid.is_valid())
        self.assertIn('customer_name', invalid.errors)

    def test_shipping_then_account_name_fallback_without_email_guess(self):
        form = self.make_form(self.make_order(billing_details={}))
        self.assertEqual(form['customer_name'].value(), 'Nom Livraison')
        form = self.make_form(self.make_order(billing_details={}, shipping_details={},
            user_id=1, user=SimpleNamespace(get_full_name=lambda:'Nom Compte')))
        self.assertEqual(form['customer_name'].value(), 'Nom Compte')
        form = self.make_form(self.make_order(billing_details={}, shipping_details={}))
        self.assertEqual(form['customer_name'].value(), '')

    def test_posted_recipient_cannot_redirect_confirmation_to_another_person(self):
        form = self.make_form(self.make_order(), {
            'submission_id':uuid.uuid4(), 'customer_name':'  Élodie   Exemple  ',
            'confirmation_email':'pirate@example.test', 'kind':'withdrawal', 'confirm':'on',
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['confirmation_email'], 'commande@example.test')
        self.assertEqual(form.cleaned_data['customer_name'], 'Élodie Exemple')


@override_settings(DEBUG=True, EMAIL_DELIVERY_ENABLED=False,
                   LOCAL_SKIP_EMAIL_VERIFICATION=True, SITE_URL='http://testserver')
class WithdrawalDeclarationSnapshotTests(TestCase):
    def setUp(self):
        self.order = Order.objects.create(
            session_key='declaration-test', status='paid', customer_email='commande@example.test',
            shipping_details={'name':'Nom Livraison'},
            items=[{'name':'Polaire', 'quantity':2, 'unit_amount':4000}],
            subtotal=8000, total_paid=8000, return_shipping_amount=0)

    def test_guest_form_queues_confirmed_name_only_to_the_order_email(self):
        url = reverse('retours:order', args=[self.order.pk])+'?token='+order_token(self.order)
        response = self.client.post(url, {
            'submission_id':uuid.uuid4(), 'customer_name':'Client Exemple',
            'confirmation_email':'pirate@example.test',
            'quantity_0':1, 'kind':'withdrawal', 'confirm':'on',
        })
        self.assertEqual(response.status_code, 302)
        record = ReturnRequest.objects.get(order=self.order)
        self.assertEqual(record.declarant, {'name':'Client Exemple','email':'commande@example.test'})
        message = ReturnMail.objects.get(request=record)
        self.assertEqual(message.payload['recipient'], 'commande@example.test')
        self.assertIn('Déclarant : Client Exemple', message.payload['details'])
        self.assertNotIn('pirate@example.test', message.payload['details'])
        self.assertIn('Déclaration reçue le ', message.payload['details'])

    @override_settings(EMAIL_DELIVERY_ENABLED=True,
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_acknowledgement_sends_once_with_original_confirmed_name_and_one_recipient(self):
        nonce = uuid.uuid4()
        record = submit(self.order.pk, {0:1}, nonce,
            declarant={'name':'Camille Exemple', 'email':self.order.customer_email})
        submit(self.order.pk, {0:1}, nonce,
            declarant={'name':'Autre Nom', 'email':self.order.customer_email})
        self.assertEqual(deliver_pending(order_id=self.order.pk), 1)
        self.assertEqual(deliver_pending(order_id=self.order.pk), 0)
        self.assertEqual(len(mail.outbox), 1)
        acknowledgement = mail.outbox[0]
        self.assertEqual(acknowledgement.to, ['commande@example.test'])
        self.assertEqual(acknowledgement.cc, [])
        self.assertEqual(acknowledgement.bcc, [])
        self.assertIn('Déclarant : Camille Exemple', acknowledgement.body)
        self.assertIn('Camille Exemple', acknowledgement.alternatives[0].content)
        self.assertNotIn('Autre Nom', acknowledgement.body)
        self.assertIn('Déclaration reçue le ', acknowledgement.body)
        self.assertTrue(ReturnMail.objects.get(request=record).sent_at)

    @override_settings(EMAIL_DELIVERY_ENABLED=True,
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_queued_acknowledgement_is_not_redirected_if_the_order_email_changes(self):
        record = submit(self.order.pk, {0:1}, uuid.uuid4(),
            declarant={'name':'Client Exemple', 'email':self.order.customer_email})
        self.order.customer_email = 'autre@example.test'
        self.order.save(update_fields=['customer_email'])
        self.assertEqual(deliver_pending(order_id=self.order.pk), 0)
        self.assertEqual(len(mail.outbox), 0)
        queued = ReturnMail.objects.get(request=record)
        self.assertEqual(queued.payload['recipient'], 'commande@example.test')
        self.assertIsNone(queued.sent_at)

    def test_repeat_submission_does_not_rewrite_declaration_or_duplicate_acknowledgement(self):
        nonce = uuid.uuid4()
        first = submit(self.order.pk, {0:1}, nonce, declarant={'name':'Premier Nom','email':self.order.customer_email})
        second = submit(self.order.pk, {0:1}, nonce, declarant={'name':'Autre Nom','email':self.order.customer_email})
        first.refresh_from_db()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.declarant['name'], 'Premier Nom')
        self.assertEqual(ReturnMail.objects.filter(request=first).count(), 1)

    def test_service_rejects_foreign_recipient_and_empty_explicit_name(self):
        for declarant in ({'name':'Client Exemple','email':'autre@example.test'},
                          {'name':'  ','email':self.order.customer_email}):
            with self.subTest(declarant=declarant):
                with self.assertRaises(ReturnError):
                    submit(self.order.pk, {0:1}, uuid.uuid4(), declarant=declarant)
        self.assertFalse(ReturnRequest.objects.exists())

    def test_declaration_personal_information_is_encrypted_in_storage(self):
        record = submit(self.order.pk, {0:1}, uuid.uuid4(), declarant={'name':'Client Exemple','email':self.order.customer_email})
        with connection.cursor() as cursor:
            cursor.execute('SELECT declarant FROM retours_returnrequest WHERE id = %s', [record.pk])
            stored = cursor.fetchone()[0]
        self.assertTrue(stored.startswith('enc:v1:'))
        self.assertNotIn('Client Exemple', stored)
        self.assertNotIn(self.order.customer_email, stored)
