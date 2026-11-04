"""Customer data exports describe cancellations without internal incident data."""
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from annulations.models import Cancellation
from paiement.models import Order
from retours.models import ReturnEvent, ReturnRequest

from .privacy import subject_export


@override_settings(DEBUG=True, EMAIL_DELIVERY_ENABLED=False,
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class CancellationPrivacyTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.customer = users.objects.create_user('cancellation-export-client', email='client@example.test')
        self.other = users.objects.create_user('cancellation-export-other', email='other@example.test')
        self.seller = users.objects.create_user('PRIVATE_CANCELLATION_ACTOR', is_staff=True)
        self.order = self.order_for(self.customer)
        self.refund = ReturnRequest.objects.create(order=self.order, kind='cancellation',
            submission_id=uuid.uuid4(), amount=7690, shipping_amount=690,
            refund_state='succeeded', refunded_at=timezone.now(),
            payment_intent='PRIVATE_PAYMENT_INTENT', stripe_refund_id='PRIVATE_REFUND_ID')
        self.cancellation = Cancellation.objects.create(order=self.order, refund=self.refund,
            reason='Annulation demandée avant la préparation.', requested_by=self.seller,
            stock_error='PRIVATE_STOCK_ERROR', refund_error='PRIVATE_REFUND_ERROR',
            stock_completed_at=timezone.now())
        ReturnEvent.objects.create(request=self.refund, actor=self.seller,
            label='PRIVATE_CANCELLATION_EVENT')

    def order_for(self, user):
        return Order.objects.create(user=user, session_key='PRIVATE_CANCEL_SESSION',
            status='paid', paid_at=timezone.now(), total_paid=7690, subtotal=7000,
            return_shipping_amount=690, paid_line_totals=[7000],
            customer_email=self.customer.email,
            items=[{'name': 'Polaire', 'quantity': 1, 'unit_amount': 7000}])

    def test_export_contains_public_cancellation_facts_without_physical_return_or_internal_data(self):
        before = (list(Order.objects.values()), list(Cancellation.objects.values()),
                  list(ReturnRequest.objects.values()), list(ReturnEvent.objects.values()))
        data = subject_export(self.customer)
        order = data['commandes'][0]
        cancellation = order['annulation']
        self.assertEqual(cancellation, {
            'motif': self.cancellation.reason,
            'demandee_le': self.cancellation.created_at,
            'remboursement': self.refund.get_refund_state_display(),
            'montant_centimes': 7690, 'livraison_centimes': 690,
            'rembourse_le': self.refund.refunded_at,
            'stock_retabli_le': self.cancellation.stock_completed_at,
        })
        self.assertEqual(order['retours'], [])
        self.assertEqual(order['paye_centimes'], 7690)
        self.assertNotIn('PRIVATE_', str(data))
        self.assertEqual(before, (list(Order.objects.values()), list(Cancellation.objects.values()),
                                 list(ReturnRequest.objects.values()), list(ReturnEvent.objects.values())))

    def test_matching_email_does_not_disclose_another_accounts_cancellation(self):
        other_order = self.order_for(self.other)
        other_refund = ReturnRequest.objects.create(order=other_order, kind='cancellation',
            submission_id=uuid.uuid4(), amount=7690)
        Cancellation.objects.create(order=other_order, refund=other_refund,
            reason='OTHER_ACCOUNT_PRIVATE_REASON')
        data = subject_export(self.customer)
        self.assertEqual([item['reference'] for item in data['commandes']], [str(self.order.pk)])
        self.assertNotIn('OTHER_ACCOUNT_PRIVATE_REASON', str(data))

    def test_order_without_cancellation_keeps_physical_return_data(self):
        order = self.order_for(self.customer)
        returned = ReturnRequest.objects.create(order=order, submission_id=uuid.uuid4(),
            kind='withdrawal', amount=7000, reason='Taille trop grande.')
        exported = next(item for item in subject_export(self.customer)['commandes']
                        if item['reference'] == str(order.pk))
        self.assertIsNone(exported['annulation'])
        self.assertEqual(exported['retours'][0]['reference'], str(returned.pk))
        self.assertEqual(exported['retours'][0]['motif'], 'Taille trop grande.')
