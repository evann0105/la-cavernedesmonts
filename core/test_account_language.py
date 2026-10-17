from django.contrib.auth import get_user_model
User = get_user_model()
from django.test import TestCase
from django.urls import reverse
from connexion.test_helpers import verify_test_user


class AccountLanguageTests(TestCase):
    def test_home_has_no_language_bar_or_removed_slogan(self):
        page = self.client.get('/')
        self.assertNotContains(page, 'id="site-language"')
        self.assertNotContains(page, 'class="announcement"')
        self.assertNotContains(page, 'L’esprit montagne, à emporter avec soi.')

    def test_account_stays_french_for_customer_and_admin(self):
        self.assertNotContains(self.client.get('/accounts/login/'), 'id="site-language"')
        for name, staff in [('customer-language', False), ('admin-language', True)]:
            user = User.objects.create_user(name, is_staff=staff)
            verify_test_user(user)
            self.client.force_login(user, backend='connexion.backends.EmailOrUsernameBackend')
            self.client.cookies['django_language'] = 'en'
            session = self.client.session
            session['django_language'] = 'en'
            session.save()
            page = self.client.get('/mon-compte/', HTTP_ACCEPT_LANGUAGE='en-US,en;q=0.9')
            self.assertContains(page, 'Mon compte')
            self.assertContains(page, 'Heureux de vous retrouver'.upper())
            self.assertNotContains(page, 'My account')
            self.assertNotContains(page, 'id="site-language"')
            self.assertEqual(page.headers['Content-Language'], 'fr')
            self.client.logout()
