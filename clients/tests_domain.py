import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from config.encrypted_fields import email_digest
from connexion.test_helpers import verify_test_user
from paiement.models import Order
from .forms import SupportCaseForm, SupportUpdateForm
from .models import SupportCase, SupportEvent
from .services import SupportConflict, SupportError, create_case, update_case


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=False,
                   EMAIL_DELIVERY_ENABLED=False,
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class SupportDomainTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.manager = User.objects.create_user('sav-manager', email='vendeuse@example.test', is_staff=True)
        self.manager.user_permissions.add(Permission.objects.get(content_type__app_label='retours', codename='manage_returns'))
        verify_test_user(self.manager)
        self.customer = User.objects.create_user('sav-client', first_name='Élodie', last_name='Martin', email='client@example.test')
        self.other = User.objects.create_user('sav-other', email='autre@example.test')
        self.order = Order.objects.create(user=self.customer, session_key='sav-test', items=[], subtotal=8000,
            status='paid', customer_email='ancien-client@example.test', total_paid=8000,
            shipping_details={'name': 'Élodie Martin', 'phone': '0612345678'})

    def data(self, **changes):
        values = {'creation_key': uuid.uuid4(), 'customer_name': 'Élodie Martin',
                  'customer_email': self.customer.email, 'phone': '06 12 34 56 78',
                  'subject': 'Question reçue par téléphone', 'description': 'Le client souhaite connaître le suivi du colis.',
                  'channel': 'phone', 'category': 'delivery', 'priority': 'normal', 'due_date': None}
        values.update(changes)
        return values

    def create(self, **changes):
        return create_case(self.manager, self.data(**changes), user=self.customer, order=self.order)[0]

    def update_data(self, case, **changes):
        values = {'revision': case.revision, 'state': case.state, 'priority': case.priority,
                  'due_date': case.due_date, 'note': ''}
        values.update(changes)
        return values

    def test_create_phone_case_records_explicit_identity_and_initial_event(self):
        before = dict(Order.objects.values().get(pk=self.order.pk))
        case, reused = create_case(self.manager, self.data(customer_name='Mme Martin'), user=self.customer, order=self.order)
        self.assertFalse(reused)
        self.assertEqual((case.user, case.order, case.state, case.revision), (self.customer, self.order, 'open', 1))
        self.assertEqual(case.customer_name, 'Mme Martin')
        self.assertEqual(case.customer_email_hash, email_digest(self.customer.email))
        self.assertRegex(case.reference, r'^SAV-[A-F0-9]{8}$')
        event = case.events.get()
        self.assertEqual((event.kind, event.actor, event.actor_label), ('created', self.manager, 'sav-manager'))
        self.assertEqual(event.summary, 'Dossier ouvert')
        self.assertEqual(before, dict(Order.objects.values().get(pk=self.order.pk)))

    def test_unknown_phone_customer_can_have_a_case_without_account_or_email(self):
        case, reused = create_case(self.manager, self.data(customer_email=''))
        self.assertFalse(reused)
        self.assertIsNone(case.user_id)
        self.assertIsNone(case.order_id)
        self.assertEqual(case.customer_email_hash, '')
        self.assertEqual(case.phone, '06 12 34 56 78')

    def test_explicit_phone_contact_keeps_successive_cases_together_without_matching(self):
        root = create_case(self.manager, self.data(customer_email=''))[0]
        before = (root.updated_at, root.revision, root.events.count())
        second = create_case(self.manager, self.data(customer_email='', subject='Deuxième appel'), contact_case=root)[0]
        third = create_case(self.manager, self.data(customer_email='callback@example.test', phone='06 22 33 44 55'),
                            contact_case=second)[0]
        unrelated = create_case(self.manager, self.data(customer_email=''))[0]
        self.assertEqual(second.contact_reference, root.pk)
        self.assertEqual(third.contact_reference, root.pk)
        self.assertIsNone(unrelated.contact_reference)
        self.assertEqual((second.user_id, second.order_id, third.user_id, third.order_id), (None, None, None, None))
        root.refresh_from_db()
        self.assertIsNone(root.contact_reference)
        self.assertEqual((root.updated_at, root.revision, root.events.count()), before)

    def test_explicit_unbound_email_contact_can_anchor_a_new_case(self):
        root = create_case(self.manager, self.data(customer_email='guest@example.test'))[0]
        second = create_case(self.manager, self.data(customer_email='', subject='Nouvelle demande'), contact_case=root)[0]
        self.assertEqual(second.contact_reference, root.pk)

    def test_contact_creation_replay_requires_same_explicit_source(self):
        root = create_case(self.manager, self.data(customer_email=''))[0]
        other_root = create_case(self.manager, self.data(customer_email=''))[0]
        data = self.data(customer_email='')
        created, reused = create_case(self.manager, data, contact_case=root)
        repeated, reused_again = create_case(self.manager, data, contact_case=root)
        self.assertFalse(reused)
        self.assertTrue(reused_again)
        self.assertEqual(created.pk, repeated.pk)
        for source in (None, other_root):
            with self.subTest(source=source), self.assertRaises(SupportConflict):
                create_case(self.manager, data, contact_case=source)
        self.assertEqual((SupportCase.objects.count(), SupportEvent.objects.count()), (3, 3))

    def test_contact_reference_rejects_account_or_order_sources_and_destinations(self):
        root = create_case(self.manager, self.data(customer_email=''))[0]
        account_case = self.create()
        guest_order = Order.objects.create(session_key='guest-contact', items=[], subtotal=1000,
                                           customer_email=self.customer.email)
        order_case = create_case(self.manager, self.data(), order=guest_order)[0]
        for source in (account_case, order_case):
            with self.subTest(source=source), self.assertRaises(ValidationError):
                create_case(self.manager, self.data(), contact_case=source)
        for target in ({'user': self.customer}, {'order': guest_order}):
            with self.subTest(target=target), self.assertRaises(ValidationError):
                create_case(self.manager, self.data(), contact_case=root, **target)
        self.assertEqual(SupportCase.objects.count(), 3)

    def test_contact_source_is_reread_and_unknown_references_are_rejected(self):
        root = create_case(self.manager, self.data(customer_email=''))[0]
        SupportCase.objects.filter(pk=root.pk).update(user=self.customer)
        self.assertIsNone(root.user_id)
        for source in (root, uuid.uuid4(), 'invalid'):
            with self.subTest(source=source), self.assertRaises(ValidationError):
                create_case(self.manager, self.data(customer_email=''), contact_case=source)
        self.assertEqual(SupportCase.objects.count(), 1)

    def test_contact_reference_cannot_be_supplied_in_form_or_changed_on_update(self):
        root = create_case(self.manager, self.data(customer_email=''))[0]
        with self.assertRaises(ValidationError):
            create_case(self.manager, self.data(customer_email='', contact_reference=root.pk))
        second = create_case(self.manager, self.data(customer_email=''), contact_case=root)[0]
        with self.assertRaises(ValidationError):
            update_case(self.manager, second.pk, {**self.update_data(second), 'contact_reference': uuid.uuid4()})
        second.refresh_from_db()
        self.assertEqual(second.contact_reference, root.pk)
        self.assertEqual(second.revision, 1)

    def test_case_requires_usable_contact_and_nonempty_request(self):
        for changes in ({'phone': '', 'customer_email': ''}, {'phone': 'bonjour', 'customer_email': ''},
                        {'subject': '   '}, {'description': '\n  '}, {'customer_name': ' '}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                create_case(self.manager, self.data(**changes))
        self.assertFalse(SupportCase.objects.exists())

    def test_email_is_normalized_and_phone_only_account_is_supported(self):
        case = create_case(self.manager, self.data(customer_email=' CLIENT@EXAMPLE.TEST '), user=self.customer)[0]
        self.assertEqual(case.customer_email, 'client@example.test')
        self.customer.email = ''
        self.customer.save(update_fields=['email'])
        other_case = create_case(self.manager, self.data(customer_email=''), user=self.customer)[0]
        self.assertEqual(other_case.user_id, self.customer.pk)
        self.assertEqual(other_case.customer_email_hash, '')

    def test_service_refuses_overlong_invalid_and_unknown_fields(self):
        invalid = ({'customer_name': 'N' * 151}, {'customer_email': 'not an email'},
                   {'subject': 'S' * 161}, {'description': 'D' * 5001}, {'phone': '1' * 41},
                   {'channel': 'carrier'}, {'category': 'refund-now'}, {'priority': 'highest'},
                   {'creation_key': 'wrong'}, {'state': 'resolved'}, {'user_id': self.other.pk})
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                create_case(self.manager, self.data(**changes))
        self.assertFalse(SupportCase.objects.exists())

    def test_creation_replay_returns_one_case_and_one_event(self):
        data = self.data()
        first, initial_reused = create_case(self.manager, data, user=self.customer, order=self.order)
        second, repeated = create_case(self.manager, data, user=self.customer, order=self.order)
        self.assertFalse(initial_reused)
        self.assertTrue(repeated)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual((SupportCase.objects.count(), SupportEvent.objects.count()), (1, 1))

    def test_creation_replay_with_changed_content_or_identity_is_rejected(self):
        data = self.data()
        create_case(self.manager, data, user=self.customer, order=self.order)
        for changes in ({'description': 'Une autre demande'}, {'subject': 'Autre objet'}):
            with self.subTest(changes=changes), self.assertRaises(SupportConflict):
                create_case(self.manager, {**data, **changes}, user=self.customer, order=self.order)
        with self.assertRaises(SupportConflict):
            create_case(self.manager, data, user=self.customer)
        self.assertEqual((SupportCase.objects.count(), SupportEvent.objects.count()), (1, 1))

    def test_another_manager_cannot_reuse_creation_key(self):
        other = get_user_model().objects.create_superuser('another-sav-manager', email='other-manager@example.test', password='test-only')
        verify_test_user(other)
        data = self.data()
        create_case(self.manager, data, user=self.customer)
        with self.assertRaises(SupportConflict):
            create_case(other, data, user=self.customer)

    def test_account_email_is_checked_against_current_database_value(self):
        self.customer.email = 'nouvelle@example.test'
        self.customer.save(update_fields=['email'])
        with self.assertRaises(SupportConflict):
            create_case(self.manager, self.data(customer_email='client@example.test'), user=self.customer)
        self.assertFalse(SupportCase.objects.exists())

    def test_owned_order_requires_its_explicit_account(self):
        with self.assertRaises(SupportError):
            create_case(self.manager, self.data(), order=self.order)
        with self.assertRaises(SupportError):
            create_case(self.manager, self.data(customer_email=self.other.email), user=self.other, order=self.order)
        self.assertFalse(SupportCase.objects.exists())

    def test_guest_order_must_match_email_and_may_be_linked_to_matching_account(self):
        guest = Order.objects.create(session_key='guest-sav', items=[], subtotal=1000, customer_email=self.customer.email)
        with self.assertRaises(SupportError):
            create_case(self.manager, self.data(customer_email='wrong@example.test'), order=guest)
        case = create_case(self.manager, self.data(), user=self.customer, order=guest)[0]
        self.assertEqual((case.user_id, case.order_id), (self.customer.pk, guest.pk))
        guest.refresh_from_db()
        self.assertIsNone(guest.user_id)

    def test_guest_order_without_email_can_be_explicitly_anchored(self):
        guest = Order.objects.create(session_key='guest-no-email', items=[], subtotal=1000)
        case = create_case(self.manager, self.data(customer_email=''), order=guest)[0]
        self.assertEqual(case.order_id, guest.pk)
        self.assertIsNone(case.user_id)

    def test_missing_or_invalid_selected_records_do_not_create_a_case(self):
        for args in ({'user': 99999999}, {'user': 'other'}, {'order': uuid.uuid4()}, {'order': 'invalid'}):
            with self.subTest(args=args), self.assertRaises(ValidationError):
                create_case(self.manager, self.data(), **args)
        self.assertFalse(SupportCase.objects.exists())

    def test_management_permissions_and_verified_access_are_checked_by_service(self):
        case = self.create()
        staff = get_user_model().objects.create_user('sav-staff', email='staff@example.test', is_staff=True)
        verify_test_user(staff)
        unverified = get_user_model().objects.create_superuser('sav-unverified', email='unverified@example.test', password='test-only')
        for actor in (None, self.customer, staff, unverified):
            with self.subTest(actor=actor), self.assertRaises(PermissionDenied):
                create_case(actor, self.data())
            with self.subTest(update_actor=actor), self.assertRaises(PermissionDenied):
                update_case(actor, case.pk, self.update_data(case, note='Ne doit pas être écrit'))
        self.assertEqual(SupportCase.objects.count(), 1)
        self.assertEqual(SupportEvent.objects.count(), 1)

    def test_private_fields_and_notes_are_encrypted_in_database(self):
        case = self.create()
        case = update_case(self.manager, case.pk, self.update_data(case, note='Note interne confidentielle'))
        with connection.cursor() as cursor:
            cursor.execute('SELECT customer_name, customer_email, phone, subject, description FROM clients_supportcase WHERE id = %s',
                           [case.pk.hex if connection.vendor == 'sqlite' else case.pk])
            row = cursor.fetchone()
            cursor.execute('SELECT note, changes FROM clients_supportevent WHERE case_id = %s',
                           [case.pk.hex if connection.vendor == 'sqlite' else case.pk])
            events = cursor.fetchall()
        self.assertTrue(all(value.startswith('enc:v1:') for value in row))
        self.assertTrue(all(value.startswith('enc:v1:') for event in events for value in event))
        self.assertNotIn('Élodie', ' '.join(row))
        self.assertEqual(case.events.first().note, 'Note interne confidentielle')

    def test_update_records_changed_status_priority_due_date_and_note(self):
        case = self.create()
        due = timezone.localdate() + timedelta(days=2)
        case = update_case(self.manager, case.pk, self.update_data(case, state='in_progress', priority='urgent',
                           due_date=due, note='Transporteur contacté.'))
        self.assertEqual((case.state, case.priority, case.due_date, case.revision), ('in_progress', 'urgent', due, 2))
        event = case.events.first()
        self.assertEqual(event.previous_state, 'open')
        self.assertEqual(event.state, 'in_progress')
        self.assertEqual(set(event.changes), {'state', 'priority', 'due_date'})
        self.assertIn('À traiter → En cours', event.summary)
        self.assertIn('Normale → Urgente', event.summary)
        self.assertEqual(event.note, 'Transporteur contacté.')

    def test_note_only_adds_history_and_increments_revision(self):
        case = self.create()
        updated = update_case(self.manager, case.pk, self.update_data(case, note='Client rappelé.'))
        self.assertEqual((updated.revision, updated.state), (2, 'open'))
        event = updated.events.first()
        self.assertEqual((event.kind, event.summary, event.changes), ('note', 'Note ajoutée', {}))
        self.assertEqual(event.note, 'Client rappelé.')

    def test_noop_does_not_add_an_event_or_touch_date(self):
        case = self.create()
        updated = update_case(self.manager, case.pk, self.update_data(case))
        self.assertEqual((updated.revision, updated.updated_at), (case.revision, case.updated_at))
        self.assertEqual(updated.events.count(), 1)

    def test_resolution_requires_explanation_and_reopening_clears_resolution_date(self):
        case = self.create()
        with self.assertRaises(ValidationError):
            update_case(self.manager, case.pk, self.update_data(case, state='resolved'))
        resolved = update_case(self.manager, case.pk, self.update_data(case, state='resolved', note='Colis reçu, client satisfait.'))
        self.assertIsNotNone(resolved.resolved_at)
        date = resolved.resolved_at
        resolved = update_case(self.manager, case.pk, self.update_data(resolved, note='Compte rendu terminé.'))
        self.assertEqual(resolved.resolved_at, date)
        reopened = update_case(self.manager, case.pk, self.update_data(resolved, state='in_progress', note='Le client rappelle.'))
        self.assertIsNone(reopened.resolved_at)
        self.assertEqual(reopened.events.first().previous_state, 'resolved')

    def test_stale_revision_refuses_all_changes_and_keeps_entered_note_out_of_history(self):
        case = self.create()
        stale = self.update_data(case, state='resolved', note='Ancienne réponse')
        updated = update_case(self.manager, case.pk, self.update_data(case, priority='urgent', note='Dernier appel'))
        with self.assertRaises(SupportConflict):
            update_case(self.manager, case.pk, stale)
        updated.refresh_from_db()
        self.assertEqual((updated.state, updated.priority, updated.revision), ('open', 'urgent', 2))
        self.assertEqual(updated.events.count(), 2)

    def test_update_cannot_reassign_identity_or_replace_original_request(self):
        case = self.create()
        for extra in ({'user_id': self.other.pk}, {'customer_email': self.other.email}, {'phone': '0622222222'},
                      {'description': 'Texte remplacé'}, {'subject': 'Autre demande'}, {'order_id': uuid.uuid4()}):
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                update_case(self.manager, case.pk, {**self.update_data(case), **extra})
        case.refresh_from_db()
        self.assertEqual((case.user_id, case.customer_email, case.revision), (self.customer.pk, self.customer.email, 1))

    def test_update_validates_note_length_and_fields_on_server(self):
        case = self.create()
        for changes in ({'note': 'N' * 5001}, {'state': 'paid'}, {'priority': 'highest'}, {'revision': 0}, {'due_date': 'tomorrow'}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                update_case(self.manager, case.pk, self.update_data(case, **changes))
        self.assertEqual(case.events.count(), 1)

    def test_create_rolls_back_if_history_fails(self):
        with patch('clients.services.SupportEvent.objects.create', side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError):
                self.create()
        self.assertFalse(SupportCase.objects.exists())

    def test_update_rolls_back_if_history_fails(self):
        case = self.create()
        with patch('clients.services.SupportEvent.objects.create', side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError):
                update_case(self.manager, case.pk, self.update_data(case, state='resolved', note='Solution donnée'))
        case.refresh_from_db()
        self.assertEqual((case.state, case.revision, case.resolved_at), ('open', 1, None))
        self.assertEqual(case.events.count(), 1)

    def test_due_date_flag_ignores_today_future_and_closed_cases(self):
        case = self.create(due_date=timezone.localdate() - timedelta(days=1))
        self.assertTrue(case.is_overdue)
        case.due_date = timezone.localdate()
        self.assertFalse(case.is_overdue)
        case.due_date = timezone.localdate() - timedelta(days=1)
        case.state = 'resolved'
        self.assertFalse(case.is_overdue)

    def test_deleted_actor_keeps_auditable_username_and_customer_contact(self):
        case = self.create()
        self.manager.delete()
        case.refresh_from_db()
        event = case.events.get()
        self.assertIsNone(event.actor_id)
        self.assertEqual(event.actor_label, 'sav-manager')
        self.assertIsNone(case.created_by_id)
        self.assertEqual(case.customer_name, 'Élodie Martin')

    def test_database_rejects_impossible_status_and_resolution_combinations(self):
        case = self.create()
        for values in ({'state': 'invalid'}, {'priority': 'invalid'}, {'revision': 0},
                       {'state': 'resolved', 'resolved_at': None}, {'resolved_at': timezone.now()}):
            with self.subTest(values=values), self.assertRaises(IntegrityError), transaction.atomic():
                SupportCase.objects.filter(pk=case.pk).update(**values)

    def test_forms_supply_phone_defaults_and_safe_date_widgets(self):
        form = SupportCaseForm()
        self.assertEqual(form.fields['channel'].initial, 'phone')
        self.assertEqual(form.fields['due_date'].widget.input_type, 'date')
        self.assertNotIn('user', form.fields)
        self.assertNotIn('order', form.fields)
        case = self.create()
        update = SupportUpdateForm(instance=case)
        self.assertEqual(update.initial['revision'], case.revision)
        self.assertEqual(update.initial['state'], 'open')
