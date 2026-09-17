import re
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase,Client,override_settings
from django.urls import reverse
from django.utils import timezone
from .email_verification import send_verification, email_verified, token_matches, confirm_token
from .models import EmailVerification

@override_settings(EMAIL_DELIVERY_ENABLED=True,EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',DEFAULT_FROM_EMAIL='boutique@example.test',SITE_URL='http://localhost:8001',DEBUG=True)
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
