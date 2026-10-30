from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(SECURE_SSL_REDIRECT=False)
class PaymentTestInterfaceTests(TestCase):
    def test_guide_is_not_exposed_on_normal_shop(self):
        response = self.client.get(reverse('paiement:test_guide'))
        self.assertEqual(response.status_code, 404)

    @override_settings(PAYMENT_TEST_MODE=True, PAYMENT_TEST_ISOLATED=True)
    def test_test_guide_is_identified_and_not_indexable(self):
        response = self.client.get(reverse('paiement:test_guide'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'MODE TEST')
        self.assertContains(response, '4242 4242 4242 4242')
        self.assertIn('noindex', response['X-Robots-Tag'])

    @override_settings(PAYMENT_TEST_MODE=True, PAYMENT_TEST_ISOLATED=False)
    def test_flag_alone_does_not_expose_test_guide(self):
        self.assertEqual(self.client.get(reverse('paiement:test_guide')).status_code, 404)
