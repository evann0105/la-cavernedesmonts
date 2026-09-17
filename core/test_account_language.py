from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class AccountLanguageTests(TestCase):
    def test_home_has_no_language_bar_or_removed_slogan(self):
        page = self.client.get('/')
        self.assertNotContains(page, 'id="site-language"')
        self.assertNotContains(page, 'class="announcement"')
        self.assertNotContains(page, 'L’esprit montagne, à emporter avec soi.')

    def test_language_available_before_login_and_to_customer_and_admin(self):
        self.assertContains(self.client.get('/accounts/login/'), 'id="site-language"')
        for name, staff in [('customer-language', False), ('admin-language', True)]:
            user = User.objects.create_user(name, is_staff=staff)
            self.client.force_login(user, backend='connexion.backends.EmailOrUsernameBackend')
            page = self.client.get(reverse('two_factor:profile'))
            self.assertContains(page, 'id="site-language"')
            response = self.client.post(reverse('set_language'), {'language':'en', 'next':reverse('two_factor:profile')}, follow=True)
            self.assertContains(response, 'Website language')
            self.assertEqual(self.client.cookies['django_language'].value,'en')
            self.client.logout()
