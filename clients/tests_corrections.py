import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from connexion.test_helpers import verify_test_user
from paiement.models import Order
from .corrections import SupportCorrectionForm, correct_case, correction_orders
from .models import SupportCase, SupportEvent
from .selectors import get_customer
from .services import SupportConflict, create_case, update_case


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=False,
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class SupportCorrectionTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user('correction-seller', email='seller@example.test', is_staff=True)
        self.manager.user_permissions.add(Permission.objects.get(codename='manage_returns'))
        verify_test_user(self.manager)
        self.customer = get_user_model().objects.create_user('correction-customer', email='customer@example.test')
        self.other = get_user_model().objects.create_user('correction-other', email='other@example.test')

    def order(self, *, user=None, email='guest@example.test', **extra):
        values = {'user': user, 'customer_email': email, 'session_key': 'correction-order',
                  'subtotal': 8000, 'items': [], 'status': 'paid', 'total_paid': 8000,
                  'paid_at': timezone.now()}
        values.update(extra)
        return Order.objects.create(**values)

    def case(self, *, user=None, order=None, email='guest@example.test', contact_case=None):
        return create_case(self.manager, {
            'creation_key': uuid.uuid4(), 'customer_name': 'Camille Martin', 'customer_email': email,
            'phone': '06 12 34 56 78', 'subject': 'Suivi demandé par téléphone',
            'description': 'Le client souhaite connaître le délai de livraison.', 'channel': 'phone',
            'category': 'delivery', 'priority': 'urgent', 'due_date': None,
        }, user=user, order=order, contact_case=contact_case)[0]

    def data(self, case, **changes):
        values = {name: getattr(case, name) for name in (
            'revision', 'customer_name', 'phone', 'subject', 'description', 'channel', 'category')}
        values.update(related_order=str(case.order_id) if case.order_id else '', reason='Précisions reçues pendant le rappel.')
        values.update(changes)
        return values

    def login(self, user):
        self.client.force_login(user, backend='connexion.backends.EmailOrUsernameBackend')

    def url(self, case):
        return reverse('clients:case_edit', args=[case.pk])

    def test_correct_contact_and_request_keeps_identity_tracking_and_audit(self):
        order = self.order(user=self.customer, email='historical@example.test')
        case = self.case(user=self.customer, order=order, email=self.customer.email)
        before_order = Order.objects.values().get(pk=order.pk)
        before_identity = (case.user_id, case.customer_email, case.customer_email_hash,
                           case.state, case.priority, case.due_date, case.creation_fingerprint)
        corrected = correct_case(self.manager, case.pk, self.data(case,
            customer_name='Camille Dupont', phone='06 22 33 44 55', subject='Demande précisée',
            description='La question porte sur le produit reçu.', channel='email', category='product'))
        self.assertEqual((corrected.customer_name, corrected.phone, corrected.subject),
                         ('Camille Dupont', '06 22 33 44 55', 'Demande précisée'))
        self.assertEqual(corrected.revision, 2)
        self.assertEqual((corrected.user_id, corrected.customer_email, corrected.customer_email_hash,
                          corrected.state, corrected.priority, corrected.due_date, corrected.creation_fingerprint), before_identity)
        self.assertEqual(Order.objects.values().get(pk=order.pk), before_order)
        event = corrected.events.first()
        self.assertEqual(event.kind, 'update')
        self.assertEqual(event.actor_id, self.manager.pk)
        self.assertEqual((event.previous_state, event.state), ('open', 'open'))
        self.assertEqual(event.changes['phone']['before'], '06 12 34 56 78')
        self.assertEqual(event.changes['phone']['after'], '06 22 33 44 55')
        self.assertEqual(event.changes['description']['before'], case.description)
        self.assertIn('Précisions reçues pendant le rappel.', event.note)
        self.assertEqual(len(mail.outbox), 0)

    def test_add_change_and_remove_order_for_same_account(self):
        first = self.order(user=self.customer, email='old@example.test')
        second = self.order(user=self.customer, email=self.customer.email)
        case = self.case(user=self.customer, email=self.customer.email)
        original_key = get_customer(f'c-{case.pk}').key
        for selected in (first, second, None):
            case = correct_case(self.manager, case.pk, self.data(case, related_order=str(selected.pk) if selected else ''))
            self.assertEqual(case.order_id, selected.pk if selected else None)
            self.assertEqual(case.user_id, self.customer.pk)
            self.assertEqual(get_customer(f'c-{case.pk}').key, original_key)
        self.assertEqual(case.events.count(), 4)

    def test_guest_order_change_and_removal_preserve_group_and_sibling_cases(self):
        first = self.order()
        second = self.order()
        case = self.case(order=first)
        sibling = self.case()
        original_key = get_customer(f'c-{case.pk}').key
        case = correct_case(self.manager, case.pk, self.data(case, related_order=str(second.pk)))
        self.assertEqual(case.contact_reference, first.pk)
        case = correct_case(self.manager, case.pk, self.data(case, related_order=''))
        self.assertEqual(case.contact_reference, first.pk)
        customer = get_customer(f'c-{case.pk}')
        self.assertEqual(customer.key, original_key)
        self.assertCountEqual(customer.order_ids, [first.pk, second.pk])
        self.assertCountEqual(customer.case_ids, [case.pk, sibling.pk])

    def test_phone_only_guest_order_removal_retains_original_profile(self):
        order = self.order(email='')
        case = self.case(order=order, email='')
        original_key = get_customer(f'c-{case.pk}').key
        case = correct_case(self.manager, case.pk, self.data(case, related_order=''))
        self.assertIsNone(case.order_id)
        self.assertEqual(case.contact_reference, order.pk)
        self.assertEqual(get_customer(f'c-{case.pk}').key, original_key)
        self.assertEqual(get_customer(f'c-{case.pk}').order_ids, [order.pk])

    def test_phone_contact_siblings_stay_grouped_after_name_and_phone_correction(self):
        root = self.case(email='')
        second = self.case(email='', contact_case=root)
        before_key = get_customer(f'c-{root.pk}').key
        corrected = correct_case(self.manager, second.pk, self.data(second,
            phone='06 99 88 77 66', customer_name='Camille Dupont'))
        self.assertEqual(corrected.contact_reference, root.pk)
        self.assertEqual(get_customer(f'c-{corrected.pk}').key, before_key)
        self.assertCountEqual(get_customer(f'c-{root.pk}').case_ids, [root.pk, second.pk])

    def test_existing_canonical_contact_anchor_is_retained(self):
        order = self.order()
        root = self.case()
        case = self.case(contact_case=root)
        case = correct_case(self.manager, case.pk, self.data(case, related_order=str(order.pk)))
        case = correct_case(self.manager, case.pk, self.data(case, related_order=''))
        self.assertEqual(case.contact_reference, root.pk)
        self.assertCountEqual(get_customer(f'c-{case.pk}').case_ids, [root.pk, case.pk])

    def test_rejects_other_customer_order_even_with_same_email_snapshot(self):
        order = self.order(user=self.customer, email=self.customer.email)
        other_order = self.order(user=self.other, email=self.customer.email)
        case = self.case(user=self.customer, order=order, email=self.customer.email)
        with self.assertRaises(ValidationError):
            correct_case(self.manager, case.pk, self.data(case, related_order=str(other_order.pk)))
        case.refresh_from_db()
        self.assertEqual((case.order_id, case.revision, case.events.count()), (order.pk, 1, 1))

    def test_service_revalidates_membership_after_related_order_changes(self):
        order = self.order(user=self.customer, email=self.customer.email)
        case = self.case(user=self.customer, email=self.customer.email)
        real_get_customer = get_customer
        reads = 0

        def changed_customer(key):
            nonlocal reads
            reads += 1
            if reads == 2:
                Order.objects.filter(pk=order.pk).update(user=self.other)
            return real_get_customer(key)

        with patch('clients.corrections.get_customer', side_effect=changed_customer):
            with self.assertRaises(SupportConflict):
                correct_case(self.manager, case.pk, self.data(case, related_order=str(order.pk)))
        case.refresh_from_db()
        self.assertIsNone(case.order_id)
        self.assertEqual((case.revision, case.events.count()), (1, 1))

    def test_revision_conflict_preserves_latest_information(self):
        case = self.case()
        stale = self.data(case, phone='06 99 88 77 66')
        current = update_case(self.manager, case.pk, {'revision': 1, 'state': 'in_progress',
            'priority': 'normal', 'due_date': None, 'note': 'Le dernier appel a été pris en compte.'})
        with self.assertRaises(SupportConflict):
            correct_case(self.manager, case.pk, stale)
        current.refresh_from_db()
        self.assertEqual((current.phone, current.state, current.revision), ('06 12 34 56 78', 'in_progress', 2))

    def test_rejects_empty_reason_invalid_fields_and_identity_tampering(self):
        case = self.case()
        invalid = ({'reason': '   '}, {'reason': 'X' * 1001}, {'customer_name': ' '},
                   {'subject': 'X' * 161}, {'description': 'X' * 5001}, {'phone': 'bad-number'},
                   {'channel': 'sms'}, {'category': 'refund'}, {'related_order': 'invalid'},
                   {'customer_email': self.other.email}, {'user_id': self.other.pk},
                   {'contact_reference': uuid.uuid4()}, {'state': 'resolved'})
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                correct_case(self.manager, case.pk, self.data(case, **changes))
        case.refresh_from_db()
        self.assertEqual((case.revision, case.events.count()), (1, 1))

    def test_cannot_remove_only_contact_phone(self):
        case = self.case(email='')
        with self.assertRaises(ValidationError):
            correct_case(self.manager, case.pk, self.data(case, phone=''))

    def test_noop_has_no_duplicate_audit_and_resolved_case_stays_resolved(self):
        case = self.case()
        no_change = correct_case(self.manager, case.pk, self.data(case))
        self.assertEqual((no_change.revision, no_change.updated_at, no_change.events.count()), (1, case.updated_at, 1))
        resolved = update_case(self.manager, case.pk, {'revision': 1, 'state': 'resolved',
            'priority': case.priority, 'due_date': None, 'note': 'Le client a reçu la réponse.'})
        corrected = correct_case(self.manager, resolved.pk, self.data(resolved, subject='Objet corrigé'))
        self.assertEqual((corrected.state, corrected.resolved_at), ('resolved', resolved.resolved_at))

    def test_audit_failure_rolls_back_correction_and_relationship(self):
        order = self.order()
        case = self.case(order=order)
        with patch('clients.corrections.SupportEvent.objects.create', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                correct_case(self.manager, case.pk, self.data(case, phone='06 99 88 77 66', related_order=''))
        case.refresh_from_db()
        self.assertEqual((case.phone, case.order_id, case.contact_reference, case.revision),
                         ('06 12 34 56 78', order.pk, None, 1))

    def test_raw_database_encrypts_original_and_corrected_values(self):
        case = self.case()
        case = correct_case(self.manager, case.pk, self.data(case, phone='06 99 88 77 66', description='Une précision confidentielle.'))
        event = case.events.first()
        with connection.cursor() as cursor:
            cursor.execute('SELECT note, changes FROM clients_supportevent WHERE id = %s', [event.pk])
            raw = cursor.fetchone()
        self.assertTrue(all(value.startswith('enc:v1:') for value in raw))
        self.assertNotIn('confidentielle', ' '.join(raw))
        self.assertNotIn('06 99', ' '.join(raw))

    def test_services_require_existing_manager_permission_and_verified_email(self):
        case = self.case()
        staff = get_user_model().objects.create_user('correction-staff', is_staff=True, email='staff@example.test')
        unverified = get_user_model().objects.create_superuser('correction-unverified', email='unverified@example.test', password='test')
        for actor in (None, self.customer, staff, unverified):
            with self.subTest(actor=actor), self.assertRaises(PermissionDenied):
                correct_case(actor, case.pk, self.data(case, subject='Interdit'))

    def test_get_edit_page_is_private_and_contains_expected_fields(self):
        case = self.case(user=self.customer, email=self.customer.email)
        self.login(self.manager)
        response = self.client.get(self.url(case))
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')
        self.assertContains(response, 'Corriger la demande.')
        self.assertContains(response, self.customer.email)
        self.assertNotIn('customer_email', response.context['form'].fields)
        self.assertEqual(set(response.context['form'].fields), set(SupportCorrectionForm.base_fields))
        self.assertEqual((SupportCase.objects.count(), SupportEvent.objects.count()), (1, 1))

    def test_view_requires_manager_and_csrf_for_writes(self):
        case = self.case()
        self.assertEqual(self.client.get(self.url(case)).status_code, 302)
        self.login(self.customer)
        self.assertEqual(self.client.get(self.url(case)).status_code, 403)
        self.assertEqual(self.client.post(self.url(case), self.data(case, subject='Interdit')).status_code, 403)
        self.login(self.manager)
        self.client.handler.enforce_csrf_checks = True
        self.assertEqual(self.client.post(self.url(case), self.data(case, subject='Sans jeton')).status_code, 403)
        case.refresh_from_db()
        self.assertEqual(case.revision, 1)

    def test_view_post_success_and_escaped_error_preserve_entered_text(self):
        case = self.case()
        self.login(self.manager)
        response = self.client.post(self.url(case), self.data(case, phone='06 99 88 77 66'))
        self.assertRedirects(response, reverse('clients:case_detail', args=[case.pk]), fetch_redirect_response=False)
        case.refresh_from_db()
        response = self.client.post(self.url(case), self.data(case, subject='<script>test</script>', reason=''))
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, '&lt;script&gt;test&lt;/script&gt;', status_code=400)
        self.assertNotContains(response, '<script>test</script>', status_code=400)
        self.assertEqual(SupportCase.objects.get(pk=case.pk).revision, 2)

    def test_view_stale_revision_keeps_correction_on_screen(self):
        case = self.case()
        self.login(self.manager)
        stale = self.data(case, phone='06 99 88 77 66')
        correct_case(self.manager, case.pk, self.data(case, subject='Nouvel objet'))
        response = self.client.post(self.url(case), stale)
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, '06 99 88 77 66', status_code=409)
        self.assertContains(response, 'depuis votre ouverture', status_code=409)
