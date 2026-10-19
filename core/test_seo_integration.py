"""Rendered-page regressions for automatic product search preparation."""
from html.parser import HTMLParser
import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from django_otp.plugins.otp_totp.models import TOTPDevice

from connexion.test_helpers import verify_test_user
from core.models import Category, PriceObservation, Product, ProductColor, ProductPromotion
from stocks.models import Variant


class SeoHTML(HTMLParser):
    """Inspect emitted markup rather than reproducing the metadata generator."""
    def __init__(self, html):
        super().__init__()
        self.in_head = False
        self.meta = []
        self.canonicals = []
        self.json_scripts = []
        self._script = None
        self.inputs = []
        self.images = []
        self.selected_containers = []
        self.price_text = []
        self._price = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'head':
            self.in_head = True
        if self.in_head and tag == 'meta':
            self.meta.append(attrs)
        if self.in_head and tag == 'link' and attrs.get('rel') == 'canonical':
            self.canonicals.append(attrs.get('href', ''))
        if tag == 'script' and attrs.get('type') == 'application/ld+json':
            self._script = []
        if tag == 'input':
            self.inputs.append(attrs)
        if tag == 'img':
            self.images.append(attrs)
        if 'data-selected-variant' in attrs:
            self.selected_containers.append(attrs)
        if 'data-product-price' in attrs:
            self._price = []

    def handle_data(self, data):
        if self._script is not None:
            self._script.append(data)
        if self._price is not None:
            self._price.append(data)

    def handle_endtag(self, tag):
        if tag == 'head':
            self.in_head = False
        if tag == 'script' and self._script is not None:
            self.json_scripts.append(''.join(self._script))
            self._script = None
        if tag == 'p' and self._price is not None:
            self.price_text.append(''.join(self._price).strip())
            self._price = None

    def descriptions(self):
        return [item.get('content', '') for item in self.meta if item.get('name') == 'description']

    def graphs(self):
        for source in self.json_scripts:
            value = json.loads(source)
            if isinstance(value, list):
                yield from value
            elif '@graph' in value:
                yield from value['@graph']
            else:
                yield value


