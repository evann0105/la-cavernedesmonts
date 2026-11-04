"""Financial and stock behaviour, with Stripe entirely mocked."""
import uuid
from decimal import Decimal
from unittest.mock import patch
import stripe
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.utils import timezone
from core.models import Product
from expeditions.models import Fulfillment
from paiement.models import Order
from retours.models import ReturnRequest, ReturnMail
from retours.refunds import apply_verified_event
from stocks.models import Variant, Reservation
from .models import Cancellation
from .services import (CancellationError, preview, request_cancellation,
                       reconcile_cancellation, retry_stock)


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True, PAYMENTS_ENABLED=True,
    STRIPE_SECRET_KEY='sk_test_fake', PAYMENT_TEST_MODE=False, EMAIL_DELIVERY_ENABLED=False)
class CancellationTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser('cancel-admin', 'admin@example.test', 'test-password')
        self.user = get_user_model().objects.create_user('cancel-client', email='client@example.test')
        self.product = Product.objects.create(name='Polaire', slug='cancel-polaire', price=Decimal('40'))
        self.variant = Variant.objects.create(product=self.product, size='M', color='Vert', quantity=5, reserved=1)
        self.order = Order.objects.create(user=self.user, session_key='cancel', status='paid', paid_at=timezone.now(),
            customer_email=self.user.email, stripe_session_id='cs_cancel', subtotal=8000, total_paid=6690,
            return_shipping_amount=690, paid_line_totals=[6000], items=[{
                'product':self.product.pk, 'variant':self.variant.pk, 'name':'Polaire',
                'size':'M', 'color':'Vert', 'quantity':2, 'unit_amount':4000}])
        self.hold = Reservation.objects.create(order=self.order, variant=self.variant, quantity=2, state='sold')
        self.session = {'payment_status':'paid', 'amount_total':6690,
                        'client_reference_id':str(self.order.pk), 'payment_intent':'pi_cancel'}

    def result(self, status='succeeded', **kwargs):
        record = self.order.returns.get()
        return {'id':'re_cancel', 'amount':record.amount, 'currency':'eur',
                'payment_intent':'pi_cancel', 'metadata':{'return_id':str(record.pk)}, 'status':status, **kwargs}

    def cancel(self, status='succeeded', **kwargs):
        token = kwargs.pop('preview_token', preview(self.order)['token'])
        with patch('retours.refunds.stripe.checkout.Session.retrieve', return_value=self.session), \
             patch('retours.refunds.stripe.Refund.list', return_value={'data':[]}), \
             patch('retours.refunds.stripe.Refund.create', side_effect=lambda **data:self.result(status)) as create:
            value = request_cancellation(self.order.pk, self.admin, 'Demande du client par téléphone', token, True, **kwargs)
        return value, create

    def test_success_refunds_net_and_shipping_and_restocks_exactly_once(self):
        cancellation, create = self.cancel()
        self.assertEqual(create.call_args.kwargs['amount'], 6690)
        self.assertEqual(create.call_args.kwargs['idempotency_key'], f'return-{cancellation.refund_id}')
        self.assertEqual(cancellation.refund.refund_state, 'succeeded')
        self.assertIsNotNone(cancellation.stock_completed_at)
        self.variant.refresh_from_db(); self.assertEqual(self.variant.quantity, 7); self.assertEqual(self.variant.reserved, 1)
        self.hold.refresh_from_db(); self.assertEqual(self.hold.state, 'sold')
        self.order.refresh_from_db(); self.assertEqual(self.order.status, 'paid')
        self.assertEqual(self.order.fulfillment.state, 'cancelled')
        line=cancellation.refund.lines.get(); self.assertEqual((line.amount,line.received,line.restocked), (6000,0,0))
        self.assertEqual(cancellation.refund.shipping_amount, 690)
        with patch('retours.refunds.stripe.Refund.create') as again:
            request_cancellation(self.order.pk,self.admin,'Double clic', 'old token', True)
            reconcile_cancellation(self.order.pk,self.admin); retry_stock(self.order.pk,self.admin)
            apply_verified_event(self.result()); apply_verified_event(self.result())
        again.assert_not_called(); self.variant.refresh_from_db(); self.assertEqual(self.variant.quantity,7)
        self.assertEqual(Cancellation.objects.count(),1); self.assertEqual(ReturnRequest.objects.count(),1)
        self.assertEqual(sum(m.payload.get('kind') == 'cancellation_requested' for m in ReturnMail.objects.all()),1)
        self.assertEqual(sum(m.payload.get('kind') == 'cancellation_refund' for m in ReturnMail.objects.all()),1)

    def test_pending_waits_for_verified_success(self):
        cancellation,_=self.cancel('pending')
        self.variant.refresh_from_db(); self.assertEqual(self.variant.quantity,5)
        self.assertIsNone(cancellation.stock_completed_at)
        with self.assertRaises(CancellationError):retry_stock(self.order.pk,self.admin)
        apply_verified_event(self.result()); apply_verified_event(self.result())
        cancellation.refresh_from_db();self.assertIsNotNone(cancellation.stock_completed_at)
        self.variant.refresh_from_db();self.assertEqual(self.variant.quantity,7)

    def test_failed_refund_never_restocks_or_creates_another_attempt(self):
        cancellation,create=self.cancel('failed')
        with patch('retours.refunds.stripe.Refund.retrieve',return_value=self.result('failed')), \
             patch('retours.refunds.stripe.Refund.create') as again:
            reconcile_cancellation(self.order.pk,self.admin)
        again.assert_not_called();self.variant.refresh_from_db();self.assertEqual(self.variant.quantity,5)
        self.assertEqual(cancellation.refund.refund_state,'failed')

    def test_timeout_persists_attempt_then_recovers_same_refund(self):
        token=preview(self.order)['token']
        with patch('retours.refunds.stripe.checkout.Session.retrieve',return_value=self.session), \
             patch('retours.refunds.stripe.Refund.list',return_value={'data':[]}), \
             patch('retours.refunds.stripe.Refund.create',side_effect=stripe.APIConnectionError('timeout')):
            cancellation=request_cancellation(self.order.pk,self.admin,'Client',token,True)
        self.assertEqual(cancellation.refund.refund_state,'submitting');self.assertTrue(cancellation.refund_error)
        with patch('retours.refunds.stripe.checkout.Session.retrieve',return_value=self.session), \
             patch('retours.refunds.stripe.Refund.list',return_value={'data':[self.result()]}), \
             patch('retours.refunds.stripe.Refund.create') as again:
            cancellation=reconcile_cancellation(self.order.pk,self.admin)
        again.assert_not_called();self.assertEqual(cancellation.refund.refund_state,'succeeded')
        self.variant.refresh_from_db();self.assertEqual(self.variant.quantity,7)

    def test_external_refund_needs_control_no_second_refund_or_stock(self):
        token=preview(self.order)['token']
        with patch('retours.refunds.stripe.checkout.Session.retrieve',return_value=self.session), \
             patch('retours.refunds.stripe.Refund.list',return_value={'data':[{'id':'re_external','metadata':{}}]}), \
             patch('retours.refunds.stripe.Refund.create') as create:
            cancellation=request_cancellation(self.order.pk,self.admin,'Client',token,True)
        create.assert_not_called();self.assertEqual(cancellation.refund.refund_state,'review')
        self.variant.refresh_from_db();self.assertEqual(self.variant.quantity,5)

    def test_stock_problem_does_not_erase_confirmed_refund(self):
        cancellation,_=self.cancel('pending')
        self.variant.size='XL';self.variant.save()
        apply_verified_event(self.result())
        cancellation.refresh_from_db();self.assertEqual(cancellation.refund.refund_state,'succeeded')
        self.assertTrue(cancellation.stock_error);self.assertIsNone(cancellation.stock_completed_at)
        self.variant.refresh_from_db();self.assertEqual(self.variant.quantity,5)
        self.variant.size='M';self.variant.save()
        with patch('retours.refunds.stripe.Refund.create') as create:
            retry_stock(self.order.pk,self.admin);retry_stock(self.order.pk,self.admin)
        create.assert_not_called();self.variant.refresh_from_db();self.assertEqual(self.variant.quantity,7)

    def test_stock_database_failure_rolls_back_stock_only(self):
        cancellation,_=self.cancel('pending')
        with patch('annulations.services.Variant.save', side_effect=IntegrityError('stock conflict')):
            apply_verified_event(self.result())
        cancellation.refresh_from_db();self.assertEqual(cancellation.refund.refund_state,'succeeded')
        self.assertTrue(cancellation.stock_error);self.assertIsNone(cancellation.stock_completed_at)
        retry_stock(self.order.pk,self.admin);self.variant.refresh_from_db();self.assertEqual(self.variant.quantity,7)

    def test_unexpected_stock_audit_failure_preserves_refund_and_rolls_back_increment(self):
        cancellation,_=self.cancel('pending')
        with patch('annulations.services.FulfillmentEvent.objects.create', side_effect=RuntimeError('audit unavailable')):
            apply_verified_event(self.result())
        cancellation.refresh_from_db();self.assertEqual(cancellation.refund.refund_state,'succeeded')
        self.assertTrue(cancellation.stock_error);self.assertIsNone(cancellation.stock_completed_at)
        self.variant.refresh_from_db();self.assertEqual(self.variant.quantity,5)
        retry_stock(self.order.pk,self.admin);self.variant.refresh_from_db();self.assertEqual(self.variant.quantity,7)

    def test_free_order_requires_no_bank_refund_but_restores_stock(self):
        self.order.total_paid=0;self.order.return_shipping_amount=0;self.order.paid_line_totals=[0];self.order.save()
        cancellation,create=self.cancel()
        create.assert_not_called();self.assertEqual(cancellation.refund.refund_state,'succeeded')
        self.variant.refresh_from_db();self.assertEqual(self.variant.quantity,7)

    def test_bad_provider_amount_does_not_restock(self):
        cancellation,_=self.cancel('pending')
        from retours.services import ReturnError
        with self.assertRaises(ReturnError):apply_verified_event(self.result(amount=8000))
        cancellation.refresh_from_db();self.assertEqual(cancellation.refund.refund_state,'pending')
        self.variant.refresh_from_db();self.assertEqual(self.variant.quantity,5)

    def test_preview_is_read_only_and_stale_or_forged_tokens_rejected(self):
        with patch('stripe.checkout.Session.retrieve') as retrieve:
            value=preview(self.order)
        retrieve.assert_not_called();self.assertTrue(value['eligible'])
        self.assertFalse(Cancellation.objects.exists());self.assertFalse(Fulfillment.objects.exists())
        self.variant.quantity=6;self.variant.save()
        for token in (value['token'],'forged'):
            with self.subTest(token=token),self.assertRaises(CancellationError):
                request_cancellation(self.order.pk,self.admin,'Client',token,True)
        self.assertFalse(ReturnRequest.objects.exists())

    def test_shipped_returned_unknown_stock_and_wrong_reservations_block(self):
        fulfilled=Fulfillment.objects.create(order=self.order,state='shipped',ready_at=timezone.now(),shipped_at=timezone.now())
        self.assertFalse(preview(self.order)['eligible']);fulfilled.delete()
        record=ReturnRequest.objects.create(order=self.order,submission_id=uuid.uuid4(),amount=100)
        self.assertFalse(preview(self.order)['eligible']);record.delete()
        self.variant.quantity=None;self.variant.reserved=0;self.variant.save()
        self.assertFalse(preview(self.order)['eligible'])
        self.variant.quantity=5;self.variant.save();self.hold.state='held';self.hold.save()
        self.assertFalse(preview(self.order)['eligible'])

    def test_unknown_discount_never_calls_stripe_from_preview(self):
        self.order.paid_line_totals=None;self.order.save()
        with patch('stripe.checkout.Session.retrieve') as retrieve:
            value=preview(self.order)
        self.assertFalse(value['eligible']);retrieve.assert_not_called()

    def test_permission_confirmation_and_reason_required(self):
        token=preview(self.order)['token']
        with self.assertRaises(PermissionDenied):request_cancellation(self.order.pk,self.user,'Client',token,True)
        for reason,confirmed in [('',True),('Client',False),('x'*1001,True)]:
            with self.assertRaises(CancellationError):request_cancellation(self.order.pk,self.admin,reason,token,confirmed)
        self.assertFalse(Cancellation.objects.exists())

    def test_disabled_payment_records_cancellation_then_can_resume(self):
        with override_settings(PAYMENTS_ENABLED=False):
            cancellation,create=self.cancel()
        create.assert_not_called();self.assertEqual(cancellation.refund.refund_state,'none')
        self.assertTrue(cancellation.refund_error);self.assertEqual(self.order.fulfillment.state,'cancelled')
        with patch('retours.refunds.stripe.checkout.Session.retrieve',return_value=self.session), \
             patch('retours.refunds.stripe.Refund.list',return_value={'data':[]}), \
             patch('retours.refunds.stripe.Refund.create',side_effect=lambda **data:self.result()):
            cancellation=reconcile_cancellation(self.order.pk,self.admin)
        self.assertEqual(cancellation.refund.refund_state,'succeeded')

    def test_demo_does_not_touch_bank_or_real_stock(self):
        self.order.status='demo';self.order.save()
        with patch('stripe.Refund.create') as create:
            cancellation=request_cancellation(self.order.pk,self.admin,'Essai',preview(self.order)['token'],True)
        create.assert_not_called();self.assertIsNotNone(cancellation.stock_completed_at)
        self.variant.refresh_from_db();self.assertEqual(self.variant.quantity,5)
        self.assertEqual(cancellation.refund.refund_state,'succeeded')
