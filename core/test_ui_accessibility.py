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
        self.current_pages = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.add(attrs['id'])
        self.descriptions.update(attrs.get('aria-describedby', '').split())
        if tag == 'a' and attrs.get('aria-current') == 'page':
            self.current_pages.append(attrs.get('href'))


class AccessiblePagesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('ui-manager', is_staff=True, is_superuser=True)
        verify_test_user(self.user)
        self.client.force_login(self.user, backend='connexion.backends.EmailOrUsernameBackend')
        category = Category.objects.create(name='Femmes', slug='femmes', show_in_menu=True)
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
        refs = References(response.content.decode())
        self.assertEqual(refs.current_pages.count(reverse('core:product_list') + '?categorie=femmes'), 2)
        self.assertNotIn(reverse('core:product_list') + '?categorie=hommes', refs.current_pages)

    def test_nested_address_page_keeps_account_navigation_current(self):
        response = self.client.get(reverse('espace:address_add'))
        self.assertContains(response, '/adresses/" aria-current="page"')
