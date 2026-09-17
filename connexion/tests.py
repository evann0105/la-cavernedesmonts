from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth.models import User
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, Client, override_settings, SimpleTestCase
from django.urls import reverse
from django.utils import timezone
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from axes.models import AccessAttempt
from .models import SignupQuota
from config.secret_key import validate_secret_key


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class SecurityTests(TestCase):
    password = 'Test-only-8Dq4!zR7'

    def setUp(self):
        self.user = User.objects.create_user('client-test', email='client@example.test', password=self.password)
        self.staff = User.objects.create_superuser('admin-test', email='admin@example.test', password=self.password)

    def authenticate(self, username, password=None, url='/accounts/login/', client=None, **extra):
        return (client or self.client).post(url, {'username':username,'password':password or self.password}, **extra)

    def test_customer_email_login_still_works(self):
        from .test_helpers import verify_test_user
        verify_test_user(self.user)
        self.assertRedirects(self.authenticate('client@example.test'), reverse('espace:account'))

    def test_unverified_admin_cannot_access_admin_or_catalog(self):
        self.client.force_login(self.staff, backend='connexion.backends.EmailOrUsernameBackend')
        for path in ('/admin/', '/admin/auth/user/', '/admin/login/', '/gestion/produits/'):
            self.assertEqual(self.client.get(path).status_code, 302)

    def test_verified_admin_uses_password_only_even_with_old_otp_device(self):
        from .test_helpers import verify_test_user
        verify_test_user(self.staff)
        TOTPDevice.objects.create(user=self.staff,name='default',confirmed=True)
        self.assertRedirects(self.authenticate('admin-test'),reverse('espace:account'))
        self.assertEqual(self.client.get('/admin/').status_code,200)
        self.assertEqual(self.client.get('/gestion/produits/').status_code,200)

    def test_login_alias_and_legacy_setup_use_email_verification(self):
        self.assertRedirects(self.authenticate('admin-test',url=reverse('two_factor:login')),reverse('connexion:verify_email'))
        self.assertNotContains(self.client.get(reverse('two_factor:setup')),'Activer l’authentification')

    def test_password_lockout_persists_with_new_session_and_spoofed_header(self):
        for n in range(5):
            response = self.authenticate('client-test', password='wrong-password')
        self.assertEqual(response.status_code, 429)
        other = Client()
        response = self.authenticate('client-test', client=other, HTTP_X_FORWARDED_FOR='203.0.113.77')
        self.assertEqual(response.status_code, 429)
        self.assertNotIn('_auth_user_id', other.session)
        self.assertEqual(response['Retry-After'], '900')

    def test_password_lockout_expires(self):
        for _ in range(5):
            self.authenticate('client-test', password='wrong')
        AccessAttempt.objects.update(attempt_time=timezone.now()-timedelta(minutes=16))
        self.assertEqual(self.authenticate('client-test').status_code, 302)

    def test_signup_limited_across_sessions_and_spoofed_headers(self):
        for _ in range(5):
            self.assertEqual(self.client.post('/accounts/signup/', {}).status_code, 200)
        response = Client().post('/accounts/signup/', {}, HTTP_X_FORWARDED_FOR='203.0.113.78')
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response['Retry-After'], '1800')
        self.assertEqual(SignupQuota.objects.get().attempts, 5)
        self.assertNotIn('127.0.0.1', SignupQuota.objects.get().key)

    def test_signup_resumes_next_window(self):
        with patch('connexion.security.time.time', return_value=1800000000):
            for _ in range(6):
                response = self.client.post('/accounts/signup/', {})
            self.assertEqual(response.status_code, 429)
        with patch('connexion.security.time.time', return_value=1800001800):
            self.assertEqual(self.client.post('/accounts/signup/', {}).status_code, 200)

    def test_signup_cannot_create_staff(self):
        response = self.client.post('/accounts/signup/', {'username':'new-customer','email':'new@example.test','password1':self.password,'password2':self.password,'is_staff':'true','is_superuser':'true'})
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username='new-customer')
        self.assertFalse(user.is_staff or user.is_superuser)

    def test_login_requires_csrf(self):
        self.assertEqual(Client(enforce_csrf_checks=True).post('/accounts/login/', {}).status_code, 403)


class SecretTests(SimpleTestCase):
    def test_weak_or_placeholder_secrets_rejected(self):
        for value in ('', 'weak-secret', 'a'*70, 'django-insecure-'+('Ab9-xY2!'*10), 'remplacer-'+('Ab9-xY2!'*10)):
            with self.assertRaises(ImproperlyConfigured):
                validate_secret_key(value)

    def test_random_secret_accepted(self):
        import secrets
        value = secrets.token_urlsafe(64)
        self.assertEqual(validate_secret_key(value), value)
