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
        client = client or self.client
        page = client.get(url)
        management = page.context['wizard']['management_form']
        return client.post(url, {management.add_prefix('current_step'): 'auth', 'auth-username': username, 'auth-password': password or self.password}, **extra)

    def submit_step(self, response, step, token, client=None, url='/accounts/login/'):
        management = response.context['wizard']['management_form']
        return (client or self.client).post(url, {management.add_prefix('current_step'): step, f'{step}-otp_token': token})

    def device(self, user=None):
        return TOTPDevice.objects.create(user=user or self.staff, name='default', confirmed=True)

    def token(self, device):
        return str(totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits)).zfill(device.digits)

    def test_admin_enrollment_and_recovery_code_generation(self):
        import base64
        self.authenticate('admin-test')
        page = self.client.get(reverse('two_factor:setup'))
        management = page.context['wizard']['management_form']
        page = self.client.post(reverse('two_factor:setup'), {management.add_prefix('current_step'): 'welcome'})
        self.assertEqual(page.context['wizard']['steps'].current, 'generator')
        self.assertEqual(self.client.get(reverse('two_factor:qr')).status_code, 200)
        key = base64.b32decode(self.client.session['django_two_factor-qr_secret_key'])
        code = str(totp(key)).zfill(6)
        management = page.context['wizard']['management_form']
        response = self.client.post(reverse('two_factor:setup'), {management.add_prefix('current_step'): 'generator', 'generator-token': code})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(TOTPDevice.objects.filter(user=self.staff, confirmed=True).exists())
        self.assertEqual(self.client.get('/admin/').status_code, 200)
        self.assertRedirects(self.client.post(reverse('two_factor:backup_tokens')), reverse('two_factor:backup_tokens'))
        self.assertGreater(StaticToken.objects.filter(device__user=self.staff).count(), 0)

    def test_customer_email_login_still_works(self):
        self.assertRedirects(self.authenticate('client@example.test'), '/')

    def test_no_password_only_admin_access(self):
        self.client.force_login(self.staff, backend='connexion.backends.EmailOrUsernameBackend')
        for path in ('/admin/', '/admin/auth/user/', '/admin/login/'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 302)
        self.assertNotIn('otp_device_id', self.client.session)

    def test_admin_without_device_must_enroll(self):
        self.assertRedirects(self.authenticate('admin-test'), reverse('two_factor:setup'))
        self.assertEqual(self.client.get('/admin/').status_code, 302)

    def test_correct_password_waits_for_code_and_correct_code_opens_admin(self):
        device = self.device()
        response = self.authenticate('admin-test')
        self.assertEqual(response.context['wizard']['steps'].current, 'token')
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(self.client.get('/admin/').status_code, 302)
        response = self.submit_step(response, 'token', self.token(device))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get('/admin/').status_code, 200)
        self.assertEqual(self.client.session['otp_device_id'], device.persistent_id)

    def test_invalid_otp_never_authenticates(self):
        device = self.device()
        response = self.authenticate('admin-test')
        wrong = '000000' if self.token(device) != '000000' else '111111'
        for _ in range(3):
            response = self.submit_step(response, 'token', wrong)
            self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(self.client.get('/admin/').status_code, 302)
        device.refresh_from_db()
        self.assertGreater(device.throttling_failure_count, 0)

    def test_valid_otp_cannot_be_replayed(self):
        device = self.device()
        code = self.token(device)
        response = self.authenticate('admin-test')
        self.submit_step(response, 'token', code)
        self.client.post('/accounts/logout/')
        response = self.authenticate('admin-test')
        response = self.submit_step(response, 'token', code)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_backup_token_single_use(self):
        self.device()
        backup = StaticDevice.objects.create(user=self.staff, name='backup')
        token = StaticToken.objects.create(device=backup, token='test-recovery')
        response = self.authenticate('admin-test')
        response = self.client.post('/accounts/login/', {'wizard_goto_step': 'backup'})
        response = self.submit_step(response, 'backup', token.token)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get('/admin/').status_code, 200)
        self.assertFalse(StaticToken.objects.filter(pk=token.pk).exists())

    def test_package_login_also_requires_otp(self):
        self.device()
        response = self.authenticate('admin-test', url=reverse('two_factor:login'))
        self.assertEqual(response.context['wizard']['steps'].current, 'token')
        self.assertNotIn('_auth_user_id', self.client.session)

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
