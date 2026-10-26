"""Explicit SAV links cannot create value or cross customer identity boundaries."""
from dataclasses import replace
from decimal import Decimal
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from avoirs.models import CustomerBenefit, BenefitEvent, BenefitMail, BenefitRedemption
from connexion.test_helpers import verify_test_user
from espace.models import CreditNote
from paiement.models import Order
from retours.models import ReturnRequest, ReturnEvent, ReturnMail
from .corrections import correct_case
from .models import SupportCase, SupportEvent, SupportRelationship
from .relationships import (SupportRelationshipsForm, relationship_context,
                            relationship_options, update_relationships)
from .selectors import get_customer
from .services import SupportConflict, SupportError


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=False,
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class SupportRelationshipTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.manager = User.objects.create_user('link-seller', email='seller@example.test', is_staff=True)
        self.manager.user_permissions.add(Permission.objects.get(codename='manage_returns'))
        verify_test_user(self.manager)
        self.customer = User.objects.create_user('link-client', email='client@example.test')
        self.other = User.objects.create_user('link-other', email='other@example.test')
        self.order = self.make_order(self.customer)
        self.returned = self.make_return(self.order)
        self.benefit = self.make_benefit(self.customer)
        self.credit = CreditNote.objects.create(order=self.order, reference='AV-EXAMPLE-1', amount=Decimal('20.00'), reason='Remboursement déjà enregistré')
        self.case = self.make_case(user=self.customer)

    def make_order(self, user=None, email=None):
        return Order.objects.create(user=user, customer_email=email if email is not None else user.email,
            session_key='sav-link-test', subtotal=8000, items=[], status='paid', total_paid=8000,
            paid_at=timezone.now())

    def make_return(self, order):
        return ReturnRequest.objects.create(order=order, submission_id=uuid.uuid4(), amount=2000)

    def make_benefit(self, user=None, email=None, **extra):
        return CustomerBenefit.objects.create(user=user, bound_user_id=user.pk if user else None,
            recipient_email=email if email is not None else user.email, label='Geste commercial',
            value=Decimal('15.00'), **extra)

    def make_case(self, user=None, order=None, email=None, **extra):
        return SupportCase.objects.create(user=user, order=order, creation_key=uuid.uuid4(),
            creation_fingerprint='a' * 64, customer_email=email if email is not None else (user.email if user else ''),
            customer_name='Camille Exemple', phone='0600000000', subject='Demande par téléphone',
            description='Vérifier le problème signalé.', **extra)

    def data(self, case=None, **extra):
        case = case or self.case
        values = {'revision': case.revision, 'returns': ['return:' + str(self.returned.pk)],
                  'benefits': ['benefit:' + str(self.benefit.pk)], 'credits': ['credit:' + str(self.credit.pk)],
                  'reason': 'Opérations qui répondent à cette demande.'}
        values.update(extra)
        return values

    def login(self, user):
        self.client.force_login(user, backend='connexion.backends.EmailOrUsernameBackend')

    def url(self):
        return reverse('clients:relationships_edit', args=[self.case.pk])

    def test_link_three_kinds_audits_without_altering_business_data(self):
        snapshots = [model.objects.values().get(pk=record.pk) for model, record in (
            (Order, self.order), (ReturnRequest, self.returned), (CustomerBenefit, self.benefit), (CreditNote, self.credit))]
        result = update_relationships(self.manager, self.case.pk, self.data())
        self.assertEqual(result.revision, 2)
        self.assertEqual(self.case.relationships.filter(removed_at__isnull=True).count(), 3)
        event = self.case.events.get()
        self.assertEqual(event.actor_id, self.manager.pk)
        self.assertEqual(len(event.changes), 3)
        self.assertIn(self.benefit.code, event.summary)
        self.assertIn('Opérations qui répondent', event.note)
        for before, (model, record) in zip(snapshots, ((Order, self.order), (ReturnRequest, self.returned),
                                                    (CustomerBenefit, self.benefit), (CreditNote, self.credit))):
            self.assertEqual(before, model.objects.values().get(pk=record.pk))
        for model in (ReturnEvent, ReturnMail, BenefitEvent, BenefitMail, BenefitRedemption):
            self.assertFalse(model.objects.exists())
        self.assertEqual(len(mail.outbox), 0)
        with connection.cursor() as cursor:
            cursor.execute('SELECT note, changes FROM clients_supportevent WHERE id = %s', [event.pk])
            raw = str(cursor.fetchone())
        self.assertNotIn(self.benefit.code, raw)
        self.assertNotIn('Opérations qui répondent', raw)

    def test_options_and_form_have_status_reference_and_all_existing_checks(self):
        update_relationships(self.manager, self.case.pk, self.data())
        self.case.refresh_from_db()
        options = relationship_options(self.case)
        form = SupportRelationshipsForm(instance=self.case, options=options)
        self.assertEqual(form.initial['returns'], ['return:' + str(self.returned.pk)])
        label = str(form.fields['returns'].choices[0][1])
        self.assertIn(self.returned.short_reference, label)
        self.assertIn(str(self.order.pk)[:8].upper(), label)
        self.assertIn('À rembourser', label)
        self.benefit.active = False
        self.benefit.save()
        self.assertIn('Désactivé', relationship_options(self.case)['benefits'][0]['status'])

    def test_multiple_returns_other_orders_of_same_client_and_order_correction(self):
        second = self.make_order(self.customer)
        second_return = self.make_return(second)
        self.case.order = self.order
        self.case.save()
        update_relationships(self.manager, self.case.pk, self.data(returns=['return:' + str(self.returned.pk), 'return:' + str(second_return.pk)]))
        self.case.refresh_from_db()
        data = {name: getattr(self.case, name) for name in ('revision', 'customer_name', 'phone', 'subject', 'description', 'channel', 'category')}
        data.update(related_order=str(second.pk), reason='Commande précisée.')
        correct_case(self.manager, self.case.pk, data)
        self.case.refresh_from_db()
        context = relationship_context(self.case, self.manager)
        self.assertEqual(len(context['related_operations']), 4)
        self.assertEqual(context['unavailable_relationship_count'], 0)

    def test_remove_then_relink_keeps_one_relation_row_and_audit(self):
        update_relationships(self.manager, self.case.pk, self.data())
        self.case.refresh_from_db()
        rows = list(self.case.relationships.values_list('pk', flat=True))
        update_relationships(self.manager, self.case.pk, self.data(returns=[], benefits=[], credits=[]))
        self.assertEqual(self.case.relationships.filter(removed_at__isnull=False).count(), 3)
        self.assertEqual(relationship_context(self.case, self.manager)['related_operations'], [])
        self.case.refresh_from_db()
        update_relationships(self.manager, self.case.pk, self.data())
        self.assertCountEqual(rows, self.case.relationships.filter(removed_at__isnull=True).values_list('pk', flat=True))
        self.assertEqual(self.case.events.count(), 3)

    def test_noop_keeps_revision_and_double_submission_cannot_duplicate(self):
        data = self.data()
        update_relationships(self.manager, self.case.pk, data)
        with self.assertRaises(SupportConflict):
            update_relationships(self.manager, self.case.pk, data)
        self.case.refresh_from_db()
        update_relationships(self.manager, self.case.pk, self.data())
        self.case.refresh_from_db()
        self.assertEqual(self.case.revision, 2)
        self.assertEqual(self.case.events.count(), 1)
        self.assertEqual(self.case.relationships.count(), 3)

    def test_foreign_operations_cannot_be_selected_even_when_order_email_matches(self):
        other_order = self.make_order(self.other, email=self.customer.email)
        other_return = self.make_return(other_order)
        other_benefit = self.make_benefit(self.other, email=self.customer.email)
        other_credit = CreditNote.objects.create(order=other_order, reference='AV-OTHER', amount=10, reason='Private')
        for field, key in [('returns', 'return:' + str(other_return.pk)), ('benefits', 'benefit:' + str(other_benefit.pk)), ('credits', 'credit:' + str(other_credit.pk))]:
            with self.subTest(field=field), self.assertRaises(SupportError):
                update_relationships(self.manager, self.case.pk, self.data(**{field: [key]}))
        self.assertFalse(SupportRelationship.objects.exists())
        self.assertFalse(SupportEvent.objects.exists())

    def test_guest_links_use_exact_email_but_phone_never_claims_operations(self):
        guest_order = self.make_order(email='guest@example.test')
        guest_return = self.make_return(guest_order)
        guest_benefit = self.make_benefit(email='guest@example.test')
        guest_case = self.make_case(order=guest_order, email='guest@example.test')
        update_relationships(self.manager, guest_case.pk, self.data(guest_case,
            returns=['return:' + str(guest_return.pk)], benefits=['benefit:' + str(guest_benefit.pk)], credits=[]))
        phone_case = self.make_case()
        self.assertFalse(any(relationship_options(phone_case).values()))
        with self.assertRaises(SupportError):
            update_relationships(self.manager, phone_case.pk, self.data(phone_case))

    def test_retained_assignment_cannot_be_claimed_by_reused_email(self):
        old = get_user_model().objects.create_user('link-old', email='retained@example.test')
        retained = self.make_benefit(old)
        old.delete()
        new = get_user_model().objects.create_user('link-new', email='retained@example.test')
        case = self.make_case(user=new)
        self.assertFalse(relationship_options(case)['benefits'])
        with self.assertRaises(SupportError):
            update_relationships(self.manager, case.pk, self.data(case, returns=[], benefits=['benefit:' + str(retained.pk)], credits=[]))

    def test_reassigned_target_is_hidden_and_can_be_unlinked_without_exposing_new_data(self):
        update_relationships(self.manager, self.case.pk, self.data())
        self.benefit.user = self.other
        self.benefit.bound_user_id = self.other.pk
        self.benefit.recipient_email = self.other.email
        self.benefit.label = 'NEVER_DISCLOSE_NEW_OWNER'
        self.benefit.save()
        context = relationship_context(self.case, self.manager)
        self.assertEqual(context['unavailable_relationship_count'], 1)
        self.assertNotIn('NEVER_DISCLOSE', str(context))
        self.case.refresh_from_db()
        update_relationships(self.manager, self.case.pk, self.data(benefits=[]))
        self.assertTrue(self.case.relationships.get(benefit=self.benefit).removed_at)
        self.assertNotIn('NEVER_DISCLOSE', self.case.events.first().summary)
        self.assertIn('rattachement client a changé', self.case.events.first().summary)

    def test_customer_membership_is_rechecked_after_locks(self):
        customer = get_customer(f'c-{self.case.pk}')
        with patch('clients.relationships.get_customer', side_effect=[customer, replace(customer, benefit_ids=[])]):
            with self.assertRaises(SupportConflict):
                update_relationships(self.manager, self.case.pk, self.data())
        self.assertFalse(SupportRelationship.objects.exists())

    def test_audit_failure_rolls_back_links_and_revision(self):
        with patch('clients.relationships.SupportEvent.objects.create', side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError):
                update_relationships(self.manager, self.case.pk, self.data())
        self.case.refresh_from_db()
        self.assertEqual(self.case.revision, 1)
        self.assertFalse(SupportRelationship.objects.exists())

    def test_reason_and_known_fields_are_required(self):
        for extra in ({'reason': ''}, {'customer_email': self.other.email}, {'returns': ['invalid']}, {'revision': 0}):
            with self.subTest(extra=extra), self.assertRaises(SupportError):
                update_relationships(self.manager, self.case.pk, self.data(**extra))
        self.assertFalse(SupportRelationship.objects.exists())

    def test_database_enforces_one_target_and_unique_pair(self):
        for kwargs in ({}, {'benefit': self.benefit, 'return_request': self.returned}):
            with self.assertRaises(IntegrityError), transaction.atomic():
                SupportRelationship.objects.create(case=self.case, **kwargs)
        SupportRelationship.objects.create(case=self.case, benefit=self.benefit)
        with self.assertRaises(IntegrityError), transaction.atomic():
            SupportRelationship.objects.create(case=self.case, benefit=self.benefit)

    def test_authorization_runs_before_customer_reads(self):
        for actor in (None, self.customer):
            with patch('clients.relationships.get_customer') as lookup:
                with self.assertRaises(PermissionDenied):
                    update_relationships(actor, self.case.pk, self.data())
                with self.assertRaises(PermissionDenied):
                    relationship_context(self.case, actor)
                lookup.assert_not_called()

    def test_http_privacy_csrf_permissions_and_update(self):
        self.assertEqual(self.client.get(self.url()).status_code, 302)
        self.login(self.customer)
        self.assertEqual(self.client.get(self.url()).status_code, 403)
        self.login(self.manager)
        response = self.client.get(self.url())
        self.assertContains(response, self.benefit.code)
        self.assertContains(response, 'Retours et avoirs concernés.')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('noindex', response['X-Robots-Tag'])
        self.assertEqual(self.client.post(self.url(), self.data()).status_code, 302)
        self.assertEqual(self.case.relationships.count(), 3)
        self.assertEqual(self.client.post(self.url(), self.data()).status_code, 409)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.manager, backend='connexion.backends.EmailOrUsernameBackend')
        self.assertEqual(csrf.post(self.url(), self.data()).status_code, 403)
        self.manager.email_verification.delete()
        self.assertEqual(self.client.get(self.url()).status_code, 302)

    def test_http_foreign_selection_and_reassigned_details_are_never_rendered(self):
        self.login(self.manager)
        update_relationships(self.manager, self.case.pk, self.data())
        self.benefit.user = self.other
        self.benefit.bound_user_id = self.other.pk
        self.benefit.recipient_email = self.other.email
        self.benefit.label = 'NEVER_DISCLOSE_FOREIGN_BENEFIT'
        self.benefit.save()
        response = self.client.get(self.url())
        self.assertNotContains(response, self.benefit.label)
        self.assertContains(response, 'ancien lien')
        self.case.refresh_from_db()
        response = self.client.post(self.url(), self.data())
        self.assertEqual(response.status_code, 400)
        self.assertNotContains(response, self.benefit.label, status_code=400)
