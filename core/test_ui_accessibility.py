from html.parser import HTMLParser
from django.test import TestCase
from django.contrib.auth import get_user_model
User = get_user_model()
from django.urls import reverse
from connexion.test_helpers import verify_test_user
from .models import Category, Product


class References(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.ids = set()
        self.descriptions = set()
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.add(attrs['id'])
        self.descriptions.update(attrs.get('aria-describedby', '').split())


class AccessiblePagesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('ui-manager', is_staff=True, is_superuser=True)
        verify_test_user(self.user)
        self.client.force_login(self.user, backend='connexion.backends.EmailOrUsernameBackend')
        category = Category.objects.create(name='Femmes', slug='femmes')
        self.product = Product.objects.create(name='Polaire de contrôle', slug='controle', price='40', category=category)

    def test_form_help_references_resolve_for_customer_and_manager(self):
        for url in [reverse('espace:personal'), reverse('core:catalog_edit', args=[self.product.pk]), reverse('core:catalog_homepage')]:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                refs = References(response.content.decode())
                self.assertTrue(refs.descriptions)
                self.assertFalse(refs.descriptions - refs.ids)

    def test_navigation_marks_selected_category_only(self):
        response = self.client.get(reverse('core:product_list'), {'categorie': 'femmes'})
        self.assertContains(response, '?categorie=femmes" aria-current="page"', count=2)
        self.assertNotContains(response, '?categorie=hommes" aria-current="page"')

    def test_nested_address_page_keeps_account_navigation_current(self):
        response = self.client.get(reverse('espace:address_add'))
        self.assertContains(response, '/adresses/" aria-current="page"')
