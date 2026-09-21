from datetime import timedelta
from django.contrib.auth.models import User
from django.test import TestCase, Client, override_settings
from django.utils import timezone
from axes.models import AccessAttempt
from .models import SignupQuota
from .security import check_current_password, PasswordCheckLimited
from .test_helpers import verify_test_user


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class PasswordLimitsTests(TestCase):
    password = 'Only-for-tests-73!'

    def setUp(self):
        self.user = User.objects.create_user('limited-user', email='limited@example.test', password=self.password)
        verify_test_user(self.user)

    def login_attempt(self, identifier, number, password='incorrect', url='/accounts/login/'):
        client = Client()
        response = client.post(url, {'username': identifier, 'password': password}, REMOTE_ADDR=f'192.0.2.{number}')
        return response, client

    def test_distributed_login_uses_same_budget_for_email_username_and_routes(self):
        aliases = ['limited-user', 'limited@example.test', 'LIMITED@example.test', 'limited-user', 'limited@example.test']
        for number, identifier in enumerate(aliases, 1):
            response, _ = self.login_attempt(identifier, number, url='/account/login/' if number % 2 else '/accounts/login/')
        self.assertEqual(response.status_code, 429)
        response, client = self.login_attempt('limited-user', 20, password=self.password)
        self.assertEqual(response.status_code, 429)
        self.assertNotIn('_auth_user_id', client.session)
        self.assertEqual(response['Retry-After'], '900')
        other = User.objects.create_user('unrelated', password=self.password)
        response, _ = self.login_attempt(other.username, 21, password=self.password)
        self.assertEqual(response.status_code, 302)

    def test_account_lockout_expires(self):
        for number in range(1, 6):
            self.login_attempt(self.user.username, number)
        AccessAttempt.objects.update(attempt_time=timezone.now()-timedelta(minutes=16))
        response, _ = self.login_attempt(self.user.email, 10, password=self.password)
        self.assertEqual(response.status_code, 302)

    def authenticated_client(self):
        client = Client()
        client.force_login(self.user, backend='connexion.backends.EmailOrUsernameBackend')
        return client

    def test_profile_password_limit_survives_new_session_and_ip(self):
        for number in range(1, 6):
            response = self.authenticated_client().post('/identite/', {'email':self.user.email, 'current_password':'incorrect'}, REMOTE_ADDR=f'192.0.2.{number}')
            self.assertEqual(response.status_code, 200)
        response = self.authenticated_client().post('/identite/', {'email':'changed@example.test', 'current_password':self.password}, REMOTE_ADDR='192.0.2.99')
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response['Retry-After'], '900')
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'limited@example.test')
        SignupQuota.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
        response = self.authenticated_client().post('/identite/', {'email':self.user.email, 'current_password':self.password})
        self.assertEqual(response.status_code, 302)

    def test_initial_email_form_is_also_limited(self):
        self.user.email = ''
        self.user.save(update_fields=['email'])
        for _ in range(5):
            self.authenticated_client().post('/accounts/verification-email/', {'email':'new@example.test','password':'incorrect'})
        response = self.authenticated_client().post('/accounts/verification-email/', {'email':'new@example.test','password':self.password})
        self.assertEqual(response.status_code, 429)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, '')

    def test_success_does_not_exhaust_budget_or_erase_failed_attempts(self):
        for _ in range(8):
            self.assertTrue(check_current_password(self.user, self.password))
        self.assertEqual(SignupQuota.objects.get().attempts, 0)
        self.assertFalse(check_current_password(self.user, 'incorrect'))
        self.assertTrue(check_current_password(self.user, self.password))
        self.assertEqual(SignupQuota.objects.get().attempts, 1)
        for _ in range(4):
            self.assertFalse(check_current_password(self.user, 'incorrect'))
        with self.assertRaises(PasswordCheckLimited):
            check_current_password(self.user, self.password)
        self.assertEqual(SignupQuota.objects.get().attempts, 5)
