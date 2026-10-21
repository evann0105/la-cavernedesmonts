import re
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
User = get_user_model()
from django.core import mail
from django.test import TestCase,Client,override_settings
from django.urls import reverse
from django.utils import timezone
from .email_verification import send_verification, email_verified, token_matches, confirm_token
from .models import EmailVerification

@override_settings(LOCAL_SKIP_EMAIL_VERIFICATION=False,EMAIL_DELIVERY_ENABLED=True,EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',DEFAULT_FROM_EMAIL='boutique@example.test',SITE_URL='http://localhost:8001',DEBUG=True)
class EmailVerificationTests(TestCase):
    def setUp(self):
        self.user=User.objects.create_user('customer',email='customer@example.test',password='Test-pass-123')
        self.client.force_login(self.user,backend='connexion.backends.EmailOrUsernameBackend')
    def sent_token(self):
        self.assertEqual(send_verification(self.user),'sent')
        return re.search(r'/confirmer-email/([^/]+)/',mail.outbox[-1].body).group(1)
    def test_real_mail_link_once_and_persistent_across_sessions(self):
        token=self.sent_token();url=reverse('connexion:confirm_email',args=[token])
        self.assertEqual(mail.outbox[0].to,[self.user.email])
        self.assertNotIn(token,EmailVerification.objects.get(user=self.user).token_digest)
        self.assertEqual(self.client.get(url).status_code,200)
        self.assertFalse(email_verified(self.user))
        self.assertRedirects(self.client.post(url),reverse('espace:account'))
        self.assertTrue(email_verified(self.user))
        self.assertEqual(self.client.post(url).status_code,400)
        self.client.logout()
        self.assertRedirects(self.client.post('/accounts/login/',{'username':'customer','password':'Test-pass-123'}),reverse('espace:account'))
        self.assertEqual(len(mail.outbox),1)
    def test_tampered_expired_wrong_account_and_changed_email_rejected(self):
        token=self.sent_token()
        self.assertFalse(token_matches(self.user,token+'x'))
        other=User.objects.create_user('other',email='other@example.test')
        self.assertFalse(confirm_token(other,token))
        with patch('django.core.signing.time.time',return_value=timezone.now().timestamp()+3601):
            self.assertFalse(token_matches(self.user,token))
        self.user.email='changed@example.test';self.user.save()
        self.assertFalse(confirm_token(self.user,token))
    def test_password_change_revokes_pending_link(self):
        token=self.sent_token();self.user.set_password('different');self.user.save()
        self.assertFalse(confirm_token(self.user,token))
    def test_resend_invalidates_old_link_and_is_throttled(self):
        old=self.sent_token();self.assertEqual(send_verification(self.user),'limited')
        EmailVerification.objects.update(sent_at=timezone.now()-timedelta(minutes=2))
        new=self.sent_token()
        self.assertFalse(token_matches(self.user,old));self.assertTrue(token_matches(self.user,new))
        for _ in range(6):
            EmailVerification.objects.update(sent_at=timezone.now()-timedelta(minutes=2))
            send_verification(self.user)
        self.assertLessEqual(len(mail.outbox),5)
    def test_confirmation_requires_login_and_csrf(self):
        token=self.sent_token();url=reverse('connexion:confirm_email',args=[token])
        self.assertEqual(Client().post(url).status_code,302)
        strict=Client(enforce_csrf_checks=True);strict.force_login(self.user,backend='connexion.backends.EmailOrUsernameBackend')
        self.assertEqual(strict.post(url).status_code,403)
        self.assertEqual(strict.post(reverse('connexion:verify_email')).status_code,403)
    @override_settings(EMAIL_DELIVERY_ENABLED=False)
    def test_unconfigured_provider_does_not_claim_delivery_or_verify(self):
        self.assertEqual(send_verification(self.user),'unavailable')
        self.assertFalse(email_verified(self.user))
        self.assertContains(self.client.get(reverse('connexion:verify_email')),'indisponible')
    @patch('connexion.email_verification.send_mail',side_effect=RuntimeError('SMTP failed'))
    def test_delivery_failure_leaves_account_unverified(self,send):
        self.assertEqual(send_verification(self.user),'unavailable')
        self.assertFalse(email_verified(self.user))
        self.assertEqual(EmailVerification.objects.get(user=self.user).token_digest,'')

    def test_link_can_be_opened_in_another_browser_after_login(self):
        token=self.sent_token();url=reverse('connexion:confirm_email',args=[token])
        other=Client()
        response=other.get(url)
        self.assertEqual(response.status_code,302)
        response=other.post(response.url,{'username':self.user.username,'password':'Test-pass-123'})
        self.assertRedirects(response,url)
        self.assertEqual(len(mail.outbox),1)
        self.assertRedirects(other.post(url),reverse('espace:account'))


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True)
class LocalEmailBypassTests(TestCase):
    def test_signup_opens_account_without_sending_or_marking_verified(self):
        with patch('connexion.views.send_verification') as send:
            response=self.client.post(reverse('connexion:signup'), {
                'username':'local-test', 'email':'local@example.test',
                'password1':'Strong-test-pass-1947', 'password2':'Strong-test-pass-1947',
            })
        self.assertRedirects(response, reverse('espace:account'))
        send.assert_not_called()
        user=User.objects.get(username='local-test')
        self.assertFalse(email_verified(user))
        self.assertFalse(EmailVerification.objects.filter(user=user).exists())
        self.assertEqual(self.client.get(reverse('espace:personal')).status_code,200)
        self.assertEqual(self.client.get(reverse('connexion:verify_email')).status_code,200)
        self.assertEqual(self.client.get('/gestion/produits/').status_code,403)
        with override_settings(DEBUG=False):
            self.assertRedirects(self.client.get(reverse('espace:account')),reverse('connexion:verify_email'))
        with override_settings(LOCAL_SKIP_EMAIL_VERIFICATION=False):
            self.assertRedirects(self.client.get(reverse('espace:account')),reverse('connexion:verify_email'))

    def test_existing_account_login_and_admin_permissions(self):
        from .email_verification import email_access_allowed
        from django.contrib.auth.models import AnonymousUser
        user=User.objects.create_user('local-existing',email='existing@example.test',password='Test-pass-123')
        with patch('connexion.views.send_verification') as send:
            self.assertRedirects(self.client.post('/accounts/login/',{'username':user.username,'password':'Test-pass-123'}),reverse('espace:account'))
        send.assert_not_called()
        self.assertFalse(email_access_allowed(AnonymousUser()))
        user.is_active=False
        self.assertFalse(email_access_allowed(user))
        user.is_active=True;user.is_staff=True;user.is_superuser=True;user.save()
        self.assertEqual(self.client.get('/gestion/produits/').status_code,200)
        self.assertFalse(email_verified(user))


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True, EMAIL_DELIVERY_ENABLED=True,
                   EMAIL_TEST_MODE=False, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   DEFAULT_FROM_EMAIL='boutique@example.test', SITE_URL='http://localhost:8001')
class VoluntaryLocalVerificationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('voluntary-local', email='voluntary@example.test', password='Test-pass-123')
        self.client.force_login(self.user, backend='connexion.backends.EmailOrUsernameBackend')

    def token_from_message(self):
        return re.search(r'/confirmer-email/([^/]+)/', mail.outbox[-1].body).group(1)

    def test_local_get_is_passive_and_explicit_post_really_verifies_before_returning_to_credits(self):
        url = reverse('connexion:verify_email')
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(EmailVerification.objects.filter(user=self.user).exists())
        self.assertRedirects(self.client.post(url, {'next': reverse('espace:credits')}), url)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[-1].to, [self.user.email])
        self.assertFalse(email_verified(self.user))
        confirmation = reverse('connexion:confirm_email', args=[self.token_from_message()])
        self.assertEqual(self.client.get(confirmation).status_code, 200)
        self.assertFalse(email_verified(self.user))
        self.assertRedirects(self.client.post(confirmation), reverse('espace:credits'))
        self.assertTrue(email_verified(self.user))
        self.assertNotIn('email_verification_return_to', self.client.session)

    def test_credits_request_button_uses_post_and_csrf(self):
        page = self.client.get(reverse('espace:credits'))
        self.assertContains(page, 'Recevoir mon lien de confirmation')
        self.assertContains(page, 'method="post" action="' + reverse('connexion:verify_email') + '"')
        self.assertContains(page, '<input type="hidden" name="next" value="' + reverse('espace:credits') + '">', html=True)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user, backend='connexion.backends.EmailOrUsernameBackend')
        self.assertEqual(strict.post(reverse('connexion:verify_email'), {'next': reverse('espace:credits')}).status_code, 403)
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(email_verified(self.user))

    def test_confirmation_from_cart_returns_to_the_same_cart(self):
        verification = reverse('connexion:verify_email')
        response = self.client.get(verification, {'next': reverse('core:cart')})
        self.assertContains(response, 'Revenir à mon panier')
        self.assertEqual(self.client.session['email_verification_return_to'], 'core:cart')
        self.client.post(verification)
        confirmation = reverse('connexion:confirm_email', args=[self.token_from_message()])
        self.assertRedirects(self.client.post(confirmation), reverse('core:cart'))
        self.assertTrue(email_verified(self.user))

    def test_missing_email_is_requested_with_current_password_then_confirmation_still_required(self):
        self.user.email = ''
        self.user.save(update_fields=['email'])
        page = self.client.get(reverse('espace:credits'))
        self.assertContains(page, 'Ajouter mon adresse e-mail')
        self.assertNotContains(page, 'Recevoir mon lien de confirmation')
        verification = reverse('connexion:verify_email')
        page = self.client.get(verification, {'next': reverse('espace:credits')})
        self.assertContains(page, 'Mot de passe actuel')
        self.assertEqual(len(mail.outbox), 0)
        invalid = self.client.post(verification, {'email': 'added@example.test', 'password': 'wrong'})
        self.assertEqual(invalid.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, '')
        self.assertEqual(len(mail.outbox), 0)
        self.assertRedirects(self.client.post(verification, {'email': 'ADDED@example.test', 'password': 'Test-pass-123'}), verification)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'added@example.test')
        self.assertEqual(len(mail.outbox), 1)
        self.assertFalse(email_verified(self.user))
        confirmation = reverse('connexion:confirm_email', args=[self.token_from_message()])
        self.assertRedirects(self.client.post(confirmation), reverse('espace:credits'))
        self.assertTrue(email_verified(self.user))

    def test_external_or_corrupted_return_destination_never_redirects_outside_account(self):
        verification = reverse('connexion:verify_email')
        self.assertEqual(self.client.get(verification, {'next': 'https://outside.example.test/'}).status_code, 200)
        self.assertNotIn('email_verification_return_to', self.client.session)
        self.assertRedirects(self.client.post(verification, {'next': '//outside.example.test/'}), verification)
        session = self.client.session
        session['email_verification_return_to'] = 'https://outside.example.test/'
        session.save()
        confirmation = reverse('connexion:confirm_email', args=[self.token_from_message()])
        self.assertRedirects(self.client.post(confirmation), reverse('espace:account'))

    @override_settings(EMAIL_TEST_MODE=True)
    def test_capture_mode_does_not_claim_real_email_delivery(self):
        response = self.client.post(reverse('connexion:verify_email'), follow=True)
        self.assertContains(response, 'Le lien de confirmation a été généré dans la boîte e-mail de test du site.')
        self.assertContains(response, 'Aucun e-mail n’a été envoyé à votre boîte personnelle.')
        self.assertNotContains(response, 'Le lien de confirmation a été envoyé. Consultez votre boîte mail')
        self.assertEqual(len(mail.outbox), 1)
        self.assertFalse(email_verified(self.user))

    @override_settings(EMAIL_TEST_MODE=True)
    def test_capture_inbox_link_is_only_shown_to_catalog_managers(self):
        self.assertNotContains(self.client.get(reverse('connexion:verify_email')), 'Ouvrir la boîte e-mail de test')
        manager = User.objects.create_superuser('voluntary-manager', email='manager@example.test', password='Test-pass-123')
        self.client.force_login(manager, backend='connexion.backends.EmailOrUsernameBackend')
        self.assertContains(self.client.get(reverse('connexion:verify_email')), 'Ouvrir la boîte e-mail de test')
