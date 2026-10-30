"""Isolation and Stripe mode checks use fakes only: no external API is called."""
import hashlib
import hmac
import json
import time
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from core.models import Product
from paiement.models import Order
from paiement.stock_checkout import get_session, settle
from paiement.test_mode import (
    UnsafePaymentTestMode, assert_isolated_test_environment,
    assert_test_runtime, assert_test_object,
)
from stocks.models import Variant
from stocks.reservations import reserve, StockUnavailable


DATABASE_NAME = 'caverne_payments_test_0123456789ab'
DATABASE = {'ENGINE': 'django.db.backends.postgresql', 'NAME': DATABASE_NAME,
            'USER': DATABASE_NAME + '_app'}
TEST_SETTINGS = {
    'PAYMENT_TEST_MODE': True,
    'PAYMENT_TEST_ISOLATED': True,
    'PAYMENT_TEST_DATABASE_NAME': DATABASE_NAME,
    'SETTINGS_MODULE': 'config.payment_test_settings',
    'DEBUG': True,
    'SITE_URL': 'http://127.0.0.1:8010',
    'EMAIL_BACKEND': 'courriels.backend.LocalMailboxBackend',
    'EMAIL_TEST_MODE': True,
    'EMAIL_DELIVERY_ENABLED': True,
    'STRIPE_SECRET_KEY': 'sk_test_unit_tests_only',
    'STRIPE_WEBHOOK_SECRET': 'whsec_unit_tests_only',
    'STRIPE_SHIPPING_RATE': 'shr_test',
    'PAYMENTS_ENABLED': True,
    'SHOP_READY': False,
}


def isolated_overrides(**values):
    # Django's nested UserSettingsHolder deliberately resets SETTINGS_MODULE.
    return override_settings(**{'SETTINGS_MODULE': 'config.payment_test_settings', **values})


class StripeObject(dict):
    def __getattr__(self, key):
        return self[key]


class IsolatedRuntimeMixin:
    def setUp(self):
        super().setUp()
        # Only the guard's read of connection metadata is replaced. Database
        # writes below still go to Django's disposable ordinary test database.
        self.database = dict(DATABASE)
        mocked = {'default': SimpleNamespace(settings_dict=self.database)}
        self.connection_patch = patch('paiement.test_mode.connections', mocked)
        self.connection_patch.start()
        self.addCleanup(self.connection_patch.stop)


@override_settings(**TEST_SETTINGS)
class PaymentTestRuntimeTests(IsolatedRuntimeMixin, SimpleTestCase):
    def test_isolation_can_be_checked_before_stripe_key_is_supplied(self):
        with isolated_overrides(STRIPE_SECRET_KEY=''):
            self.assertTrue(assert_isolated_test_environment())
            with self.assertRaises(UnsafePaymentTestMode):
                assert_test_runtime()

    def test_flag_alone_cannot_bypass_shop_launch_requirements(self):
        for values in (
            {'PAYMENT_TEST_ISOLATED': False},
            {'SETTINGS_MODULE': 'config.settings'},
            {'DEBUG': False},
            {'PAYMENT_TEST_MODE': 'true'},
        ):
            with self.subTest(values=values), isolated_overrides(**values):
                with self.assertRaises(UnsafePaymentTestMode):
                    assert_test_runtime()

    def test_only_local_url_and_captured_emails_are_accepted(self):
        for value in ('https://la-cavernedesmonts.fr', 'http://127.0.0.1.evil.test',
                      'http://user@localhost:8010', 'http://localhost:99999',
                      'http://localhost:8010/path', 'http://localhost:8010?redirect=1'):
            with self.subTest(url=value), isolated_overrides(SITE_URL=value):
                with self.assertRaises(UnsafePaymentTestMode):
                    assert_test_runtime()
        for values in ({'EMAIL_TEST_MODE': False}, {'EMAIL_DELIVERY_ENABLED': False},
                       {'EMAIL_BACKEND': 'django.core.mail.backends.smtp.EmailBackend'}):
            with self.subTest(values=values), isolated_overrides(**values):
                with self.assertRaises(UnsafePaymentTestMode):
                    assert_test_runtime()

    def test_connection_must_match_the_dedicated_database_and_role(self):
        for values in ({'NAME': 'caverne'}, {'USER': 'production_user'},
                       {'ENGINE': 'django.db.backends.sqlite3'}):
            with self.subTest(values=values), patch.dict(self.database, values):
                with self.assertRaises(UnsafePaymentTestMode):
                    assert_test_runtime()

    def test_live_and_missing_keys_are_refused(self):
        for key in ('sk_live_secret', 'rk_live_secret', 'rkcs_live_secret', '', 'other', 'sk_test_'):
            with self.subTest(key=key), isolated_overrides(STRIPE_SECRET_KEY=key):
                with self.assertRaises(UnsafePaymentTestMode):
                    assert_test_runtime()
        for key in ('sk_test_secret', 'rk_test_secret', 'rkcs_test_secret'):
            with self.subTest(key=key), isolated_overrides(STRIPE_SECRET_KEY=key):
                self.assertTrue(assert_test_runtime())

    def test_stripe_must_explicitly_confirm_test_mode(self):
        for value in ({}, {'livemode': True}, {'livemode': 0}, {'livemode': None},
                      {'livemode': 'false'}, None):
            with self.subTest(value=value), self.assertRaises(UnsafePaymentTestMode):
                assert_test_object(value)
        assert_test_object({'livemode': False})

    def test_ready_allows_captured_mail_only_in_validated_test_environment(self):
        from paiement.views import ready
        with patch('paiement.shipping.shipping_ready', return_value=True), \
                patch('espace.legal.sales_ready', return_value=False) as legal:
            self.assertTrue(ready())
            legal.assert_not_called()
            with isolated_overrides(PAYMENT_TEST_ISOLATED=False):
                self.assertFalse(ready())
            with isolated_overrides(PAYMENT_TEST_MODE=False):
                self.assertFalse(ready())
            with isolated_overrides(STRIPE_WEBHOOK_SECRET=''):
                self.assertFalse(ready())

    @isolated_overrides(PAYMENT_TEST_MODE=False)
    def test_existing_checkout_and_response_contract_are_unchanged(self):
        self.assertFalse(assert_test_runtime())
        assert_test_object({})


