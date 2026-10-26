"""Open SAV relationships suspend cleanup review without changing customer data."""
import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.utils import timezone

from avoirs.models import BenefitRedemption, CustomerBenefit
from clients.models import SupportCase, SupportEvent, SupportRelationship
from clients.services import create_case
from connexion.test_helpers import verify_test_user
from paiement.models import Order
from retours.models import ReturnRequest
from .models import CreditNote
from .privacy import retention_review


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=False,
                   EMAIL_DELIVERY_ENABLED=False,
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class SupportRelationshipPrivacyTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.old = self.now - timedelta(days=4000)
        User = get_user_model()
        self.customer = User.objects.create_user('sav-link-retention-client',
            email='retention-client@example.test', date_joined=self.old, last_login=self.old)
        self.seller = User.objects.create_user('sav-link-retention-seller',
            email='retention-seller@example.test', is_staff=True)
        self.seller.user_permissions.add(Permission.objects.get(
            content_type__app_label='retours', codename='manage_returns'))
        verify_test_user(self.seller)

    def counts(self):
        return {row['code']: row['nombre'] for row in retention_review(self.now)['categories']}

    def order(self, *, user=None, status='paid'):
        order = Order.objects.create(user=user, session_key='synthetic-retention-order',
            customer_email=user.email if user else '', status=status, subtotal=8000,
            total_paid=8000 if status == 'paid' else None, items=[])
        Order.objects.filter(pk=order.pk).update(created_at=self.old)
        return order

    def case(self, **extra):
        values = {'creation_key': uuid.uuid4(), 'creation_fingerprint': 'a' * 64,
                  'customer_name': 'Client synthétique', 'customer_email': '',
                  'phone': '0600000000', 'subject': 'Demande synthétique de suivi',
                  'description': 'Les opérations sont suivies dans un dossier distinct.',
                  'state': 'open'}
        values.update(extra)
        case = SupportCase.objects.create(**values)
        SupportCase.objects.filter(pk=case.pk).update(created_at=self.old, updated_at=self.old)
        return case

    def benefit(self, *, retained_only=False):
        benefit = CustomerBenefit.objects.create(
            user=None if retained_only else self.customer, bound_user_id=self.customer.pk,
            recipient_email=self.customer.email, label='Avoir synthétique ancien',
            value=Decimal('15.00'), active=False, ends_at=self.old)
        CustomerBenefit.objects.filter(pk=benefit.pk).update(created_at=self.old, updated_at=self.old)
        return benefit

    def assert_account_and_order_counts(self, expected):
        counts = self.counts()
        self.assertEqual(counts['inactive_accounts_review'], expected)
        self.assertEqual(counts['old_paid_orders_review'], expected)

    def assert_lifecycle(self, case, **target):
        """Only an active link on an unresolved case suspends both reviews."""
        self.assertIsNone(case.user_id)
        self.assertIsNone(case.order_id)
        self.assert_account_and_order_counts(1)
        link = SupportRelationship.objects.create(case=case, created_by=self.seller, **target)
        self.assert_account_and_order_counts(0)
        SupportRelationship.objects.filter(pk=link.pk).update(removed_at=self.now, removed_by=self.seller)
        self.assert_account_and_order_counts(1)
        SupportRelationship.objects.filter(pk=link.pk).update(removed_at=None, removed_by=None)
        self.assert_account_and_order_counts(0)
        SupportCase.objects.filter(pk=case.pk).update(state='resolved', resolved_at=self.old)
        self.assert_account_and_order_counts(1)
        SupportCase.objects.filter(pk=case.pk).update(state='in_progress', resolved_at=None)
        self.assert_account_and_order_counts(0)

    def test_return_link_protects_its_order_and_account_until_removed_or_resolved(self):
        order = self.order(user=self.customer)
        returned = ReturnRequest.objects.create(order=order, submission_id=uuid.uuid4(),
            amount=1500, state='completed', completed_at=self.old)
        ReturnRequest.objects.filter(pk=returned.pk).update(created_at=self.old)
        self.assert_lifecycle(self.case(), return_request=returned)

    def test_credit_document_link_protects_its_order_and_account_until_removed_or_resolved(self):
        order = self.order(user=self.customer)
        credit = CreditNote.objects.create(order=order, reference='AV-SYNTHETIC-RETENTION',
            amount=Decimal('15.00'), reason='Document de test', issued_at=self.old.date())
        self.assert_lifecycle(self.case(), credit_note=credit)

    def test_used_benefit_link_protects_redemption_order_and_account(self):
        order = self.order(user=self.customer)
        benefit = self.benefit()
        redemption = BenefitRedemption.objects.create(benefit=benefit, order=order,
            state='used', discount_amount=1500, used_at=self.old, snapshot={})
        BenefitRedemption.objects.filter(pk=redemption.pk).update(created_at=self.old)
        self.assert_lifecycle(self.case(), benefit=benefit)

    def test_benefit_retained_account_binding_is_protected_even_without_current_user_fk(self):
        benefit = self.benefit(retained_only=True)
        case = self.case()
        self.assertEqual(self.counts()['inactive_accounts_review'], 1)
        link = SupportRelationship.objects.create(case=case, benefit=benefit)
        self.assertEqual(self.counts()['inactive_accounts_review'], 0)
        SupportRelationship.objects.filter(pk=link.pk).update(removed_at=self.now)
        self.assertEqual(self.counts()['inactive_accounts_review'], 1)

    def test_contact_order_anchor_protects_guest_orders_without_assigning_a_related_order(self):
        keys = ('abandoned_orders_review', 'demo_orders_review', 'old_paid_orders_review')
        orders = [self.order(status=status) for status in ('pending', 'demo', 'paid')]
        self.assertEqual([self.counts()[key] for key in keys], [1, 1, 1])
        cases = []
        for order in orders:
            case, reused = create_case(self.seller, {
                'creation_key': uuid.uuid4(), 'customer_name': 'Invité synthétique',
                'customer_email': '', 'phone': '0600000000', 'subject': 'Nouvel appel',
                'description': 'La demande ne concerne pas une commande précise.',
                'channel': 'phone', 'category': 'other', 'priority': 'normal', 'due_date': None,
            }, contact_order=order)
            self.assertFalse(reused)
            self.assertIsNone(case.user_id)
            self.assertIsNone(case.order_id)
            self.assertEqual(case.contact_reference, order.pk)
            cases.append(case)
        self.assertEqual([self.counts()[key] for key in keys], [0, 0, 0])
        SupportCase.objects.filter(pk__in=[case.pk for case in cases]).update(
            state='resolved', resolved_at=self.now)
        self.assertEqual([self.counts()[key] for key in keys], [1, 1, 1])

    def test_order_anchor_also_protects_account_if_source_order_is_later_attached(self):
        order = self.order()
        case = self.case(contact_reference=order.pk)
        Order.objects.filter(pk=order.pk).update(user=self.customer)
        self.assert_account_and_order_counts(0)
        SupportCase.objects.filter(pk=case.pk).update(state='resolved', resolved_at=self.old)
        self.assert_account_and_order_counts(1)

    def test_one_remaining_active_link_keeps_order_protected_without_duplicate_counts(self):
        order = self.order(user=self.customer)
        first = self.case()
        second = self.case()
        credit = CreditNote.objects.create(order=order, reference='AV-SYNTHETIC-SHARED',
            amount=Decimal('15.00'), reason='Document de test', issued_at=self.old.date())
        SupportRelationship.objects.create(case=first, credit_note=credit)
        remaining = SupportRelationship.objects.create(case=second, credit_note=credit)
        self.assert_account_and_order_counts(0)
        SupportCase.objects.filter(pk=first.pk).update(state='resolved', resolved_at=self.old)
        self.assert_account_and_order_counts(0)
        SupportRelationship.objects.filter(pk=remaining.pk).update(removed_at=self.now)
        self.assert_account_and_order_counts(1)

    def test_review_never_exposes_linked_records_or_mutates_them(self):
        order = self.order(user=self.customer)
        case = self.case(customer_name='PRIVATE_SAV_LINK_CLIENT')
        benefit = self.benefit()
        link = SupportRelationship.objects.create(case=case, benefit=benefit, created_by=self.seller)
        event = SupportEvent.objects.create(case=case, kind='update', state='open',
            note='PRIVATE_SAV_LINK_REASON', changes={'relation': {'label': 'Lien privé',
                'before': '', 'after': benefit.code}})
        models = (get_user_model(), Order, CustomerBenefit, SupportCase, SupportEvent, SupportRelationship)
        before = {model: list(model.objects.order_by('pk').values()) for model in models}
        report = retention_review(self.now)
        self.assertEqual(report['modifications'], 0)
        self.assertEqual(report['mode'], 'lecture seule')
        for private in (str(order.pk), str(case.pk), case.customer_name, event.note,
                        benefit.code, benefit.recipient_email, self.seller.username):
            self.assertNotIn(private, str(report))
        self.assertEqual(before, {model: list(model.objects.order_by('pk').values()) for model in models})
        link.refresh_from_db()
        self.assertIsNone(link.removed_at)
