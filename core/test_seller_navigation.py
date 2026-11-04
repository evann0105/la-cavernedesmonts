import re
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.test import RequestFactory, TestCase, override_settings
from django.urls import resolve, reverse

from connexion.test_helpers import verify_test_user
from core.context_processors import back_navigation, catalog_access


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True,
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class SellerNavigationTests(TestCase):
    password = 'Seller-navigation-test-629!'

    def setUp(self):
        User = get_user_model()
        self.customer = User.objects.create_user('nav-customer', password=self.password)
        self.catalog = User.objects.create_user('nav-catalog', is_staff=True, password=self.password)
        self.returns = User.objects.create_user('nav-returns', is_staff=True, password=self.password)
        self.staff = User.objects.create_user('nav-staff', is_staff=True, password=self.password)
        self.catalog.user_permissions.add(Permission.objects.get(
            content_type__app_label='core', codename='manage_catalog'))
        self.returns.user_permissions.add(Permission.objects.get(
            content_type__app_label='retours', codename='manage_returns'))

    def login_form(self, user, next_url=None):
        self.client.logout()
        data = {'username': user.username, 'password': self.password}
        if next_url is not None:
            data['next'] = next_url
        return self.client.post(reverse('connexion:login'), data)

    def test_login_opens_seller_home_only_for_authorized_roles(self):
        for user, destination in (
            (self.catalog, 'tableau:home'), (self.returns, 'tableau:home'),
            (self.customer, 'espace:account'), (self.staff, 'espace:account'),
        ):
            with self.subTest(user=user.username):
                self.assertRedirects(self.login_form(user), reverse(destination), fetch_redirect_response=False)

    def test_explicit_safe_destination_is_preserved_and_external_next_is_rejected(self):
        account = reverse('espace:account')
        self.assertRedirects(self.login_form(self.catalog, account), account, fetch_redirect_response=False)
        cart = reverse('core:cart')
        self.assertRedirects(self.login_form(self.catalog, cart), cart, fetch_redirect_response=False)
        for unsafe in ('https://outside.example/', '//outside.example/'):
            self.assertRedirects(self.login_form(self.catalog, unsafe), reverse('tableau:home'),
                                 fetch_redirect_response=False)

    @override_settings(LOCAL_SKIP_EMAIL_VERIFICATION=False)
    def test_email_verification_is_still_required_before_seller_landing(self):
        self.catalog.email = 'nav-catalog@example.test'
        self.catalog.save(update_fields=['email'])
        with patch('connexion.views.send_verification') as send:
            self.assertRedirects(self.login_form(self.catalog), reverse('connexion:verify_email'),
                                 fetch_redirect_response=False)
        send.assert_called_once()
        verify_test_user(self.catalog)
        self.assertRedirects(self.login_form(self.catalog), reverse('tableau:home'),
                             fetch_redirect_response=False)

    def test_personal_account_stays_personal_with_one_clear_seller_entry(self):
        self.client.force_login(self.catalog, backend='connexion.backends.EmailOrUsernameBackend')
        response = self.client.get(reverse('espace:account'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Vous consultez votre compte personnel.')
        self.assertContains(response, reverse('tableau:home'))
        self.assertContains(response, reverse('espace:orders'))
        self.assertNotContains(response, 'account-admin-section')
        self.assertNotContains(response, 'account-admin-links')
        self.assertNotContains(response, reverse('core:catalog_add'))
        self.assertTrue(response.context['can_use_seller_space'])
        self.assertFalse(response.context['is_management_page'])

    @override_settings(LOCAL_SKIP_EMAIL_VERIFICATION=False, EMAIL_DELIVERY_ENABLED=True,
                       EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                       DEFAULT_FROM_EMAIL='boutique@example.test', SITE_URL='http://testserver')
    def test_first_email_confirmation_opens_seller_home_but_preserves_requested_destination(self):
        from connexion.email_verification import send_verification
        for user, destination in ((self.catalog, 'tableau:home'), (self.returns, 'espace:credits')):
            with self.subTest(user=user.username):
                user.email = user.username + '@example.test'
                user.save(update_fields=['email'])
                self.client.force_login(user, backend='connexion.backends.EmailOrUsernameBackend')
                if destination == 'espace:credits':
                    session = self.client.session
                    session['email_verification_return_to'] = destination
                    session.save()
                self.assertEqual(send_verification(user), 'sent')
                token = re.search(r'/confirmer-email/([^/]+)/', mail.outbox[-1].body).group(1)
                self.assertRedirects(self.client.post(reverse('connexion:confirm_email', args=[token])),
                                     reverse(destination), fetch_redirect_response=False)

    def test_customers_and_staff_without_business_rights_have_no_seller_entry(self):
        for user in (self.customer, self.staff):
            self.client.force_login(user, backend='connexion.backends.EmailOrUsernameBackend')
            page = self.client.get(reverse('espace:account'))
            self.assertNotContains(page, 'Espace vendeuse')
            self.assertFalse(page.context['can_use_seller_space'])

    def test_management_back_links_return_to_seller_home_not_personal_account(self):
        factory = RequestFactory()
        for name in ('core:catalog_list', 'stocks:overview', 'tableau:dashboard',
                     'core:sales_dashboard', 'boutique:settings', 'boutique:hours',
                     'boutique:shipping_overview', 'expeditions:overview',
                     'clients:directory', 'facturation:accounting', 'alertes:emails'):
            with self.subTest(name=name):
                request = factory.get(reverse(name))
                request.resolver_match = resolve(request.path)
                request.user = self.catalog
                values = back_navigation(request)
                self.assertEqual(values['back_fallback'], reverse('tableau:home'))
                self.assertFalse(values['back_use_history'])
        request = factory.get(reverse('tableau:home'))
        request.resolver_match = resolve(request.path)
        self.assertIsNone(back_navigation(request)['back_url'])

    def test_public_shipping_page_never_links_to_private_seller_space(self):
        self.assertEqual(self.client.get(reverse('boutique:shipping_information')).context['back_fallback'], '/')

    def test_anonymous_account_entry_lets_login_choose_the_right_workspace(self):
        page = self.client.get('/')
        self.assertContains(page, '<a class="account-link" href="' + reverse('connexion:login') + '"')
        self.assertNotContains(page, 'Espace vendeuse')

    def test_public_previews_keep_shop_layout_and_editors_keep_seller_layout(self):
        factory = RequestFactory()
        cases = (
            ('editorial:page_preview_frame', {'key': 'about'}, False),
            ('editorial:page_version_frame', {'key': 'about', 'pk': 1}, False),
            ('core:catalog_preview', {'pk': 1}, False),
            ('core:product_preview', {'slug': 'polaire-test'}, False),
            ('accueil:home', {}, False),
            ('editorial:page_preview', {'key': 'about'}, True),
            ('editorial:page_version_preview', {'key': 'about', 'pk': 1}, True),
            ('homepage:slides', {}, True),
            ('tableau:home', {}, True),
        )
        for view, kwargs, management_layout in cases:
            with self.subTest(view=view):
                request = factory.get(reverse(view, kwargs=kwargs))
                request.user = self.catalog
                request.resolver_match = resolve(request.path)
                context = catalog_access(request)
                self.assertTrue(context['can_use_seller_space'])
                self.assertEqual(context['is_management_page'], management_layout)