@override_settings(**TEST_SETTINGS)
class PaymentTestFlowTests(IsolatedRuntimeMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.intent_patch = patch('paiement.test_mode.stripe.PaymentIntent.retrieve',
                                  return_value={'id': 'pi_test_only', 'livemode': False})
        self.intent_retrieve = self.intent_patch.start()
        self.addCleanup(self.intent_patch.stop)
        self.product = Product.objects.create(name='Polaire de test', slug='polaire-test', price='36.00')
        self.variant = Variant.objects.create(product=self.product, size='M', color='Bleu', quantity=5)
        self.order = reserve('isolated-test-browser', None, [{
            'product': self.product.pk, 'variant': self.variant.pk, 'quantity': 2,
            'unit_amount': 3600, 'name': self.product.name, 'size': 'M', 'color': 'Bleu',
        }])

    def opened(self, **changes):
        return StripeObject(id='cs_test_isolated', url='https://checkout.stripe.com/test',
                            status='open', payment_status='unpaid', livemode=False, **changes)

    def paid(self, **changes):
        data = dict(id='cs_test_isolated', status='complete', payment_status='paid', livemode=False,
                    currency='eur', amount_subtotal=7200, amount_total=7800,
                    total_details={'amount_shipping': 600, 'amount_discount': 0},
                    client_reference_id=str(self.order.pk), metadata={'order_id': str(self.order.pk)},
                    customer_details={'email': 'buyer@example.test'}, payment_intent='pi_test_only')
        data.update(changes)
        return data

    def start(self):
        with patch('paiement.stock_checkout.stripe.checkout.Session.create', return_value=self.opened()):
            get_session(self.order.pk)
        self.order.refresh_from_db()

    def signed_event(self, **changes):
        event = {'type': 'checkout.session.completed', 'livemode': False,
                 'data': {'object': {'id': 'cs_test_isolated', 'livemode': False}}}
        event.update(changes)
        payload = json.dumps(event).encode()
        timestamp = str(int(time.time()))
        signature = hmac.new(b'whsec_unit_tests_only', timestamp.encode() + b'.' + payload,
                             hashlib.sha256).hexdigest()
        return self.client.post('/paiement/webhook/', payload, content_type='application/json',
                                HTTP_STRIPE_SIGNATURE=f't={timestamp},v1={signature}')

    def assert_pending_stock(self):
        self.order.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(self.order.status, 'pending')
        self.assertEqual((self.variant.quantity, self.variant.reserved), (5, 2))
        self.assertEqual(self.order.reservations.get().state, 'held')

    @isolated_overrides(STRIPE_SECRET_KEY='sk_live_not_allowed')
    def test_invalid_runtime_is_rejected_before_checkout_payload_or_api(self):
        with patch('paiement.stock_checkout.stripe.checkout.Session.create') as create:
            with self.assertRaises(StockUnavailable):
                get_session(self.order.pk)
        create.assert_not_called()
        self.order.refresh_from_db()
        self.assertEqual(self.order.checkout_payload, {})
        self.assert_pending_stock()

    def test_live_created_session_is_never_attached_to_order(self):
        response = self.opened()
        response['livemode'] = True
        with patch('paiement.stock_checkout.stripe.checkout.Session.create', return_value=response):
            with self.assertRaises(StockUnavailable):
                get_session(self.order.pk)
        self.order.refresh_from_db()
        self.assertFalse(self.order.stripe_session_id)
        self.assert_pending_stock()

    def test_live_or_missing_mode_cannot_consume_or_release_stock(self):
        self.start()
        for mode in (True, None, 0):
            for state in ('paid', 'expired'):
                response = self.paid(livemode=mode)
                if state == 'expired':
                    response.update(status='expired', payment_status='unpaid')
                with self.subTest(mode=mode, state=state), self.assertRaises(StockUnavailable):
                    settle(response)
                self.assert_pending_stock()

    def test_signed_live_or_malformed_event_is_rejected_before_retrieval(self):
        self.start()
        for changes in ({'livemode': True}, {'livemode': None}, {'livemode': 0},
                        {'data': None}, {'data': {}}, {'data': {'object': {}}},
                        {'data': {'object': {'id': 'cs_live', 'livemode': True}}}):
            with self.subTest(changes=changes), \
                    patch('paiement.views.stripe.checkout.Session.retrieve') as retrieve:
                self.assertEqual(self.signed_event(**changes).status_code, 400)
                retrieve.assert_not_called()
                self.assert_pending_stock()

    def test_test_event_cannot_settle_live_retrieved_session(self):
        self.start()
        with patch('paiement.views.stripe.checkout.Session.retrieve', return_value=self.paid(livemode=True)):
            self.assertEqual(self.signed_event().status_code, 400)
        self.assert_pending_stock()

    def test_confirmed_test_payment_remains_idempotent(self):
        self.start()
        with patch('paiement.views.stripe.checkout.Session.retrieve', return_value=self.paid()):
            self.assertEqual(self.signed_event().status_code, 200)
            self.assertEqual(self.signed_event().status_code, 200)
        self.order.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(self.order.status, 'paid')
        self.assertEqual((self.variant.quantity, self.variant.reserved), (3, 0))
        self.assertEqual(self.order.total_paid, 7800)
        self.assertEqual(self.order.reservations.get().state, 'sold')

    def test_confirmed_test_expiration_releases_reservation(self):
        self.start()
        self.assertTrue(settle(self.paid(status='expired', payment_status='unpaid')))
        self.order.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(self.order.status, 'expired')
        self.assertEqual((self.variant.quantity, self.variant.reserved), (5, 0))

    def refund_record(self):
        from retours.services import submit
        self.start()
        settle(self.paid())
        record = submit(self.order.pk, {0: 1}, uuid.uuid4())
        record.refund_state = 'submitting'
        record.refund_started_at = timezone.now()
        record.save(update_fields=['refund_state', 'refund_started_at'])
        return record

    def refunded(self, record, **changes):
        result = {'id': 're_test_only', 'object': 'refund', 'status': 'succeeded',
                  'amount': record.amount, 'currency': 'eur', 'payment_intent': 'pi_test_only',
                  'metadata': {'return_id': str(record.pk)}}
        result.update(changes)
        return result

    def test_refund_creation_requires_verified_test_payment_intent(self):
        from retours.refunds import reconcile
        from retours.services import ReturnError
        record = self.refund_record()
        for parent in ({'id': 'pi_test_only', 'livemode': True},
                       {'id': 'pi_test_only'}, {'id': 'pi_test_only', 'livemode': 0},
                       {'id': 'pi_wrong', 'livemode': False}):
            self.intent_retrieve.return_value = parent
            with self.subTest(parent=parent), \
                    patch('retours.refunds.stripe.checkout.Session.retrieve', return_value=self.paid()), \
                    patch('retours.refunds.stripe.Refund.list') as listed, \
                    patch('retours.refunds.stripe.Refund.create') as create:
                with self.assertRaises(ReturnError):
                    reconcile(record.pk)
                listed.assert_not_called()
                create.assert_not_called()
            record.refresh_from_db()
            self.assertEqual(record.refund_state, 'submitting')
            self.assertFalse(record.payment_intent)

    def test_refund_without_livemode_is_verified_from_its_parent_payment(self):
        from retours.refunds import apply_verified_event
        record = self.refund_record()
        refund = self.refunded(record)
        self.assertNotIn('livemode', refund)
        with patch('retours.refunds.stripe.checkout.Session.retrieve', return_value=self.paid()):
            self.assertTrue(apply_verified_event(refund))
        record.refresh_from_db()
        self.assertEqual(record.refund_state, 'succeeded')
        self.intent_retrieve.assert_called_with('pi_test_only', api_key=TEST_SETTINGS['STRIPE_SECRET_KEY'])

    def test_refund_event_without_livemode_uses_verified_test_parent(self):
        record = self.refund_record()
        with patch('paiement.views.stripe.Refund.retrieve', return_value=self.refunded(record)), \
                patch('retours.refunds.stripe.checkout.Session.retrieve', return_value=self.paid()):
            response = self.signed_event(type='refund.updated', data={'object': self.refunded(record)})
        self.assertEqual(response.status_code, 200)
        record.refresh_from_db()
        self.assertEqual(record.refund_state, 'succeeded')

    def test_refund_reconciliation_refuses_live_session_before_creating_refund(self):
        from retours.refunds import reconcile
        from retours.services import ReturnError
        record = self.refund_record()
        with patch('retours.refunds.stripe.checkout.Session.retrieve', return_value=self.paid(livemode=True)), \
                patch('retours.refunds.stripe.Refund.create') as create:
            with self.assertRaises(ReturnError):
                reconcile(record.pk)
        create.assert_not_called()
        record.refresh_from_db()
        self.assertEqual(record.refund_state, 'submitting')
        self.assertFalse(record.payment_intent)

    def test_live_refund_cannot_enter_via_retrieve_list_or_create(self):
        from retours.refunds import reconcile
        from retours.services import ReturnError
        record = self.refund_record()
        for source in ('retrieve', 'list', 'old_list', 'create'):
            record.stripe_refund_id = 're_test_only' if source == 'retrieve' else None
            record.refund_started_at = timezone.now() - timedelta(hours=25 if source == 'old_list' else 0)
            record.save(update_fields=['stripe_refund_id', 'refund_started_at'])
            listed = [self.refunded(record, livemode=True)] if source in ('list', 'old_list') else []
            with self.subTest(source=source), \
                    patch('retours.refunds.stripe.checkout.Session.retrieve', return_value=self.paid()), \
                    patch('retours.refunds.stripe.Refund.retrieve', return_value=self.refunded(record, livemode=True)), \
                    patch('retours.refunds.stripe.Refund.list', return_value={'data': listed}), \
                    patch('retours.refunds.stripe.Refund.create', return_value=self.refunded(record, livemode=True)) as create:
                with self.assertRaises(ReturnError):
                    reconcile(record.pk)
                if source != 'create':
                    create.assert_not_called()
            record.refresh_from_db()
            self.assertEqual(record.refund_state, 'submitting')
            self.assertIsNone(record.refunded_at)
            self.assertFalse(record.payment_intent)

    def test_refund_webhook_rejects_live_object_and_live_fallback_session(self):
        from retours.refunds import apply_verified_event
        from retours.services import ReturnError
        record = self.refund_record()
        with self.assertRaises(ReturnError):
            apply_verified_event(self.refunded(record, livemode=True))
        with patch('retours.refunds.stripe.checkout.Session.retrieve', return_value=self.paid(livemode=True)):
            with self.assertRaises(ReturnError):
                apply_verified_event(self.refunded(record))
        record.refresh_from_db()
        self.assertEqual(record.refund_state, 'submitting')
        self.assertFalse(record.payment_intent)
        self.assertFalse(record.stripe_refund_id)

    def test_confirmed_test_refund_can_complete_once(self):
        from retours.refunds import reconcile, initiate
        record = self.refund_record()
        with patch('retours.refunds.stripe.checkout.Session.retrieve', return_value=self.paid()), \
                patch('retours.refunds.stripe.Refund.list', return_value={'data': []}), \
                patch('retours.refunds.stripe.Refund.create', return_value=self.refunded(record)) as create:
            reconcile(record.pk)
            initiate(record.pk, None)
        create.assert_called_once()
        record.refresh_from_db()
        self.assertEqual(record.refund_state, 'succeeded')
        self.assertEqual(record.stripe_refund_id, 're_test_only')
