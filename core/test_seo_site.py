import json
from datetime import time
from unittest.mock import patch
from xml.etree import ElementTree

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import resolve, reverse

from core.models import Category, Product
from core.seo import indexing_enabled, json_ld, page_seo, public_absolute_url
from core.seo_middleware import SearchIndexingMiddleware


PUBLIC = {'DEBUG': False, 'SITE_URL': 'https://boutique.example.com',
          'SEO_INDEXING_ENABLED': True, 'SECURE_SSL_REDIRECT': False}


def request_for(path):
    request = RequestFactory().get(path, HTTP_HOST='untrusted.example.net')
    request.resolver_match = resolve(request.path)
    return request


@override_settings(**PUBLIC)
class SeoPolicyTests(SimpleTestCase):
    def test_public_domain_can_be_indexed_only_when_explicitly_enabled_and_not_debug(self):
        self.assertTrue(indexing_enabled())
        for values in ({'DEBUG': True}, {'SEO_INDEXING_ENABLED': False},
                       {'SITE_URL': 'http://boutique.example.com'},
                       {'SITE_URL': 'https://localhost'}, {'SITE_URL': 'https://127.0.0.1'},
                       {'SITE_URL': 'https://192.168.1.25'}, {'SITE_URL': 'https://[::1]'},
                       {'SITE_URL': 'https://preview.local'}, {'SITE_URL': 'https://test'},
                       {'SITE_URL': 'https://user:password@example.com'},
                       {'SITE_URL': 'https://example.com/shop?token=private'}):
            with self.subTest(values=values), override_settings(**values):
                self.assertFalse(indexing_enabled())

    def test_canonical_origin_ignores_untrusted_request_host(self):
        seo = page_seo(request_for('/confidentialite/?utm_source=email'))
        self.assertEqual(seo['canonical'], 'https://boutique.example.com/confidentialite/')
        self.assertTrue(seo['indexable'])
        self.assertNotIn('untrusted', seo['canonical'])

    def test_unsafe_absolute_urls_are_rejected(self):
        for value in ('javascript:alert(1)', '//evil.example/x', '/\\evil.example', 'https://user:pw@evil.example/x',
                      'https://evil.example/x', '/x\r\nLocation:evil', 'relative.jpg'):
            with self.subTest(value=value):
                self.assertEqual(public_absolute_url(value), '')
        self.assertEqual(public_absolute_url('https://cdn.example.com/image.webp', allow_external=True),
                         'https://cdn.example.com/image.webp')
        self.assertEqual(public_absolute_url('http://cdn.example.com/image.webp', allow_external=True), '')

    def test_token_and_private_pages_never_leak_query_into_canonical_or_json(self):
        for path in ('/mon-compte/?token=very-secret', '/gestion/produits/',
                     '/panier/', '/commandes/346c53df-62bc-58ed-8841-10b3289adbf2/facture/',
                     '/produits/example/prix/?token=very-secret'):
            with self.subTest(path=path):
                seo = page_seo(request_for(path), structured_data={'secret': 'must-not-appear'})
                self.assertEqual(seo['robots'], 'noindex, nofollow')
                self.assertEqual(seo['canonical'], '')
                self.assertEqual(seo['json_ld'], '')

    def test_script_json_is_valid_and_cannot_break_out(self):
        unsafe = '</script><script>alert("test")</script> & \u2028 \u2029'
        encoded = json_ld({'name': unsafe})
        self.assertNotIn('<', encoded)
        self.assertNotIn('&', encoded)
        self.assertEqual(json.loads(encoded)['name'], unsafe)

    def test_private_files_apis_and_error_responses_receive_noindex_header(self):
        for path, status, content_type in (
            ('/mon-compte/', 200, 'text/html'),
            ('/gestion/soldes/', 200, 'text/html'),
            ('/retours/commande/9/?token=secret', 200, 'application/pdf'),
            ('/produits/item/prix/', 200, 'application/json'),
            ('/media/return-proof.pdf', 200, 'application/pdf'),
            ('/page-inexistante/', 404, 'text/html'),
        ):
            with self.subTest(path=path):
                request = RequestFactory().get(path)
                response = SearchIndexingMiddleware(lambda _: HttpResponse(status=status, content_type=content_type))(request)
                self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')

    def test_product_images_are_not_accidentally_noindexed(self):
        for path in ('/media/products/photo.webp', '/media/products/colors/vert.webp', '/static/core/img/products/1.webp'):
            request = RequestFactory().get(path)
            response = SearchIndexingMiddleware(lambda _: HttpResponse(content_type='image/webp'))(request)
            self.assertNotIn('X-Robots-Tag', response)

    def test_development_responses_are_noindexed_even_if_product_image(self):
        with override_settings(DEBUG=True):
            request = request_for('/')
            response = SearchIndexingMiddleware(lambda _: HttpResponse(content_type='image/webp'))(request)
            self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')


