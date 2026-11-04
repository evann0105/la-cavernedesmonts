"""Exercise the real Stripe HTTP transport without making a network request."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import certifi
import requests
import stripe
from django.apps import apps
from django.test import SimpleTestCase
from stripe._http_client import RequestsClient


class StripeTrustStoreTests(SimpleTestCase):
    def test_django_startup_selects_certifi_without_disabling_verification(self):
        with patch.multiple(stripe, ca_bundle_path='/old-sdk-bundle.pem', verify_ssl_certs=False):
            apps.get_app_config('paiement').ready()
            self.assertEqual(stripe.ca_bundle_path, certifi.where())
            self.assertIs(stripe.verify_ssl_certs, True)

    def test_checkout_retrieval_coupon_and_refund_use_the_selected_bundle(self):
        session = Mock(spec=requests.Session)
        session.request.return_value = SimpleNamespace(
            content=b'{"id":"test_tls","object":"test"}', status_code=200, headers={},
        )
        transport = RequestsClient(session=session, verify_ssl_certs=True)
        with patch.multiple(stripe, ca_bundle_path='/old-sdk-bundle.pem',
                            verify_ssl_certs=False, default_http_client=transport):
            apps.get_app_config('paiement').ready()
            stripe.checkout.Session.create(api_key='sk_test_tls_only', mode='payment', line_items=[])
            stripe.checkout.Session.retrieve('cs_test_tls', api_key='sk_test_tls_only')
            stripe.Coupon.create(api_key='sk_test_tls_only', amount_off=500,
                                 currency='eur', duration='once')
            stripe.Refund.create(api_key='sk_test_tls_only', payment_intent='pi_test_tls')
        self.assertEqual(session.request.call_count, 4)
        paths = set()
        for call in session.request.call_args_list:
            self.assertEqual(call.kwargs['verify'], certifi.where())
            self.assertNotEqual(call.kwargs['verify'], False)
            paths.add(call.args[1].split('api.stripe.com', 1)[1])
        self.assertEqual(paths, {'/v1/checkout/sessions', '/v1/checkout/sessions/cs_test_tls',
                                 '/v1/coupons', '/v1/refunds'})

    def test_invalid_certificate_is_rejected_without_an_insecure_retry(self):
        session = Mock(spec=requests.Session)
        session.request.side_effect = requests.exceptions.SSLError('Synthetic invalid certificate')
        transport = RequestsClient(session=session, verify_ssl_certs=True)
        with self.assertRaises(stripe.APIConnectionError):
            transport.request('get', 'https://api.stripe.com/v1/checkout/sessions/cs_test_tls', {})
        session.request.assert_called_once()
        self.assertEqual(session.request.call_args.kwargs['verify'], certifi.where())