@override_settings(DEBUG=False, SITE_URL='https://la-cavernedesmonts.fr',
                   SEO_INDEXING_ENABLED=True, SECURE_SSL_REDIRECT=False,
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class AutomaticProductSeoIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name='Femmes', slug='femmes')
        cls.product = Product.objects.create(
            name='Polaire femme bicolore', category=cls.category, price='80.00',
            brand='Peak Mountain', description='Une polaire zippée pour femme.',
            composition='100 % polyester', main_color='Rouge',
            main_image='products/seo-rouge.webp', availability='available')
        cls.red = Variant.objects.create(product=cls.product, size='M', color='Rouge', quantity=2)
        cls.green = Variant.objects.create(product=cls.product, size='L', color='Vert', quantity=3, price='100.00')
        ProductColor.objects.create(product=cls.product, name='Vert', image='products/colors/seo-vert.webp')
        cls.draft = Product.objects.create(name='Brouillon SEO privé', category=cls.category, price='45.00', is_published=False)

    def product_url(self):
        return reverse('core:product_detail', args=[self.product.slug])

    def page(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        return response, SeoHTML(response.content.decode())

    def manager_login(self):
        manager = get_user_model().objects.create_user('seo-integration-manager', is_staff=True)
        manager.user_permissions.add(Permission.objects.get(content_type__app_label='core', codename='manage_catalog'))
        verify_test_user(manager)
        device = TOTPDevice.objects.create(user=manager, name='default', confirmed=True)
        self.client.force_login(manager, backend='connexion.backends.EmailOrUsernameBackend')
        session = self.client.session
        session['otp_device_id'] = device.persistent_id
        session.save()
        return manager

    def test_product_emits_one_specific_description_and_one_canonical(self):
        response, html = self.page(self.product_url() + '?utm_source=google')
        self.assertEqual(len(html.descriptions()), 1)
        self.assertIn('Peak Mountain', html.descriptions()[0])
        self.assertEqual(html.canonicals, ['https://la-cavernedesmonts.fr' + self.product_url()])
        self.assertNotIn('X-Robots-Tag', response)
        self.assertFalse(any(item.get('name') == 'keywords' for item in html.meta))

    def test_rendered_product_group_identifies_both_real_size_color_variants(self):
        _, html = self.page(self.product_url())
        group = next(item for item in html.graphs() if item.get('@type') == 'ProductGroup')
        self.assertEqual(len(group['hasVariant']), 2)
        self.assertCountEqual([(item['color'], item['size']) for item in group['hasVariant']], [('Rouge', 'M'), ('Vert', 'L')])
        self.assertCountEqual([item['url'] for item in group['hasVariant']], [
            'https://la-cavernedesmonts.fr' + self.product_url() + f'?variante={self.red.pk}',
            'https://la-cavernedesmonts.fr' + self.product_url() + f'?variante={self.green.pk}',
        ])

    def test_new_and_later_edited_product_gets_fresh_metadata_without_manual_seo_entry(self):
        created = Product.objects.create(
            name='Polaire enfant neuve', price='39.00', brand='Marque Vérifiée',
            category=self.category, description='Polaire enfant à fermeture zippée.',
            main_image='products/seo-nouveau.webp')
        url = reverse('core:product_detail', args=[created.slug])
        _, before = self.page(url)
        self.assertIn('Marque Vérifiée', before.descriptions()[0])
        created.brand = 'Nouvelle Marque'
        created.name = 'Polaire enfant verte'
        created.save(update_fields=['brand', 'name'])
        _, after = self.page(url)
        self.assertIn('Nouvelle Marque', after.descriptions()[0])
        self.assertNotIn('Marque Vérifiée', after.descriptions()[0])
        self.assertEqual(after.canonicals, ['https://la-cavernedesmonts.fr' + url])
        self.assertContains(self.client.get('/sitemap.xml'), url)

    def test_untrusted_product_text_cannot_close_structured_data_script(self):
        self.product.name = 'Polaire </script><script>alert("SEO")</script>'
        self.product.description = 'Texte & tissu </script><script>alert("SEO")</script>'
        self.product.save(update_fields=['name', 'description'])
        response, html = self.page(self.product_url())
        self.assertTrue(list(html.graphs()))
        self.assertTrue(html.json_scripts)
        for source in html.json_scripts:
            self.assertNotIn('</script>', source.casefold())
            json.loads(source)
        self.assertNotContains(response, '<script>alert("SEO")</script>')
        self.assertEqual(len(html.descriptions()), 1)

    def test_variant_link_preselects_correct_size_color_price_and_photograph(self):
        response, html = self.page(self.product_url() + f'?variante={self.green.pk}')
        self.assertEqual(response.context['selected_variant'].pk, self.green.pk)
        self.assertTrue(any(item.get('data-selected-variant') == str(self.green.pk) for item in html.selected_containers))
        self.assertTrue(any(item.get('name') == 'variant' and item.get('value') == str(self.green.pk)
                            and 'checked' in item for item in html.inputs))
        self.assertTrue(any('100,00' in text and 'À partir' not in text for text in html.price_text))
        main_image = next(item for item in html.images if item.get('id') == 'main-product-image')
        self.assertEqual(main_image['src'], '/media/products/colors/seo-vert.webp')
        self.assertEqual(html.canonicals, ['https://la-cavernedesmonts.fr' + self.product_url()])
        self.assertNotIn('X-Robots-Tag', response)

    def test_invalid_or_other_product_variant_is_redirected_to_clean_product_url(self):
        foreign = Variant.objects.create(product=self.draft, size='S', color='Bleu', quantity=1)
        for value in ('invalide', str(foreign.pk), '99999999'):
            with self.subTest(value=value):
                response = self.client.get(self.product_url() + '?variante=' + value)
                self.assertRedirects(response, self.product_url(), status_code=301, fetch_redirect_response=False)

    def test_private_token_page_emits_neither_canonical_nor_public_structured_data(self):
        response, html = self.page(self.product_url() + '?token=private-order-link')
        self.assertEqual(html.canonicals, [])
        self.assertEqual(html.json_scripts, [])
        self.assertFalse(any(item.get('property') == 'og:url' for item in html.meta))
        self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')

    def test_pagination_uses_rendered_page_and_category_in_canonical(self):
        for index in range(25):
            Product.objects.create(name=f'Article SEO pagination {index}', category=self.category, price='20.00')
        _, page_two = self.page('/produits/?categorie=femmes&page=2')
        self.assertEqual(page_two.canonicals, ['https://la-cavernedesmonts.fr/produits/?categorie=femmes&page=2'])
        _, overflow = self.page('/produits/?categorie=femmes&page=999')
        self.assertEqual(overflow.canonicals, page_two.canonicals)

    def test_overview_is_protected_private_and_read_only(self):
        url = reverse('core:seo_overview')
        self.assertEqual(self.client.get(url).status_code, 302)
        customer = get_user_model().objects.create_user('seo-integration-customer')
        self.client.force_login(customer, backend='connexion.backends.EmailOrUsernameBackend')
        self.assertEqual(self.client.get(url).status_code, 403)
        self.manager_login()
        before = (Product.objects.count(), Variant.objects.count(), PriceObservation.objects.count(),
                  ProductPromotion.objects.count(), list(Product.objects.order_by('pk').values()))
        response, html = self.page(url)
        self.assertContains(response, self.product.name)
        self.assertContains(response, self.draft.name)
        self.assertEqual(response.context['total'], 2)
        self.assertEqual(response.context['prepared'], 2)
        self.assertEqual(html.canonicals, [])
        self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')
        self.assertIn('no-store', response['Cache-Control'])
        after = (Product.objects.count(), Variant.objects.count(), PriceObservation.objects.count(),
                 ProductPromotion.objects.count(), list(Product.objects.order_by('pk').values()))
        self.assertEqual(before, after)