@override_settings(**PUBLIC)
class SeoCatalogueTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.women = Category.objects.create(name='Femmes', slug='femmes')
        cls.men = Category.objects.create(name='Hommes', slug='hommes')
        cls.baby = Category.objects.create(name='Accessoires bébé', slug='accessoires-bebe')
        cls.empty = Category.objects.create(name='Non publiée', slug='sans-article')
        cls.visible = Product.objects.create(name='Polaire verte Peak Mountain', category=cls.women, price='80.00')
        cls.visible.collections.add(cls.baby)
        cls.hidden = Product.objects.create(name='Brouillon confidentiel', category=cls.empty, price='50.00', is_published=False)

    def test_category_and_curated_type_have_distinct_stable_canonical(self):
        category = page_seo(request_for('/produits/?categorie=femmes'))
        self.assertEqual(category['canonical'], 'https://boutique.example.com/produits/?categorie=femmes')
        self.assertIn('Femmes', category['title'])
        self.assertTrue(category['indexable'])
        intent = page_seo(request_for('/produits/?type=polaires&categorie=femmes&utm_source=google'))
        self.assertEqual(intent['canonical'], 'https://boutique.example.com/produits/?categorie=femmes&type=polaires')
        self.assertIn('Polaires femme', intent['title'])
        self.assertTrue(intent['indexable'])

    def test_each_real_catalogue_page_has_its_own_canonical(self):
        request = request_for('/produits/?categorie=femmes&page=2')
        self.assertEqual(page_seo(request)['canonical'], 'https://boutique.example.com/produits/?categorie=femmes&page=2')
        request = request_for('/produits/?categorie=femmes&page=999')
        request._seo_page_number = 3
        self.assertEqual(page_seo(request)['canonical'], 'https://boutique.example.com/produits/?categorie=femmes&page=3')
        self.assertNotIn('page=', page_seo(request_for('/produits/?page=1'))['canonical'])

    def test_internal_search_and_facets_do_not_create_indexable_duplicates(self):
        for query in ('categorie=femmes&q=polaire', 'categorie=femmes&tri=prix',
                      'categorie=femmes&taille=M', 'categorie=femmes&disponibilite=available',
                      'categorie=femmes&type=accessoires', 'categorie=unknown',
                      'categorie=femmes&categorie=hommes', 'page=bad', 'page=-1'):
            with self.subTest(query=query):
                request = request_for('/produits/?' + query)
                seo = page_seo(request)
                self.assertFalse(seo['indexable'])
                self.assertEqual(seo['robots'], 'noindex, follow')
                self.assertNotIn('q=', seo['canonical'])
                response = SearchIndexingMiddleware(lambda _: HttpResponse())(request)
                self.assertEqual(response['X-Robots-Tag'], 'noindex, follow')

    def test_product_metadata_is_automatic_and_current_without_invented_values(self):
        request = request_for(reverse('core:product_detail', args=[self.visible.slug]))
        seo = page_seo(request, self.visible, {'title': 'Polaire Peak Mountain verte',
                                             'description': 'Une polaire femme verte.',
                                             'image': '/media/products/photo.webp'},
                       {'@type': 'Product', 'name': self.visible.name})
        self.assertEqual(seo['title'], 'Polaire Peak Mountain verte')
        self.assertEqual(seo['description'], 'Une polaire femme verte.')
        self.assertEqual(seo['image'], 'https://boutique.example.com/media/products/photo.webp')
        self.assertEqual(seo['og_type'], 'product')
        self.assertEqual(json.loads(seo['json_ld'])['name'], self.visible.name)
        self.assertTrue(seo['indexable'])
        self.visible.is_published = False
        self.assertFalse(page_seo(request, self.visible)['indexable'])

    def test_verified_variant_selector_is_indexable_with_parent_canonical(self):
        base = reverse('core:product_detail', args=[self.visible.slug])
        request = request_for(base + '?variante=123&utm_source=google')
        request._seo_variant_valid = True
        seo = page_seo(request, self.visible)
        self.assertTrue(seo['indexable'])
        self.assertEqual(seo['canonical'], 'https://boutique.example.com' + base)
        response = SearchIndexingMiddleware(lambda _: HttpResponse())(request)
        self.assertNotIn('X-Robots-Tag', response)

    def test_unknown_or_unverified_variant_selector_does_not_enable_other_queries(self):
        base = reverse('core:product_detail', args=[self.visible.slug])
        for query, valid in (('variante=123', False), ('variante=123&price=1', True),
                             ('variante=123&variante=456', True), ('variante=bad', True)):
            with self.subTest(query=query):
                request = request_for(base + '?' + query)
                request._seo_variant_valid = valid
                self.assertFalse(page_seo(request, self.visible)['indexable'])
                response = SearchIndexingMiddleware(lambda _: HttpResponse())(request)
                self.assertEqual(response['X-Robots-Tag'], 'noindex, follow')

    def test_sitemap_contains_all_published_products_and_no_private_or_drafts(self):
        response = self.client.get('/sitemap.xml')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].startswith('application/xml'))
        root = ElementTree.fromstring(response.content)
        urls = [element.text for element in root.findall('{*}url/{*}loc')]
        self.assertIn('https://boutique.example.com' + reverse('core:product_detail', args=[self.visible.slug]), urls)
        self.assertNotIn('https://boutique.example.com' + reverse('core:product_detail', args=[self.hidden.slug]), urls)
        self.assertIn('https://boutique.example.com/produits/?categorie=femmes&type=polaires', urls)
        self.assertIn('https://boutique.example.com/produits/?categorie=accessoires-bebe', urls)
        self.assertFalse(any('sans-article' in url or '/gestion/' in url or '/mon-compte/' in url for url in urls))
        self.assertEqual(len(urls), len(set(urls)))
        self.assertContains(response, '&amp;type=polaires')
        self.assertNotContains(response, '<lastmod>')

    def test_robots_advertises_only_public_configured_sitemap(self):
        from core.seo_views import robots
        response = robots(request_for('/robots.txt'))
        self.assertIn(b'Sitemap: https://boutique.example.com/sitemap.xml', response.content)
        self.assertIn(b'Disallow: /gestion/', response.content)
        self.assertIn(b'Disallow: /retours/', response.content)
        self.assertNotIn(b'untrusted.example.net', response.content)

    def test_disabled_indexing_blocks_robots_and_empties_sitemap(self):
        for values in ({'SEO_INDEXING_ENABLED': False}, {'DEBUG': True}, {'SITE_URL': 'http://127.0.0.1:8000'}):
            with self.subTest(values=values), override_settings(**values):
                response = self.client.get('/robots.txt')
                self.assertContains(response, 'Disallow: /')
                self.assertNotContains(response, 'Sitemap:')
                self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')
                root = ElementTree.fromstring(self.client.get('/sitemap.xml').content)
                self.assertEqual(root.findall('{*}url'), [])

    def test_local_business_uses_published_hours_and_does_not_invent_rating(self):
        day = {'known': True, 'closed': False, 'periods': [{'start': time(9), 'end': time(12)}]}
        with patch('boutique.services.public_hours', return_value={'opening_days': [day], 'opening_period': None}):
            seo = page_seo(request_for('/'))
        graph = json.loads(seo['json_ld'])['@graph']
        store = next(item for item in graph if item['@type'] == 'ClothingStore')
        self.assertEqual(store['openingHoursSpecification'][0]['opens'], '09:00')
        self.assertEqual(store['address']['postalCode'], '01410')
        self.assertNotIn('aggregateRating', store)
        self.assertNotIn('geo', store)
