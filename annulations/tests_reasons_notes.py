"""Suggested public reasons and encrypted notes stay separate, including on races."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Permission
from django.core.exceptions import PermissionDenied
from django.db import close_old_connections, connection, connections
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from connexion.test_helpers import verify_test_user
from core.models import Product
from espace.privacy import subject_export
from expeditions.models import Fulfillment, FulfillmentEvent
from paiement.models import Order
from retours.models import ReturnEvent, ReturnMail, ReturnRequest
from stocks.models import Reservation, Variant

from .forms import CancellationForm, CancellationInternalNoteForm
from .models import Cancellation
from .reasons import REASON_PRESETS, REASON_TEXTS
from .services import (CancellationError, InternalNoteConflict, finalize_refund,
                       preview, request_cancellation, save_internal_note)


class CancellationReasonFormsTests(SimpleTestCase):
    def data(self, **kwargs):
        return {'preview_token': 'signed-preview', 'confirmed': 'on', **kwargs}

    def test_each_preset_supplies_a_public_reason_without_javascript(self):
        for code, label, text in REASON_PRESETS:
            with self.subTest(code=code):
                form = CancellationForm(self.data(reason_code=code, reason='  '))
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data['reason'], text)
                self.assertEqual(form.cleaned_data['internal_note'], '')

    def test_custom_text_wins_and_legacy_form_keeps_working(self):
        for code in ('client_request', ''):
            with self.subTest(code=code):
                form = CancellationForm(self.data(reason_code=code, reason='  À votre demande.  '))
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data['reason'], 'À votre demande.')

    def test_invalid_codes_and_missing_or_excessive_public_reason_are_rejected(self):
        for values, field in (({'reason_code': 'forged', 'reason': 'Client'}, 'reason_code'),
                              ({}, 'reason'),
                              ({'reason_code': 'other', 'reason': 'x' * 1001}, 'reason')):
            with self.subTest(values=values):
                form = CancellationForm(self.data(**values))
                self.assertFalse(form.is_valid())
                self.assertIn(field, form.errors)

    def test_notes_allow_clearing_but_limit_content_and_require_revision(self):
        for value in ('', 'x' * 2000):
            form = CancellationInternalNoteForm({'internal_note': value, 'revision': 0})
            self.assertTrue(form.is_valid(), form.errors)
        for form in (CancellationInternalNoteForm({'internal_note': 'x' * 2001, 'revision': 0}),
                     CancellationInternalNoteForm({'internal_note': 'Note'}),
                     CancellationInternalNoteForm({'revision': -1}),
                     CancellationInternalNoteForm({'revision': 2147483648}),
                     CancellationForm(self.data(reason='Client', internal_note='x' * 2001))):
            self.assertFalse(form.is_valid())

    def test_widget_exposes_the_same_preset_text_as_server_validation(self):
        widget = CancellationForm().fields['reason_code'].widget
        for index, (code, label, text) in enumerate(REASON_PRESETS):
            option = widget.create_option('reason_code', code, label, False, index)
            self.assertEqual(option['attrs']['data-reason-text'], text)


class CancellationNotesFixture:
    def setUp(self):
        users = get_user_model()
        self.seller = users.objects.create_superuser('cancel-notes-seller',
            email='seller@example.test', password='synthetic-test-password')
        self.customer = users.objects.create_user('cancel-notes-customer', email='client@example.test')
        self.product = Product.objects.create(name='Polaire de test',
            slug='cancel-notes-polaire', price=Decimal('40.00'))
        self.variant = Variant.objects.create(product=self.product, size='M', color='Vert',
            quantity=5, reserved=1)
        self.order = Order.objects.create(user=self.customer, session_key='synthetic-cancellation-notes',
            status='paid', paid_at=timezone.now(), customer_email=self.customer.email,
            stripe_session_id='cs_cancellation_notes', subtotal=8000, total_paid=6690,
            return_shipping_amount=690, paid_line_totals=[6000], items=[{
                'product': self.product.pk, 'variant': self.variant.pk, 'name': self.product.name,
                'size': 'M', 'color': 'Vert', 'quantity': 2, 'unit_amount': 4000}])
        Reservation.objects.create(order=self.order, variant=self.variant, quantity=2, state='sold')
        self.token = preview(self.order)['token']

    def cancel(self, **kwargs):
        return request_cancellation(self.order.pk, self.seller,
            kwargs.pop('reason', 'Annulation demandée par le client.'), self.token, True, **kwargs)

    def private_state(self, cancellation):
        return (cancellation.internal_note, cancellation.internal_note_revision,
                cancellation.internal_note_updated_at, cancellation.internal_note_updated_by_id)


TEST_SETTINGS = dict(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True, PAYMENTS_ENABLED=False,
    STRIPE_SECRET_KEY='', PAYMENT_TEST_MODE=False, EMAIL_DELIVERY_ENABLED=False,
    EMAIL_TEST_MODE=False, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])


@override_settings(**TEST_SETTINGS)
class CancellationReasonsNotesTests(CancellationNotesFixture, TestCase):
    def test_service_saves_preset_custom_wording_and_encrypted_initial_note(self):
        private = 'PRIVATE_INITIAL_NOTE : suivi téléphonique.'
        cancellation = self.cancel(reason_code='client_request', internal_note=private)
        self.assertEqual(cancellation.reason_code, 'client_request')
        self.assertEqual(cancellation.reason, 'Annulation demandée par le client.')
        self.assertEqual(self.private_state(cancellation)[:2], (private, 1))
        self.assertIsNotNone(cancellation.internal_note_updated_at)
        self.assertEqual(cancellation.internal_note_updated_by_id, self.seller.pk)
        with connection.cursor() as cursor:
            cursor.execute('SELECT internal_note FROM annulations_cancellation WHERE id = %s', [cancellation.pk])
            stored = cursor.fetchone()[0]
        self.assertTrue(stored.startswith('enc:v1:'))
        self.assertNotIn(private, stored)
        cancellation.refresh_from_db()
        self.assertEqual(cancellation.internal_note, private)

    def test_service_falls_back_to_known_preset_and_empty_note_keeps_legacy_defaults(self):
        cancellation = self.cancel(reason='', reason_code='unavailable')
        self.assertEqual(cancellation.reason, REASON_TEXTS['unavailable'])
        self.assertEqual(cancellation.refund.reason, REASON_TEXTS['unavailable'])
        self.assertEqual(self.private_state(cancellation), ('', 0, None, None))

    def test_legacy_call_and_duplicate_confirmation_never_overwrite_saved_words_or_note(self):
        cancellation = self.cancel()
        self.assertEqual(cancellation.reason_code, '')
        save_internal_note(self.order.pk, self.seller, 'Première note.', 0)
        with patch('annulations.services._run_refund') as refund:
            duplicate = request_cancellation(self.order.pk, self.seller, 'Nouveau motif',
                'old-token', True, reason_code='other', internal_note='Autre note')
        refund.assert_not_called()
        cancellation.refresh_from_db()
        self.assertEqual(duplicate.pk, cancellation.pk)
        self.assertEqual(cancellation.reason, 'Annulation demandée par le client.')
        self.assertEqual(cancellation.reason_code, '')
        self.assertEqual(cancellation.internal_note, 'Première note.')

    def test_invalid_inputs_fail_before_financial_stock_or_notification_effects(self):
        invalid = ({'reason_code': 'forged'}, {'reason_code': None}, {'reason_code': ['other']},
                   {'internal_note': 'x' * 2001}, {'internal_note': None},
                   {'reason': ''}, {'reason': 'x' * 1001})
        with patch('annulations.services._run_refund') as refund:
            for values in invalid:
                with self.subTest(values=values), self.assertRaises(CancellationError):
                    self.cancel(**values)
        refund.assert_not_called()
        self.assertFalse(Cancellation.objects.exists())
        self.assertFalse(ReturnRequest.objects.exists())
        self.assertFalse(ReturnMail.objects.exists())
        self.assertFalse(Fulfillment.objects.exists())
        self.variant.refresh_from_db()
        self.assertEqual((self.variant.quantity, self.variant.reserved), (5, 1))

    def test_note_edit_and_clear_update_only_private_metadata_and_are_idempotent(self):
        cancellation = self.cancel()
        before = (list(ReturnRequest.objects.values()), list(ReturnMail.objects.values()),
                  list(ReturnEvent.objects.values()), list(FulfillmentEvent.objects.values()),
                  list(Variant.objects.values()), list(Reservation.objects.values()))
        with patch('stripe.Refund.create') as refund, patch('annulations.mail.notice') as notice:
            first = save_internal_note(self.order.pk, self.seller, '  Note à conserver.  ', 0)
            same = save_internal_note(self.order.pk, self.seller, 'Note à conserver.', 0)
            self.assertEqual(self.private_state(first), self.private_state(same))
            cleared = save_internal_note(self.order.pk, self.seller, '', 1)
        refund.assert_not_called(); notice.assert_not_called()
        self.assertEqual((cleared.internal_note, cleared.internal_note_revision), ('', 2))
        self.assertEqual(cleared.internal_note_updated_by_id, self.seller.pk)
        self.assertEqual(cancellation.reason, cleared.reason)
        self.assertEqual(before, (list(ReturnRequest.objects.values()), list(ReturnMail.objects.values()),
            list(ReturnEvent.objects.values()), list(FulfillmentEvent.objects.values()),
            list(Variant.objects.values()), list(Reservation.objects.values())))

    def test_stale_tab_cannot_overwrite_a_new_note_and_explicit_review_can_save(self):
        self.cancel(internal_note='Note initiale.')
        current = save_internal_note(self.order.pk, self.seller, 'Nouvelle note enregistrée.', 1)
        with self.assertRaises(InternalNoteConflict):
            save_internal_note(self.order.pk, self.seller, 'Saisie de l’ancien onglet.', 1)
        self.assertEqual(Cancellation.objects.get(pk=current.pk).internal_note, current.internal_note)
        reviewed = save_internal_note(self.order.pk, self.seller, 'Saisie de l’ancien onglet.', 2)
        self.assertEqual(reviewed.internal_note_revision, 3)

    def test_note_service_rejects_missing_cancellation_and_invalid_note_or_revision(self):
        with self.assertRaises(CancellationError):
            save_internal_note(self.order.pk, self.seller, 'Note', 0)
        self.cancel()
        for value, revision in [('x' * 2001, 0), (None, 0), ([], 0), ('Note', -1),
                                ('Note', True), ('Note', '0'), ('Note', 2147483648)]:
            with self.subTest(revision=revision), self.assertRaises(CancellationError):
                save_internal_note(self.order.pk, self.seller, value, revision)
        self.assertEqual(Cancellation.objects.get(order=self.order).internal_note_revision, 0)
        saved = save_internal_note(self.order.pk, self.seller, 'x' * 2000, 0)
        self.assertEqual(len(saved.internal_note), 2000)

    def test_only_authorized_verified_staff_can_write_notes(self):
        self.cancel()
        users = get_user_model()
        staff = users.objects.create_user('cancel-note-staff', email='staff@example.test', is_staff=True)
        inactive = users.objects.create_superuser('cancel-note-inactive', email='inactive@example.test',
            password='synthetic-test-password', is_active=False)
        for actor in (None, AnonymousUser(), self.customer, staff, inactive):
            with self.subTest(actor=actor), self.assertRaises(PermissionDenied):
                save_internal_note(self.order.pk, actor, 'Note interdite.', 0)
        with override_settings(LOCAL_SKIP_EMAIL_VERIFICATION=False):
            with self.assertRaises(PermissionDenied):
                save_internal_note(self.order.pk, self.seller, 'Adresse non confirmée.', 0)
            staff.user_permissions.add(Permission.objects.get(codename='manage_returns'))
            staff = users.objects.get(pk=staff.pk)
            verify_test_user(staff)
            saved = save_internal_note(self.order.pk, staff, 'Agent autorisé.', 0)
        self.assertEqual(saved.internal_note_updated_by_id, staff.pk)

    def test_deleting_editor_does_not_delete_note_or_edit_date(self):
        cancellation = self.cancel()
        editor = get_user_model().objects.create_superuser('cancel-note-editor',
            email='editor@example.test', password='synthetic-test-password')
        saved = save_internal_note(self.order.pk, editor, 'Note conservée.', 0)
        editor.delete()
        cancellation.refresh_from_db()
        self.assertEqual(cancellation.internal_note, saved.internal_note)
        self.assertEqual(cancellation.internal_note_updated_at, saved.internal_note_updated_at)
        self.assertIsNone(cancellation.internal_note_updated_by_id)

    def test_private_note_never_enters_customer_view_export_emails_or_public_events(self):
        private = 'PRIVATE_CANCELLATION_NOTE_917'
        cancellation = self.cancel(reason='Motif public de test.', reason_code='other', internal_note=private)
        from .mail import notice
        notice(cancellation, 'cancellation_refund')
        self.client.force_login(self.customer)
        response = self.client.get(reverse('espace:order', args=[self.order.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Motif public de test.')
        self.assertNotContains(response, private)
        self.assertNotIn(private, str(subject_export(self.customer)))
        self.assertNotIn('internal_note', str(subject_export(self.customer)))
        for values in (ReturnMail.objects.values(), ReturnEvent.objects.values(),
                       FulfillmentEvent.objects.values(), ReturnRequest.objects.values()):
            self.assertNotIn(private, str(list(values)))
        self.assertEqual(cancellation.refund.reason, 'Motif public de test.')


@override_settings(**TEST_SETTINGS)
class CancellationNotesConcurrencyTests(CancellationNotesFixture, TransactionTestCase):
    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL row-lock test')
        super().setUp()
        self.cancellation = self.cancel()

    def race(self, *actions):
        barrier = Barrier(len(actions))

        def worker(action):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '5s'")
                    cursor.execute("SET statement_timeout = '10s'")
                actor = get_user_model().objects.get(pk=self.seller.pk)
                barrier.wait(timeout=10)
                return action(actor)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=len(actions)) as pool:
            futures = [pool.submit(worker, action) for action in actions]
            return [future.result(timeout=20) for future in futures]

    def test_two_different_edits_from_same_revision_have_one_winner(self):
        def save(actor, value):
            try:
                save_internal_note(self.order.pk, actor, value, 0)
                return value
            except InternalNoteConflict:
                return None
        results = self.race(lambda actor: save(actor, 'Note A'), lambda actor: save(actor, 'Note B'))
        winners = [value for value in results if value]
        self.assertEqual(len(winners), 1)
        self.cancellation.refresh_from_db()
        self.assertEqual(self.cancellation.internal_note, winners[0])
        self.assertEqual(self.cancellation.internal_note_revision, 1)

    def test_identical_simultaneous_saves_increment_only_once(self):
        results = self.race(
            lambda actor: save_internal_note(self.order.pk, actor, 'Même note.', 0).internal_note_revision,
            lambda actor: save_internal_note(self.order.pk, actor, 'Même note.', 0).internal_note_revision)
        self.assertEqual(results, [1, 1])
        self.cancellation.refresh_from_db()
        self.assertEqual(self.cancellation.internal_note_revision, 1)

    def test_note_save_and_refund_finalization_preserve_both_changes(self):
        ReturnRequest.objects.filter(pk=self.cancellation.refund_id).update(
            refund_state='succeeded', refunded_at=timezone.now())
        self.race(lambda actor: save_internal_note(self.order.pk, actor, 'Note pendant le remboursement.', 0),
                  lambda actor: finalize_refund(self.cancellation.refund_id))
        self.cancellation.refresh_from_db(); self.variant.refresh_from_db()
        self.assertEqual(self.cancellation.internal_note, 'Note pendant le remboursement.')
        self.assertEqual(self.cancellation.internal_note_revision, 1)
        self.assertIsNotNone(self.cancellation.stock_completed_at)
        self.assertEqual(self.cancellation.stock_movements.count(), 1)
        self.assertEqual((self.variant.quantity, self.variant.reserved), (7, 1))


@override_settings(**TEST_SETTINGS)
class CancellationNotesMigrationTests(CancellationNotesFixture, TransactionTestCase):
    def test_old_cancellation_gets_an_encrypted_empty_note_without_inventing_history(self):
        refund = ReturnRequest.objects.create(order=self.order, kind='cancellation',
            submission_id=uuid.uuid4(), reason='Ancien motif libre.', amount=6690,
            shipping_amount=690)
        old_target = [('annulations', '0002_cancellation_stock_history')]
        new_target = [('annulations', '0003_cancellation_reasons_internal_note')]
        executor = MigrationExecutor(connection)
        try:
            executor.migrate(old_target)
            previous_apps = executor.loader.project_state(old_target).apps
            previous_model = previous_apps.get_model('annulations', 'Cancellation')
            old = previous_model.objects.create(order_id=self.order.pk, refund_id=refund.pk,
                reason='Ancien motif libre.', requested_by_id=self.seller.pk)
        finally:
            MigrationExecutor(connection).migrate(new_target)
        migrated = Cancellation.objects.get(pk=old.pk)
        self.assertEqual(migrated.reason, 'Ancien motif libre.')
        self.assertEqual(migrated.reason_code, '')
        self.assertEqual(self.private_state(migrated), ('', 0, None, None))
        with connection.cursor() as cursor:
            cursor.execute('SELECT internal_note FROM annulations_cancellation WHERE id = %s', [old.pk])
            self.assertTrue(cursor.fetchone()[0].startswith('enc:v1:'))
