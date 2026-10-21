"""Local convenience never proves mailbox ownership or claims email-only value."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase, override_settings
from django.urls import reverse

from config.encrypted_fields import email_digest
from connexion.email_verification import email_verified
from connexion.models import EmailVerification
from connexion.test_helpers import verify_test_user
from .access import (
    benefits_access_allowed,
    benefits_for,
    customer_matches,
    email_confirmation_required,
)
from .models import CustomerBenefit
from .services import BenefitError, quote, save_benefit


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True,
                   EMAIL_DELIVERY_ENABLED=False, EMAIL_TEST_MODE=False)
class LocalCustomerBenefitAccessTests(TestCase):
    def setUp(self):
        users = get_user_model().objects
        self.seller = users.create_user('local-benefit-seller', email='seller@example.test', is_staff=True)
        self.customer = users.create_user('local-benefit-customer', email='customer@example.test')
        self.other = users.create_user('local-benefit-other', email='other@example.test')

    def benefit(self, **changes):
        fields = {
            'recipient_email': self.customer.email,
            'label': 'Avoir attribué par la boutique',
            'kind': 'percent', 'value': Decimal('15'),
            'minimum_subtotal': Decimal('70'),
            'user': self.customer, 'bound_user_id': self.customer.pk,
        }
        fields.update(changes)
        return CustomerBenefit.objects.create(**fields)

    def assert_rejected(self, benefit, user=None):
        account = user if user is not None else self.customer
        self.assertFalse(customer_matches(benefit, account))
        self.assertFalse(benefits_for(account).filter(pk=benefit.pk).exists())
        with self.assertRaises(BenefitError):
            quote(benefit, account, 8000)

    def test_explicitly_assigned_local_code_is_visible_and_applicable_without_fake_confirmation(self):
        benefit = save_benefit(CustomerBenefit(
            recipient_email=self.customer.email, label='Merci pour votre fidélité',
            kind='percent', value=15, minimum_subtotal=70,
        ), self.seller, notify=False)
        self.assertEqual(benefit.user_id, self.customer.pk)
        self.assertEqual(benefit.bound_user_id, self.customer.pk)
        self.assertEqual(benefit.recipient_email_hash, email_digest(self.customer.email))
        self.assertFalse(email_confirmation_required())
        self.assertTrue(benefits_access_allowed(self.customer))
        self.assertTrue(customer_matches(benefit, self.customer))
        self.assertEqual(list(benefits_for(self.customer)), [benefit])
        self.assertEqual(quote(benefit, self.customer, 8000)['discount_amount'], 1200)
        self.assertFalse(email_verified(self.customer))
        self.assertFalse(EmailVerification.objects.exists())

    def test_local_customer_page_shows_only_explicitly_assigned_code_without_confirming_email(self):
        assigned = self.benefit()
        unbound = self.benefit(user=None, bound_user_id=None)
        foreign = self.benefit(user=self.other, bound_user_id=self.other.pk,
                               recipient_email=self.other.email)
        self.client.force_login(self.customer, backend='connexion.backends.EmailOrUsernameBackend')
        response = self.client.get(reverse('espace:credits'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, assigned.code)
        self.assertNotContains(response, unbound.code)
        self.assertNotContains(response, foreign.code)
        self.assertFalse(EmailVerification.objects.exists())
        self.assertFalse(email_verified(self.customer))

    def test_email_only_code_cannot_be_claimed_by_unverified_local_account(self):
        benefit = self.benefit(user=None, bound_user_id=None)
        self.assert_rejected(benefit)
        benefit.refresh_from_db()
        self.assertIsNone(benefit.user_id)
        self.assertIsNone(benefit.bound_user_id)
        self.assertFalse(EmailVerification.objects.exists())

    def test_another_account_cannot_see_or_use_personal_code(self):
        self.assert_rejected(self.benefit(), self.other)

    def test_local_access_requires_both_current_and_permanent_account_attachment(self):
        for changes in ({'bound_user_id': None}, {'bound_user_id': self.other.pk},
                        {'user': None}, {'user': self.other}):
            with self.subTest(changes=changes):
                self.assert_rejected(self.benefit(**changes))

    def test_matching_attachments_do_not_override_recipient_email_mismatch(self):
        self.assert_rejected(self.benefit(recipient_email=self.other.email))

    def test_changing_account_email_hides_previously_assigned_code(self):
        benefit = self.benefit()
        self.customer.email = 'changed@example.test'
        self.customer.save(update_fields=['email'])
        self.assert_rejected(benefit)
        benefit.refresh_from_db()
        self.assertEqual(benefit.recipient_email, 'customer@example.test')
        self.assertEqual(benefit.user_id, self.customer.pk)

    def test_stale_verified_email_does_not_bypass_address_matching_in_local_mode(self):
        benefit = self.benefit()
        verify_test_user(self.customer)
        self.customer.email = 'new-after-confirmation@example.test'
        self.customer.save(update_fields=['email'])
        self.assertFalse(email_verified(self.customer))
        self.assert_rejected(benefit)

    def test_missing_current_email_cannot_use_bound_value(self):
        benefit = self.benefit()
        self.customer.email = ''
        self.customer.save(update_fields=['email'])
        self.assert_rejected(benefit)

    def test_deleted_accounts_value_is_not_inherited_by_new_owner_of_same_email(self):
        benefit = self.benefit()
        old_id, old_email = self.customer.pk, self.customer.email
        self.customer.delete()
        replacement = get_user_model().objects.create_user('local-reused-email', email=old_email)
        benefit.refresh_from_db()
        self.assertIsNone(benefit.user_id)
        self.assertEqual(benefit.bound_user_id, old_id)
        self.assert_rejected(benefit, replacement)
        self.assertFalse(EmailVerification.objects.exists())

    def test_inactive_account_is_rejected_even_with_valid_attachment_and_local_bypass(self):
        benefit = self.benefit()
        self.customer.is_active = False
        self.customer.save(update_fields=['is_active'])
        self.assertFalse(benefits_access_allowed(self.customer))
        self.assert_rejected(benefit)

    def test_guest_is_rejected_even_in_local_bypass_mode(self):
        guest = AnonymousUser()
        self.assertFalse(benefits_access_allowed(guest))
        self.assert_rejected(self.benefit(), guest)

    def test_unverified_bound_code_is_blocked_unless_both_local_flags_are_enabled(self):
        benefit = self.benefit()
        for flags in ({'DEBUG': False, 'LOCAL_SKIP_EMAIL_VERIFICATION': True},
                      {'DEBUG': True, 'LOCAL_SKIP_EMAIL_VERIFICATION': False},
                      {'DEBUG': False, 'LOCAL_SKIP_EMAIL_VERIFICATION': False}):
            with self.subTest(flags=flags), override_settings(**flags):
                self.assertTrue(email_confirmation_required())
                self.assertFalse(benefits_access_allowed(self.customer))
                self.assert_rejected(benefit)
        self.assertFalse(EmailVerification.objects.exists())

    def test_real_confirmation_keeps_email_only_codes_available_in_production(self):
        benefit = self.benefit(user=None, bound_user_id=None)
        verify_test_user(self.customer)
        with override_settings(DEBUG=False, LOCAL_SKIP_EMAIL_VERIFICATION=True):
            self.assertTrue(email_confirmation_required())
            self.assertTrue(benefits_access_allowed(self.customer))
            self.assertTrue(customer_matches(benefit, self.customer))
            self.assertTrue(benefits_for(self.customer).filter(pk=benefit.pk).exists())
            self.assertEqual(quote(benefit, self.customer, 8000)['discount_amount'], 1200)
        benefit.refresh_from_db()
        self.assertIsNone(benefit.user_id, 'a read-only quote must not claim the code')
        self.assertTrue(email_verified(self.customer))

    def test_real_confirmation_never_transfers_a_code_bound_to_another_account(self):
        benefit = self.benefit(user=None, bound_user_id=self.other.pk)
        verify_test_user(self.customer)
        self.assert_rejected(benefit)
