from datetime import timedelta
from decimal import Decimal
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from connexion.models import EmailVerification
from connexion.test_helpers import verify_test_user
from espace.models import CreditNote
from paiement.models import Order
from .models import BenefitRedemption, CustomerBenefit


@override_settings(EMAIL_DELIVERY_ENABLED=False, EMAIL_TEST_MODE=False,
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   LOCAL_SKIP_EMAIL_VERIFICATION=False)
class CustomerBenefitUITests(TestCase):
    def setUp(self):
        self.seller = get_user_model().objects.create_superuser('benefit-seller', email='seller@example.test', password='test-password')
        self.customer = get_user_model().objects.create_user('benefit-customer', email='client@example.test', first_name='Camille', last_name='Dupont')
        self.other = get_user_model().objects.create_user('other-benefit-client', email='other@example.test')
        for user in (self.seller, self.customer, self.other):
            verify_test_user(user)
        self.client.force_login(self.seller, backend='connexion.backends.EmailOrUsernameBackend')
        self.benefit = CustomerBenefit.objects.create(recipient_email=self.customer.email, label='Merci Camille', kind='percent', value=15, minimum_subtotal=70, created_by=self.seller)

    def data(self, **overrides):
        data = {
            'recipient_email': self.customer.email, 'label': 'Merci pour votre fidélité',
            'kind': 'percent', 'value': '15', 'minimum_subtotal': '70',
            'starts_at': '', 'ends_at': '', 'active': 'on',
        }
        data.update(overrides)
        return data

    def test_admin_pages_are_private_permission_protected_and_not_cached(self):
        url = reverse('avoirs:management')
        self.assertEqual(Client().get(url).status_code, 302)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertContains(response, 'Avoirs clients')
        self.client.force_login(self.customer, backend='connexion.backends.EmailOrUsernameBackend')
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(reverse('avoirs:create'), self.data()).status_code, 403)

    def test_create_generates_immutable_code_and_ignores_privilege_injection(self):
        response = self.client.post(reverse('avoirs:create'), self.data(code='CHOSEN-CODE', user=self.other.pk, created_by=self.other.pk))
        self.assertEqual(response.status_code, 302)
        created = CustomerBenefit.objects.exclude(pk=self.benefit.pk).get()
        self.assertNotEqual(created.code, 'CHOSEN-CODE')
        self.assertEqual(created.user, self.customer)
        self.assertEqual(created.created_by, self.seller)
        self.assertEqual(created.kind, 'percent')
        self.assertEqual(created.minimum_subtotal, Decimal('70'))

    def test_create_can_assign_unregistered_email(self):
        self.client.post(reverse('avoirs:create'), self.data(recipient_email='NEW@example.test'))
        benefit = CustomerBenefit.objects.get(recipient_email_hash__isnull=False, label='Merci pour votre fidélité')
        self.assertEqual(benefit.recipient_email, 'new@example.test')
        self.assertIsNone(benefit.user)

    def test_search_encrypted_customer_names_and_exact_email(self):
        response = self.client.get(reverse('avoirs:create'), {'client': 'Camille'})
        self.assertContains(response, self.customer.email)
        self.assertNotContains(response, self.other.email)
        self.assertContains(self.client.get(reverse('avoirs:management'), {'q': 'Camille'}), self.benefit.code)
        self.assertContains(self.client.get(reverse('avoirs:management'), {'q': 'CLIENT@example.test'}), self.benefit.code)
        self.assertNotContains(self.client.get(reverse('avoirs:management'), {'q': self.other.email}), self.benefit.code)

    def test_edit_validates_dates_percentage_and_stale_form(self):
        url = reverse('avoirs:edit', args=[self.benefit.pk])
        now = timezone.localtime()
        payload = self.data(value='101', starts_at=(now + timedelta(days=2)).strftime('%Y-%m-%dT%H:%M'), ends_at=now.strftime('%Y-%m-%dT%H:%M'), revision=self.benefit.updated_at.isoformat())
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, 200)
        self.benefit.refresh_from_db()
        self.assertEqual(self.benefit.value, Decimal('15'))
        response = self.client.post(url, self.data(value='20', revision='stale'))
        self.assertContains(response, 'modifié entre-temps')
        response = self.client.post(url, self.data(value='20', revision=self.benefit.updated_at.isoformat()))
        self.assertEqual(response.status_code, 302)
        self.benefit.refresh_from_db()
        self.assertEqual(self.benefit.value, Decimal('20'))

    def test_recipient_can_be_corrected_without_stale_user_validation(self):
        self.benefit.user = self.customer
        self.benefit.save()
        response = self.client.post(reverse('avoirs:edit', args=[self.benefit.pk]), self.data(recipient_email=self.other.email, revision=self.benefit.updated_at.isoformat()))
        self.assertEqual(response.status_code, 302)
        self.benefit.refresh_from_db()
        self.assertEqual(self.benefit.user, self.other)

    def test_reserved_benefits_cannot_be_edited_or_deactivated(self):
        order = Order.objects.create(user=self.customer, session_key='held', subtotal=7000, items=[])
        BenefitRedemption.objects.create(benefit=self.benefit, order=order, discount_amount=1050)
        self.assertEqual(self.client.get(reverse('avoirs:edit', args=[self.benefit.pk])).status_code, 302)
        self.client.post(reverse('avoirs:toggle', args=[self.benefit.pk]), {'active': '0'})
        self.benefit.refresh_from_db()
        self.assertTrue(self.benefit.active)
        response = self.client.get(reverse('avoirs:detail', args=[self.benefit.pk]))
        self.assertContains(response, 'réservé dans une commande')
        self.assertNotContains(response, 'Modifier les conditions')

    def test_toggle_and_notify_require_post_and_csrf(self):
        for action in ('toggle', 'notify'):
            url = reverse('avoirs:' + action, args=[self.benefit.pk])
            self.assertEqual(self.client.get(url).status_code, 405)
            strict = Client(enforce_csrf_checks=True)
            strict.force_login(self.seller, backend='connexion.backends.EmailOrUsernameBackend')
            self.assertEqual(strict.post(url, {'active': '0'}).status_code, 403)

    def test_notification_is_explicit_and_queued_without_real_delivery(self):
        self.assertEqual(self.benefit.notifications.count(), 0)
        response = self.client.post(reverse('avoirs:notify', args=[self.benefit.pk]))
        self.assertEqual(response.status_code, 302)
        notice = self.benefit.notifications.get()
        self.assertIsNone(notice.sent_at)
        self.assertEqual(notice.payload['recipient'], self.customer.email)
        self.assertContains(self.client.get(reverse('avoirs:detail', args=[self.benefit.pk])), 'en attente d’envoi')

    def test_customer_only_sees_email_bound_benefits_and_own_accounting_documents(self):
        secret = CustomerBenefit.objects.create(recipient_email=self.other.email, label='Private benefit', kind='amount', value=8)
        own_order = Order.objects.create(user=self.customer, session_key='own-credit', subtotal=7000, items=[], status='paid')
        other_order = Order.objects.create(user=self.other, session_key='other-credit', subtotal=7000, items=[], status='paid')
        CreditNote.objects.create(order=own_order, reference='OWN-CREDIT-NOTE', amount=5)
        CreditNote.objects.create(order=other_order, reference='SECRET-CREDIT-NOTE', amount=8)
        self.client.force_login(self.customer, backend='connexion.backends.EmailOrUsernameBackend')
        response = self.client.get(reverse('espace:credits'))
        self.assertContains(response, self.benefit.code)
        self.assertContains(response, 'OWN-CREDIT-NOTE')
        self.assertNotContains(response, secret.code)
        self.assertNotContains(response, 'SECRET-CREDIT-NOTE')
        self.assertContains(response, 'Saisissez votre code')

    @override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True)
    def test_local_access_bypass_does_not_expose_unbound_codes_or_request_confirmation(self):
        EmailVerification.objects.filter(user=self.customer).delete()
        self.assertIsNone(self.benefit.user_id)
        self.assertIsNone(self.benefit.bound_user_id)
        self.client.force_login(self.customer, backend='connexion.backends.EmailOrUsernameBackend')
        response = self.client.get(reverse('espace:credits'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Confirmez votre adresse e-mail')
        self.assertNotContains(response, 'Recevoir mon lien de confirmation')
        self.assertNotContains(response, self.benefit.code)
        self.benefit.refresh_from_db()
        self.assertIsNone(self.benefit.user_id)
        self.assertIsNone(self.benefit.bound_user_id)
        self.assertFalse(EmailVerification.objects.filter(user=self.customer).exists())

    @override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True)
    def test_local_customer_sees_seller_assigned_codes_without_confirming_email(self):
        self.client.post(reverse('avoirs:create'), self.data(label='Attribution explicite en essais'))
        assigned = CustomerBenefit.objects.get(label='Attribution explicite en essais')
        self.assertEqual((assigned.user_id, assigned.bound_user_id), (self.customer.pk, self.customer.pk))
        EmailVerification.objects.filter(user=self.customer).delete()
        self.client.force_login(self.customer, backend='connexion.backends.EmailOrUsernameBackend')
        response = self.client.get(reverse('espace:credits'))
        self.assertContains(response, assigned.code)
        self.assertNotContains(response, self.benefit.code)
        self.assertNotContains(response, 'Confirmez votre adresse e-mail')
        self.assertNotContains(response, 'Recevoir mon lien de confirmation')
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertFalse(EmailVerification.objects.filter(user=self.customer).exists())

    def test_email_does_not_expose_benefit_assigned_to_another_account(self):
        benefit = CustomerBenefit.objects.create(user=self.other, recipient_email=self.customer.email, label='Bound elsewhere', kind='amount', value=9)
        self.client.force_login(self.customer, backend='connexion.backends.EmailOrUsernameBackend')
        response = self.client.get(reverse('espace:credits'))
        self.assertNotContains(response, benefit.code)

    def test_repeated_create_and_notify_submission_do_not_duplicate_codes_or_email(self):
        key = str(uuid.uuid4())
        data = self.data(creation_key=key)
        first = self.client.post(reverse('avoirs:create'), data)
        second = self.client.post(reverse('avoirs:create'), data)
        self.assertEqual(first.url, second.url)
        self.assertEqual(CustomerBenefit.objects.filter(creation_key=key).count(), 1)
        notice_key = str(uuid.uuid4())
        url = reverse('avoirs:notify', args=[self.benefit.pk])
        self.client.post(url, {'notification_key': notice_key})
        self.client.post(url, {'notification_key': notice_key})
        self.assertEqual(self.benefit.notifications.count(), 1)

    def test_seller_role_is_allowed_but_catalog_only_staff_is_denied(self):
        seller = get_user_model().objects.create_user('seller-role', email='seller-role@example.test', is_staff=True)
        verify_test_user(seller)
        seller.user_permissions.add(Permission.objects.get(codename='manage_returns', content_type__app_label='retours'))
        self.client.force_login(seller, backend='connexion.backends.EmailOrUsernameBackend')
        self.assertEqual(self.client.get(reverse('avoirs:management')).status_code, 200)
        self.assertEqual(self.client.get(reverse('avoirs:create')).status_code, 200)
        catalog_staff = get_user_model().objects.create_user('catalog-only-role', email='catalog-role@example.test', is_staff=True)
        verify_test_user(catalog_staff)
        catalog_staff.user_permissions.add(Permission.objects.get(codename='manage_catalog', content_type__app_label='core'))
        self.client.force_login(catalog_staff, backend='connexion.backends.EmailOrUsernameBackend')
        self.assertEqual(self.client.get(reverse('avoirs:management')).status_code, 403)

    def test_invalid_form_fields_never_create_benefits_or_raise_server_error(self):
        count = CustomerBenefit.objects.count()
        for changes in ({'value': ''}, {'value': 'abc'}, {'recipient_email': 'invalid'}, {'starts_at': 'invalid'}, {'kind': 'invalid'}):
            response = self.client.post(reverse('avoirs:create'), self.data(**changes))
            self.assertEqual(response.status_code, 200)
        self.assertEqual(CustomerBenefit.objects.count(), count)

    def test_deleted_account_benefit_is_not_exposed_to_new_owner_of_same_email(self):
        self.benefit.user = self.other
        self.benefit.bound_user_id = self.other.pk
        self.benefit.save()
        self.other.delete()
        self.client.force_login(self.customer, backend='connexion.backends.EmailOrUsernameBackend')
        self.assertNotContains(self.client.get(reverse('espace:credits')), self.benefit.code)

    def test_seller_can_explicitly_reassign_unused_code_after_account_deletion(self):
        self.benefit.user = self.other
        self.benefit.bound_user_id = self.other.pk
        self.benefit.save()
        self.other.delete()
        self.benefit.refresh_from_db()
        url = reverse('avoirs:edit', args=[self.benefit.pk])
        response = self.client.post(url, self.data(reassign_customer='on', revision=self.benefit.updated_at.isoformat()))
        self.assertEqual(response.status_code, 302)
        self.benefit.refresh_from_db()
        self.assertEqual(self.benefit.user, self.customer)
        self.assertEqual(self.benefit.bound_user_id, self.customer.pk)

    def test_percentage_invalid_decimal_values_show_errors_without_server_failure(self):
        count = CustomerBenefit.objects.count()
        for value in ('', 'abc', 'NaN', 'Infinity'):
            with self.subTest(value=value):
                response = self.client.post(reverse('avoirs:create'), self.data(kind='percent', value=value))
                self.assertEqual(response.status_code, 200)
                self.assertIn('value', response.context['form'].errors)
        self.assertEqual(CustomerBenefit.objects.count(), count)

    def test_seller_can_create_justification_and_client_sees_details_without_admin_controls(self):
        start = timezone.now() - timedelta(days=1)
        end = timezone.now() + timedelta(days=2)
        justification = 'Pour vous remercier de votre fidélité.\nÀ bientôt à la boutique.'
        response = self.client.post(reverse('avoirs:create'), self.data(
            label='Votre fidélité récompensée', justification=justification,
            starts_at=timezone.localtime(start).strftime('%Y-%m-%dT%H:%M'),
            ends_at=timezone.localtime(end).strftime('%Y-%m-%dT%H:%M'),
        ))
        self.assertEqual(response.status_code, 302)
        benefit = CustomerBenefit.objects.get(label='Votre fidélité récompensée')
        self.assertEqual(benefit.justification, justification)
        self.assertContains(self.client.get(response.url), 'Message de la boutique')
        self.client.force_login(self.customer, backend='connexion.backends.EmailOrUsernameBackend')
        page = self.client.get(reverse('espace:credits'))
        self.assertContains(page, 'Message de la boutique')
        self.assertContains(page, '<p>Pour vous remercier de votre fidélité.<br>À bientôt à la boutique.</p>', html=True)
        self.assertContains(page, 'Créé le')
        self.assertContains(page, timezone.localtime(benefit.created_at).strftime('%d/%m/%Y à %H:%M'))
        self.assertContains(page, 'Disponible du')
        self.assertContains(page, timezone.localtime(benefit.starts_at).strftime('%d/%m/%Y à %H:%M'))
        self.assertContains(page, 'Valable jusqu’au')
        self.assertContains(page, timezone.localtime(benefit.ends_at).strftime('%d/%m/%Y à %H:%M'))
        self.assertContains(page, 'Temps restant :')
        self.assertContains(page, 'Conditions d’utilisation')
        self.assertContains(page, '70,00')
        self.assertContains(page, benefit.code)
        self.assertNotContains(page, 'Créer un avoir ou un bon')
        self.assertNotContains(page, 'Modifier les conditions')
        self.assertNotContains(page, 'Activer le code')
        self.assertNotContains(page, 'Réattribuer au compte')

    def test_justification_is_escaped_and_optional(self):
        self.benefit.justification = '<script>alert(1)</script>\nUn geste pour vous.'
        self.benefit.save()
        self.client.force_login(self.customer, backend='connexion.backends.EmailOrUsernameBackend')
        page = self.client.get(reverse('espace:credits'))
        self.assertNotContains(page, '<script>alert(1)</script>')
        self.assertContains(page, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.benefit.justification = ''
        self.benefit.save()
        self.assertNotContains(self.client.get(reverse('espace:credits')), 'Message de la boutique')
