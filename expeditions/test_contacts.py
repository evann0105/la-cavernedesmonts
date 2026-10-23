"""Operational delivery corrections never rewrite the original purchase."""
import uuid
from copy import deepcopy
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from connexion.models import EmailVerification
from connexion.test_helpers import verify_test_user
from facturation.delivery import build_delivery_slip
from facturation.models import Invoice
from facturation.services import build_preview
from paiement.models import Order
from retours.mail import deliver_pending
from retours.models import ReturnMail, ReturnRequest
from retours.services import submit

from .contacts import correct_contacts, effective_email, effective_shipping
from .models import Fulfillment, FulfillmentEvent
from .public import public_status
from .services import FulfillmentError, RevisionConflict
from .tests import FulfillmentFixture, LOGIN_BACKEND, TEST_SETTINGS, TRACKING


CORRECTED_EMAIL = 'corrected-parcel@example.test'
CORRECTED_SHIPPING = {
    'name': 'Destinataire corrigé fictif',
    'address': {'line1': '456 rue Exemple', 'line2': 'Bâtiment de test',
                'postal_code': '75000', 'city': 'Ville Test', 'state': '', 'country': 'FR'},
}
PRIVATE_REASON = 'PRIVATE_DELIVERY_CORRECTION_REASON'


@override_settings(**TEST_SETTINGS)
class FulfillmentContactTests(FulfillmentFixture, TestCase):
    def current_order(self):
        return Order.objects.get(pk=self.order.pk)

    def contacts_url(self, section='address', order=None):
        return reverse('expeditions:contacts', args=[(order or self.order).pk]) + '?section=' + section

    def correction(self, kind, values=None, *, revision=None, reason=PRIVATE_REASON, confirmed=True):
        if values is None:
            values = deepcopy(CORRECTED_SHIPPING) if kind == 'address' else CORRECTED_EMAIL
        if revision is None:
            revision = self.info()['revision']
        return correct_contacts(self.order.pk, self.seller, revision, kind, values, reason, confirmed)

    def address_post(self, **changes):
        data = {'kind': 'address', 'revision': self.info()['revision'],
                'name': CORRECTED_SHIPPING['name'], **CORRECTED_SHIPPING['address'],
                'reason': PRIVATE_REASON, 'confirmed': 'on'}
        data.update(changes)
        return data

    def email_post(self, **changes):
        data = {'kind': 'email', 'revision': self.info()['revision'],
                'email': CORRECTED_EMAIL, 'email_confirmation': CORRECTED_EMAIL,
                'reason': PRIVATE_REASON, 'confirmed': 'on'}
        data.update(changes)
        return data

    def purchase_snapshot(self):
        order = self.current_order()
        fields = ('items', 'subtotal', 'total_paid', 'paid_line_totals', 'return_shipping_amount',
                  'shipping_details', 'billing_details', 'customer_email', 'shipping_snapshot',
                  'invoice_profile', 'terms_snapshot', 'terms_accepted_at', 'status', 'paid_at')
        return {field: deepcopy(getattr(order, field)) for field in fields}

    def test_get_both_contact_forms_creates_no_fulfillment_event_or_notice(self):
        for section in ('address', 'email'):
            response = self.client.get(self.contacts_url(section))
            self.assertEqual(response.status_code, 200)
            self.assertIn('no-store', response.headers['Cache-Control'])
            self.assertEqual(response.headers['Referrer-Policy'], 'same-origin')
        self.assertFalse(Fulfillment.objects.exists())
        self.assertFalse(FulfillmentEvent.objects.exists())
        self.assertFalse(ReturnMail.objects.exists())

    def test_contacts_require_staff_return_permission_for_read_and_write(self):
        self.assertEqual(Client().get(self.contacts_url()).status_code, 302)
        staff = get_user_model().objects.create_user('contacts-staff', email='staff@example.test', is_staff=True)
        nonstaff = get_user_model().objects.create_user('contacts-nonstaff', email='nonstaff@example.test')
        nonstaff.user_permissions.add(Permission.objects.get(
            content_type__app_label='retours', codename='manage_returns'))
        for user in (staff, nonstaff, self.customer, self.other):
            verify_test_user(user)
            with self.subTest(user=user.username):
                self.client.force_login(user, backend=LOGIN_BACKEND)
                self.assertEqual(self.client.get(self.contacts_url()).status_code, 403)
                self.assertEqual(self.client.post(self.contacts_url('email'), self.email_post()).status_code, 403)
                with self.assertRaises(PermissionDenied):
                    correct_contacts(self.order.pk, user, 0, 'email', CORRECTED_EMAIL, PRIVATE_REASON, True)
        self.assertFalse(Fulfillment.objects.exists())

    def test_unverified_manager_cannot_read_or_correct_delivery_contacts(self):
        EmailVerification.objects.filter(user=self.seller).delete()
        target = reverse('connexion:verify_email')
        self.assertRedirects(self.client.get(self.contacts_url()), target, fetch_redirect_response=False)
        self.assertRedirects(self.client.post(self.contacts_url('email'), self.email_post()), target,
                             fetch_redirect_response=False)
        with self.assertRaises(PermissionDenied):
            self.correction('email')
        self.assertFalse(Fulfillment.objects.exists())

    def test_both_contact_forms_require_csrf(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.seller, backend=LOGIN_BACKEND)
        for section, data in (('address', self.address_post()), ('email', self.email_post())):
            self.assertEqual(strict.post(self.contacts_url(section), data).status_code, 403)
        self.assertFalse(Fulfillment.objects.exists())

    def test_first_correction_creates_preparing_revision_without_touching_purchase_or_stock(self):
        original = self.purchase_snapshot()
        with patch('courriels.messages.EmailMultiAlternatives.send') as send:
            result = self.correction('address')
            send.assert_not_called()
        self.assertEqual((result.state, result.revision), ('preparing', 1))
        self.assertEqual(effective_shipping(self.current_order()), CORRECTED_SHIPPING)
        self.assertEqual(effective_email(self.current_order()), original['customer_email'])
        self.assert_untouched_purchase_and_stock(original)
        self.assertFalse(ReturnMail.objects.exists())
        self.assertEqual(FulfillmentEvent.objects.count(), 1)

    def test_address_and_email_corrections_use_one_shared_revision(self):
        self.correction('address', revision=0)
        with self.assertRaises(RevisionConflict):
            self.correction('email', revision=0)
        self.assertEqual(effective_email(self.current_order()), self.order.customer_email)
        response = self.client.post(self.contacts_url('email'), self.email_post(revision=0))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.info()['revision'], 1)
        self.correction('email', revision=1)
        self.assertEqual(self.info()['revision'], 2)
        self.assertEqual(effective_shipping(self.current_order()), CORRECTED_SHIPPING)
        self.assertEqual(effective_email(self.current_order()), CORRECTED_EMAIL)

    def test_repeated_post_cannot_create_duplicate_contact_events(self):
        data = self.email_post()
        first = self.client.post(self.contacts_url('email'), data)
        self.assertRedirects(first, self.detail_url(), fetch_redirect_response=False)
        self.assertEqual(self.client.post(self.contacts_url('email'), data).status_code, 409)
        self.assertEqual(FulfillmentEvent.objects.count(), 1)
        self.assertEqual(self.info()['revision'], 1)
        self.assertFalse(ReturnMail.objects.exists())

    def test_reason_confirmation_and_actual_change_are_required(self):
        for reason in ('', '   ', 'x' * 1001):
            with self.subTest(reason_length=len(reason)):
                with self.assertRaises(FulfillmentError):
                    self.correction('email', reason=reason)
        with self.assertRaises(FulfillmentError):
            self.correction('email', confirmed=False)
        with self.assertRaises(FulfillmentError):
            self.correction('email', values=self.order.customer_email.upper())
        with self.assertRaises(FulfillmentError):
            self.correction('address', values=deepcopy(self.order.shipping_details))
        self.assertFalse(Fulfillment.objects.exists())
        self.assertFalse(FulfillmentEvent.objects.exists())

    def test_email_validation_double_entry_and_unknown_kind_cannot_write(self):
        for value in ('', 'not-an-email', 'bad\naddress@example.test'):
            with self.subTest(email=value):
                with self.assertRaises(FulfillmentError):
                    self.correction('email', values=value)
        response = self.client.post(self.contacts_url('email'), self.email_post(email_confirmation='different@example.test'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Fulfillment.objects.exists())
        self.assertEqual(self.client.post(self.contacts_url(), {'kind': 'bank-account', 'revision': 0}).status_code, 400)
        self.assertFalse(Fulfillment.objects.exists())

    def test_address_fields_must_be_complete_text_with_a_known_country(self):
        variants = []
        for key in ('line1', 'postal_code', 'city', 'country'):
            values = deepcopy(CORRECTED_SHIPPING)
            values['address'][key] = ''
            variants.append(values)
        values = deepcopy(CORRECTED_SHIPPING)
        values['name'] = ''
        variants.append(values)
        values = deepcopy(CORRECTED_SHIPPING)
        values['address']['city'] = {'nested': 'not text'}
        variants.append(values)
        values = deepcopy(CORRECTED_SHIPPING)
        values['address']['country'] = 'ZZ'
        variants.append(values)
        for values in variants:
            with self.subTest(values=values):
                with self.assertRaises(FulfillmentError):
                    self.correction('address', values=values)
        self.assertFalse(Fulfillment.objects.exists())

    def test_original_country_cannot_change_and_server_rejects_forged_country(self):
        values = deepcopy(CORRECTED_SHIPPING)
        values['address']['country'] = 'BE'
        with self.assertRaises(FulfillmentError):
            self.correction('address', values=values)
        response = self.client.post(self.contacts_url(), self.address_post(country='BE'))
        self.assertRedirects(response, self.detail_url(), fetch_redirect_response=False)
        self.assertEqual(effective_shipping(self.current_order())['address']['country'], 'FR')
        self.assertEqual(self.current_order().shipping_details['address']['country'], 'FR')

    def test_paid_shipping_snapshot_country_has_priority_over_legacy_address_country(self):
        order = self.current_order()
        order.shipping_snapshot = {'managed': True, 'country': 'FR'}
        order.shipping_details['address']['country'] = 'BE'
        order.save(update_fields=['shipping_snapshot', 'shipping_details'])
        forbidden = deepcopy(CORRECTED_SHIPPING)
        forbidden['address']['country'] = 'BE'
        with self.assertRaises(FulfillmentError):
            self.correction('address', values=forbidden)
        self.correction('address')
        self.assertEqual(effective_shipping(self.current_order())['address']['country'], 'FR')
        self.assertEqual(self.current_order().shipping_details['address']['country'], 'BE')

    def test_unknown_original_country_can_be_completed_with_supported_iso_code(self):
        order = self.current_order()
        order.shipping_details['address']['country'] = ''
        order.save(update_fields=['shipping_details'])
        completed = deepcopy(CORRECTED_SHIPPING)
        completed['address']['country'] = 'BE'
        self.correction('address', values=completed)
        self.assertEqual(effective_shipping(self.current_order())['address']['country'], 'BE')
        self.assertEqual(self.current_order().shipping_details['address']['country'], '')

    def test_pending_expired_and_production_demo_orders_cannot_be_corrected(self):
        for status in ('pending', 'expired'):
            self.order.status = status
            self.order.save(update_fields=['status'])
            with self.subTest(status=status):
                with self.assertRaises(FulfillmentError):
                    self.correction('email')
                self.assertEqual(self.client.get(self.contacts_url()).status_code, 404)
        self.order.status = 'demo'
        self.order.save(update_fields=['status'])
        with override_settings(DEBUG=False):
            with self.assertRaises(FulfillmentError):
                self.correction('email')
            self.assertEqual(self.client.get(self.contacts_url()).status_code, 404)
        self.assertFalse(Fulfillment.objects.exists())

    def test_correction_after_ready_reopens_preparation_and_supersedes_pending_notice(self):
        self.ready(notify_customer=True)
        notice = ReturnMail.objects.get(order=self.order)
        result = self.correction('email')
        self.assertEqual((result.state, result.revision), ('preparing', 2))
        self.assertIsNone(result.ready_at)
        self.assertIsNone(result.shipped_at)
        notice.refresh_from_db()
        self.assertIsNotNone(notice.superseded_at)
        self.assertEqual(notice.payload['recipient'], self.order.customer_email)
        self.assertEqual(ReturnMail.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 0)
        with self.assertRaises(FulfillmentError):
            self.ship()

    def test_neither_address_nor_email_can_be_corrected_after_actual_departure(self):
        self.ready()
        self.ship()
        before = (self.info()['revision'], FulfillmentEvent.objects.count(), ReturnMail.objects.count())
        for kind in ('address', 'email'):
            with self.subTest(kind=kind):
                with self.assertRaises(FulfillmentError):
                    self.correction(kind)
        self.assertEqual((self.info()['revision'], FulfillmentEvent.objects.count(), ReturnMail.objects.count()), before)
        self.assertEqual(effective_email(self.current_order()), self.order.customer_email)

    def test_packing_slip_uses_corrected_address_while_order_and_issued_invoice_stay_historical(self):
        original = self.purchase_snapshot()
        purchase_shipping = build_preview(self.current_order())['shipping']
        invoice_snapshot = {'shipping': deepcopy(self.order.shipping_details),
                            'buyer': {'email': self.order.customer_email}, 'historical': True}
        invoice = Invoice.objects.create(order=self.order, number='WEB-TEST-CONTACTS',
                                         issued_at=timezone.now(), snapshot=invoice_snapshot)
        self.correction('address')
        self.correction('email')
        fresh = self.current_order()
        self.assertEqual(build_delivery_slip(fresh)['shipping'], CORRECTED_SHIPPING)
        self.assertEqual(build_preview(fresh)['shipping'], purchase_shipping)
        invoice.refresh_from_db()
        self.assertEqual(invoice.snapshot, invoice_snapshot)
        self.assert_untouched_purchase_and_stock(original)
        response = self.client.get(reverse('facturation:delivery_slip', args=[self.order.pk]))
        self.assertContains(response, CORRECTED_SHIPPING['address']['line1'])
        self.assertNotContains(response, PRIVATE_REASON)

    def test_contact_audit_keeps_effective_before_after_without_rewriting_original_values(self):
        self.correction('address')
        self.correction('email')
        event = FulfillmentEvent.objects.order_by('pk').last()
        snapshot = event.contact_snapshot
        self.assertEqual(snapshot['before']['shipping'], CORRECTED_SHIPPING)
        self.assertEqual(snapshot['before']['email'], self.order.customer_email)
        self.assertEqual(snapshot['after']['shipping'], CORRECTED_SHIPPING)
        self.assertEqual(snapshot['after']['email'], CORRECTED_EMAIL)
        self.assertIn(PRIVATE_REASON, event.note)
        fulfillment = Fulfillment.objects.get(order=self.order)
        quote = connection.ops.quote_name
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT delivery_address_override, notification_email_override '
                           f'FROM {quote(Fulfillment._meta.db_table)} WHERE id = %s', [fulfillment.pk])
            stored_address, stored_email = cursor.fetchone()
            self.assertNotIn(CORRECTED_SHIPPING['address']['line1'], str(stored_address))
            self.assertNotIn(CORRECTED_EMAIL, str(stored_email))
            cursor.execute(f'SELECT contact_snapshot FROM {quote(FulfillmentEvent._meta.db_table)} WHERE id = %s', [event.pk])
            self.assertNotIn(CORRECTED_EMAIL, str(cursor.fetchone()[0]))

    @override_settings(EMAIL_DELIVERY_ENABLED=True)
    def test_correction_does_not_redirect_old_notice_and_future_dispatch_uses_effective_email(self):
        self.ready(notify_customer=True)
        original_notice = ReturnMail.objects.get(order=self.order)
        self.correction('email')
        self.assertEqual(deliver_pending(order_id=self.order.pk), 0)
        self.assertEqual(len(mail.outbox), 0)
        self.ready()
        self.ship()
        self.assertEqual(deliver_pending(order_id=self.order.pk), 1)
        self.assertEqual(mail.outbox[0].to, [CORRECTED_EMAIL])
        self.assertIn(TRACKING, mail.outbox[0].body)
        original_notice.refresh_from_db()
        self.assertEqual(original_notice.payload['recipient'], self.order.customer_email)
        self.assertIsNone(original_notice.sent_at)

    def test_withdrawal_acknowledgement_keeps_original_purchase_email_after_shipping_correction(self):
        self.correction('email')
        record = submit(self.order.pk, {0: 1}, uuid.uuid4())
        notice = ReturnMail.objects.get(request=record)
        self.assertEqual(notice.payload['kind'], 'return_requested')
        self.assertEqual(notice.payload['recipient'], self.order.customer_email)
        self.assertNotEqual(notice.payload['recipient'], CORRECTED_EMAIL)

    def test_customer_projection_contains_no_correction_snapshot_reason_or_staff_identity(self):
        self.correction('address')
        self.correction('email')
        projected = public_status(self.current_order())
        for forbidden in ('contact_snapshot', 'events', 'note', 'reason', 'actor', 'notice'):
            self.assertNotIn(forbidden, projected)
        self.assertNotIn(PRIVATE_REASON, str(projected))
        self.client.force_login(self.customer, backend=LOGIN_BACKEND)
        response = self.client.get(reverse('espace:order', args=[self.order.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, PRIVATE_REASON)
        self.assertNotContains(response, self.seller.username)

    def test_privacy_export_has_own_effective_contacts_and_safe_before_after_history(self):
        from espace.privacy import subject_export
        self.correction('address')
        self.correction('email')
        foreign = Order.objects.create(user=self.other, session_key='foreign-contact-export',
                                       customer_email=self.customer.email, status='paid',
                                       items=deepcopy(self.order.items), subtotal=self.order.subtotal,
                                       shipping_details=deepcopy(self.order.shipping_details))
        correct_contacts(foreign.pk, self.seller, 0, 'email', 'foreign-private@example.test', PRIVATE_REASON, True)
        data = subject_export(self.customer)
        self.assertEqual([order['reference'] for order in data['commandes']], [str(self.order.pk)])
        contact = data['commandes'][0]['coordonnees_expedition']
        self.assertEqual(contact['livraison'], CORRECTED_SHIPPING)
        self.assertEqual(contact['email_avis'], CORRECTED_EMAIL)
        self.assertEqual(len(contact['historique']), 2)
        self.assertEqual(contact['historique'][-1]['after']['email'], CORRECTED_EMAIL)
        for private in (PRIVATE_REASON, self.seller.username, 'foreign-private@example.test', str(foreign.pk)):
            self.assertNotIn(private, str(data))

    def test_posted_foreign_order_actor_and_purchase_values_are_ignored(self):
        foreign = Order.objects.create(user=self.other, session_key='foreign-contact-post',
                                       customer_email='foreign@example.test', status='paid', items=[], subtotal=0)
        data = self.email_post(order=str(foreign.pk), actor=self.other.pk, user=self.other.pk,
                               customer_email=self.other.email, total_paid=1, state='shipped')
        response = self.client.post(self.contacts_url('email'), data)
        self.assertRedirects(response, self.detail_url(), fetch_redirect_response=False)
        self.assertEqual(effective_email(self.current_order()), CORRECTED_EMAIL)
        self.assertFalse(Fulfillment.objects.filter(order=foreign).exists())
        self.assertEqual(self.current_order().customer_email, 'purchase@example.test')
        self.assertEqual(self.current_order().total_paid, 7800)
        self.assertEqual(FulfillmentEvent.objects.get().actor_id, self.seller.pk)

    def test_address_blocker_links_to_address_correction_and_is_resolved_by_effective_address(self):
        self.order.shipping_details = {}
        self.order.save(update_fields=['shipping_details'])
        response = self.client.get(self.detail_url())
        shipment = response.context['shipment']
        self.assertEqual(shipment['blocked_kind'], 'address')
        self.assertEqual(shipment['resolution_url'], self.contacts_url('address'))
        self.assertContains(response, 'href="' + self.contacts_url('address') + '"')
        self.correction('address')
        shipment = self.client.get(self.detail_url()).context['shipment']
        self.assertFalse(shipment['blocked_reason'])
        self.assertTrue(shipment['can_ready'])
        self.ready()
        self.ship()
        self.assertEqual(self.info()['state'], 'shipped')
        self.assertEqual(self.current_order().shipping_details, {})

    def test_email_blocker_links_to_email_correction_then_requires_rechecking_preparation(self):
        self.ready()
        self.order.customer_email = ''
        self.order.save(update_fields=['customer_email'])
        response = self.client.get(self.detail_url())
        shipment = response.context['shipment']
        self.assertEqual(shipment['blocked_kind'], 'email')
        self.assertEqual(shipment['resolution_url'], self.contacts_url('email'))
        self.assertFalse(shipment['can_ship'])
        self.correction('email')
        self.assertEqual(self.info()['state'], 'preparing')
        self.ready()
        self.ship()
        self.assertEqual(self.info()['state'], 'shipped')
        self.assertEqual(self.current_order().customer_email, '')

    def test_return_blocker_links_to_exact_case_and_contact_correction_does_not_bypass_it(self):
        case = ReturnRequest.objects.create(order=self.order, submission_id=uuid.uuid4(), amount=3600)
        foreign = Order.objects.create(user=self.other, session_key='foreign-return-blocker',
                                       status='paid', items=[], subtotal=0)
        other_case = ReturnRequest.objects.create(order=foreign, submission_id=uuid.uuid4(), amount=1)
        self.order.shipping_details = {}
        self.order.save(update_fields=['shipping_details'])
        response = self.client.get(self.detail_url())
        shipment = response.context['shipment']
        target = reverse('retours:manage_detail', args=[case.pk])
        self.assertEqual(shipment['blocked_kind'], 'return')
        self.assertEqual(shipment['resolution_url'], target)
        self.assertNotEqual(shipment['resolution_url'], reverse('retours:manage_detail', args=[other_case.pk]))
        self.correction('address')
        with self.assertRaises(FulfillmentError):
            self.ready()
        self.assertEqual(self.client.get(self.detail_url()).context['shipment']['resolution_url'], target)

    def test_item_blocker_links_to_purchase_document_without_proposing_contact_correction(self):
        self.order.items = [{'name': '', 'quantity': 1, 'unit_amount': 3600}]
        self.order.save(update_fields=['items'])
        response = self.client.get(self.detail_url())
        shipment = response.context['shipment']
        self.assertEqual(shipment['blocked_kind'], 'items')
        self.assertEqual(shipment['resolution_url'], reverse('facturation:purchase_order', args=[self.order.pk]))
        self.assertFalse(shipment['can_ready'])

    @override_settings(EMAIL_DELIVERY_ENABLED=True)
    def test_worker_does_not_redirect_queued_notice_if_effective_email_no_longer_matches(self):
        self.correction('email')
        self.ready()
        self.ship()
        notice = ReturnMail.objects.get(order=self.order)
        fulfillment = Fulfillment.objects.get(order=self.order)
        fulfillment.notification_email_override = 'requires-review@example.test'
        fulfillment.save(update_fields=['notification_email_override'])
        with patch('courriels.messages.EmailMultiAlternatives.send') as send:
            self.assertEqual(deliver_pending(order_id=self.order.pk), 0)
            send.assert_not_called()
        notice.refresh_from_db()
        self.assertEqual(notice.payload['recipient'], CORRECTED_EMAIL)
        self.assertIsNone(notice.sent_at)
        self.assertIsNotNone(notice.last_attempt_at)
        self.assertEqual(notice.attempts, 0)
