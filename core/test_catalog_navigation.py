"""Preserve the catalogue context through editing, copying and private previews."""
from io import BytesIO
from tempfile import TemporaryDirectory
from urllib.parse import quote, urlencode

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils.html import escape
from django_otp.plugins.otp_totp.models import TOTPDevice
from PIL import Image

from connexion.test_helpers import verify_test_user
from .catalog_navigation import catalog_return, safe_catalog_return
from .models import Category, Product


class SafeCatalogReturnTests(SimpleTestCase):
    def test_only_canonical_catalogue_filters_survive(self):
        self.assertEqual(safe_catalog_return(
            '/gestion/produits/?unexpected=ignored&page=003&tri=-price&attention=missing_brand'
            '&statut=draft&categorie=femmes&q=+polaire+verte+#catalog-product-111'),
            '/gestion/produits/?q=polaire+verte&categorie=femmes&statut=draft'
            '&attention=missing_brand&tri=-price&page=3#catalog-product-111')

    def test_external_relative_encoded_and_control_paths_are_rejected(self):
        for value in [None, 123, '', '/panier/', 'gestion/produits/',
                      'https://example.test/gestion/produits/', 'http://127.0.0.1:8000/gestion/produits/',
                      '//example.test/gestion/produits/', '///example.test/gestion/produits/',
                      '/\\example.test/gestion/produits/', '\\gestion\\produits\\',
                      '/%2fexample.test/gestion/produits/', '%2F%2Fexample.test/',
                      '%252F%252Fexample.test/', '/gestion/produits/%2e%2e/',
                      ' /gestion/produits/', '\n/gestion/produits/', '/gestion/produits/\r\n',
                      '/gestion/produits/?q=a%0d%0aLocation%3Ahttps%3A%2F%2Fevil.test',
                      '/gestion/produits/?q=' + ('a' * 2000)]:
            with self.subTest(value=value):
                self.assertEqual(safe_catalog_return(value), '')

    def test_unknown_values_and_invalid_fragments_are_removed(self):
        self.assertEqual(safe_catalog_return('/gestion/produits/?tri=password&statut=all&attention=all'
                                           '&categorie=bad%2Fcategory&page=-1#https://evil.test'),
                         '/gestion/produits/')
        self.assertEqual(safe_catalog_return('/gestion/produits/?page=1000001#catalog-product-0'),
                         '/gestion/produits/')
        self.assertEqual(safe_catalog_return('/gestion/produits/?q=%22%3E%3Cscript%3E'),
                         '/gestion/produits/?q=%22%3E%3Cscript%3E')
        self.assertEqual(safe_catalog_return('/gestion/produits/?q=first&q=last&page=1'),
                         '/gestion/produits/?q=last&page=1')

    def test_post_value_wins_even_when_empty_or_unsafe(self):
        factory = RequestFactory()
        url = '/gestion/produits/1/modifier/?' + urlencode({'return_to': '/gestion/produits/?page=2'})
        self.assertEqual(catalog_return(factory.get(url)), '/gestion/produits/?page=2')
        self.assertEqual(catalog_return(factory.post(url, {})), '/gestion/produits/?page=2')
        self.assertEqual(catalog_return(factory.post(url, {'return_to': '/gestion/produits/?page=3'})),
                         '/gestion/produits/?page=3')
        for value in ['', '//evil.test']:
            self.assertEqual(catalog_return(factory.post(url, {'return_to': value})), '')


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class CatalogContextTests(TestCase):
    def setUp(self):
        media = TemporaryDirectory()
        self.addCleanup(media.cleanup)
        setting = override_settings(MEDIA_ROOT=media.name)
        setting.enable()
        self.addCleanup(setting.disable)
        user = get_user_model().objects.create_user('catalogue-navigation', is_staff=True)
        user.groups.add(Group.objects.get(name='Gestion du catalogue'))
        verify_test_user(user)
        device = TOTPDevice.objects.create(user=user, name='default', confirmed=True)
        self.client.force_login(user, backend='connexion.backends.EmailOrUsernameBackend')
        session = self.client.session
        session['otp_device_id'] = device.persistent_id
        session.save()
        self.category = Category.objects.create(name='Navigation Femmes', slug='navigation-femmes')
        image = BytesIO()
        Image.new('RGB', (12, 12), 'green').save(image, 'PNG')
        self.product = Product.objects.create(name='Polaire navigation', slug='polaire-navigation',
            category=self.category, price='70', description='Une polaire douce.', is_published=True,
            main_image=SimpleUploadedFile('navigation.png', image.getvalue(), content_type='image/png'))
        self.target = '/gestion/produits/?q=polaire&categorie=navigation-femmes&statut=published&tri=name&page=2#catalog-product-' + str(self.product.pk)

    def url(self, name='catalog_edit', *, target=None):
        return reverse('core:' + name, args=[self.product.pk]) + '?' + urlencode({'return_to': target if target is not None else self.target})

    def template_url(self, name='catalog_edit'):
        return reverse('core:' + name, args=[self.product.pk]) + '?return_to=' + quote(self.target, safe='/')

    def data(self, **overrides):
        values = {'name':self.product.name, 'price':'65', 'description':'Une polaire améliorée.',
                  'category':str(self.category.pk), 'is_published':'on', 'return_to':self.target,
                  'photos-TOTAL_FORMS':'0', 'photos-INITIAL_FORMS':'0',
                  'photos-MIN_NUM_FORMS':'0', 'photos-MAX_NUM_FORMS':'50',
                  'stock-TOTAL_FORMS':'1', 'stock-INITIAL_FORMS':'0',
                  'stock-MIN_NUM_FORMS':'0', 'stock-MAX_NUM_FORMS':'50',
                  'colors-TOTAL_FORMS':'1', 'colors-INITIAL_FORMS':'0',
                  'colors-MIN_NUM_FORMS':'0', 'colors-MAX_NUM_FORMS':'30'}
        values.update(overrides)
        return values

    def test_edit_preserves_links_hidden_value_and_explicit_back(self):
        response = self.client.get(self.url(), HTTP_REFERER='http://testserver/mon-compte/')
        self.assertEqual(response.context['catalog_return_to'], self.target)
        self.assertEqual(response.context['back_url'], self.target)
        self.assertFalse(response.context['back_use_history'])
        self.assertContains(response, f'name="return_to" value="{escape(self.target)}"')
        self.assertContains(response, escape(self.template_url('catalog_preview')))
        self.assertContains(response, escape(self.template_url('catalog_duplicate')))

    def test_successful_save_of_published_and_draft_products_returns_to_catalogue(self):
        for published in ['on', '']:
            response = self.client.post(self.url(), self.data(is_published=published))
            self.assertRedirects(response, self.target, fetch_redirect_response=False)
            self.product.refresh_from_db()
            self.assertEqual(str(self.product.price), '65.00')

    def test_new_product_returns_to_context_when_explicitly_provided(self):
        response = self.client.post(reverse('core:catalog_add'), self.data(name='Nouveau brouillon navigation', is_published=''))
        self.assertRedirects(response, self.target, fetch_redirect_response=False)
        self.assertTrue(Product.objects.filter(name='Nouveau brouillon navigation', is_published=False).exists())

    def test_invalid_form_keeps_context_and_does_not_change_product(self):
        response = self.client.post(self.url(), self.data(price='-5'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['catalog_return_to'], self.target)
        self.assertContains(response, f'name="return_to" value="{escape(self.target)}"')
        self.product.refresh_from_db()
        self.assertEqual(str(self.product.price), '70.00')

    def test_unsafe_context_never_redirects_or_appears_in_form(self):
        target = 'https://evil.test/gestion/produits/'
        response = self.client.get(self.url(target=target))
        self.assertEqual(response.context['catalog_return_to'], '')
        self.assertNotContains(response, target)
        saved = self.client.post(self.url(), self.data(return_to=target))
        self.assertRedirects(saved, reverse('core:product_detail', args=[self.product.slug]), fetch_redirect_response=False)

    def test_private_preview_preserves_link_to_edit_and_catalogue(self):
        response = self.client.get(self.url('catalog_preview'), HTTP_REFERER='http://testserver/mon-compte/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['catalog_return_to'], self.target)
        self.assertEqual(response.context['back_url'], self.target)
        self.assertFalse(response.context['back_use_history'])
        self.assertContains(response, escape(self.template_url()))

    def test_duplication_keeps_context_through_confirmation_and_new_editor(self):
        page = self.client.get(self.url('catalog_duplicate'))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context['catalog_return_to'], self.target)
        self.assertContains(page, f'name="return_to" value="{escape(self.target)}"')
        created = self.client.post(self.url('catalog_duplicate'), {
            'snapshot': page.context['form'].initial['snapshot'], 'return_to': self.target})
        copy = Product.objects.get(name='Copie de ' + self.product.name)
        self.assertRedirects(created, reverse('core:catalog_edit', args=[copy.pk]) + '?' + urlencode({'return_to': self.target}), fetch_redirect_response=False)
        editor = self.client.get(created.url)
        self.assertEqual(editor.context['back_url'], self.target)
        self.assertContains(editor, f'name="return_to" value="{escape(self.target)}"')

    def test_invalid_duplication_keeps_context_for_retry(self):
        response = self.client.post(self.url('catalog_duplicate'), {'snapshot': 'invalid', 'return_to': self.target})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.context['back_url'], self.target)
        self.assertContains(response, f'name="return_to" value="{escape(self.target)}"', status_code=400)
