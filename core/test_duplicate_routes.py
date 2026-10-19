"""Public duplicate URLs preserve product identity and avoid duplicate index entries."""
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from django_otp.plugins.otp_totp.models import TOTPDevice

from connexion.test_helpers import verify_test_user
from core.catalog_forms import CatalogProductForm
from core.models import Category, Product
from core.product_images import imported_gallery_urls


@override_settings(DEBUG=False, SITE_URL='https://la-cavernedesmonts.fr',
                   SEO_INDEXING_ENABLED=True, SECURE_SSL_REDIRECT=False,
                   STATIC_URL='/static/',
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class VerifiedDuplicateRouteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name='Femmes', slug='femmes')
        cls.canonical = Product.objects.create(
            name='Veste polaire blanche LHOTSE', slug='canonical-gallery',
            category=cls.category, price='49.90', brand='LHOTSE',
            description='Veste polaire sans manche blanche.',
            static_image='core/img/products/canonical-gallery/00.jpg',
            include_imported_gallery=True,
        )
        cls.duplicate = Product.objects.create(
            name=cls.canonical.name, slug='duplicate-gallery',
            category=cls.category, price=cls.canonical.price,
            description=cls.canonical.description,
            static_image='core/img/products/duplicate-gallery/00.jpg',
            duplicate_of=cls.canonical, is_published=False,
            include_imported_gallery=True,
        )

    def manager_login(self):
        manager = get_user_model().objects.create_user('duplicate-route-manager', is_staff=True)
        manager.user_permissions.add(Permission.objects.get(content_type__app_label='core', codename='manage_catalog'))
        verify_test_user(manager)
        device = TOTPDevice.objects.create(user=manager, name='default', confirmed=True)
        self.client.force_login(manager, backend='connexion.backends.EmailOrUsernameBackend')
        session = self.client.session
        session['otp_device_id'] = device.persistent_id
        session.save()
        return manager

    def product_url(self, product):
        return reverse('core:product_detail', args=[product.slug])

    def test_archived_product_redirects_to_correct_identity_without_forwarding_queries(self):
        old = self.product_url(self.duplicate)
        canonical = self.product_url(self.canonical)
        response = self.client.get(old + '?variante=123&token=private-token&utm_source=old-shop')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], canonical)
        self.assertNotIn('?', response['Location'])
        target = self.client.get(response['Location'])
        self.assertEqual(target.status_code, 200)
        self.assertEqual(target.context['product'].pk, self.canonical.pk)

    def test_legacy_preview_url_redirects_directly_to_canonical_product(self):
        response = self.client.get(reverse('core:product_preview', args=[self.duplicate.slug]) + '?variante=999&q=test')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], self.product_url(self.canonical))

    def test_archived_url_never_redirects_to_unpublished_canonical(self):
        Product.objects.filter(pk=self.canonical.pk).update(is_published=False)
        for path in (self.product_url(self.duplicate), reverse('core:product_preview', args=[self.duplicate.slug])):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn('Location', response)

    def test_authorized_admin_can_still_preview_the_archived_record(self):
        url = reverse('core:catalog_preview', args=[self.duplicate.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.manager_login()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['product'].pk, self.duplicate.pk)
        self.assertTrue(response.context['catalog_preview'])
        self.assertIn('noindex', response['X-Robots-Tag'])
        self.assertNotIn('Location', response)

    def test_catalogue_sitemap_and_admin_seo_count_only_the_canonical_entry(self):
        catalogue = self.client.get(reverse('core:product_list'))
        self.assertEqual(catalogue.status_code, 200)
        visible_ids = [product.pk for product in catalogue.context['products']]
        self.assertIn(self.canonical.pk, visible_ids)
        self.assertNotIn(self.duplicate.pk, visible_ids)
        sitemap = self.client.get('/sitemap.xml')
        self.assertEqual(sitemap.status_code, 200)
        self.assertContains(sitemap, 'https://la-cavernedesmonts.fr' + self.product_url(self.canonical))
        self.assertNotContains(sitemap, self.product_url(self.duplicate))
        self.manager_login()
        overview = self.client.get(reverse('core:seo_overview'))
        self.assertEqual(overview.status_code, 200)
        shown_ids = [row['product'].pk for row in overview.context['page_obj']]
        self.assertIn(self.canonical.pk, shown_ids)
        self.assertNotIn(self.duplicate.pk, shown_ids)
        self.assertEqual(overview.context['archived_duplicates'], 1)
        self.assertFalse(any(row['duplicate_title'] for row in overview.context['page_obj']))

    def test_catalogue_form_refuses_to_republish_or_feature_an_archived_duplicate(self):
        for flag in ('is_published', 'is_featured'):
            with self.subTest(flag=flag):
                archived = Product.objects.get(pk=self.duplicate.pk)
                form = CatalogProductForm(instance=archived, data={
                    'name': archived.name, 'price': '49,90',
                    'category': self.category.pk,
                    'description': archived.description,
                    'include_imported_gallery': 'on', flag: 'on',
                })
                self.assertFalse(form.is_valid())
                self.assertIn('is_published', form.errors)
                self.assertIn('doublon est archivé', str(form.errors['is_published']))
        unchanged = Product.objects.get(pk=self.duplicate.pk)
        self.assertFalse(unchanged.is_published)
        self.assertFalse(unchanged.is_featured)

    def test_imported_alias_gallery_preserves_extra_photos_and_deduplicates_image_bytes(self):
        with TemporaryDirectory() as directory, override_settings(BASE_DIR=Path(directory)):
            base = Path(directory) / 'core/static'
            primary = base / self.canonical.static_image
            primary.parent.mkdir(parents=True)
            primary.write_bytes(b'original photograph bytes')
            # A second path to the exact primary image must not create another thumbnail.
            alias_folder = base / 'core/img/femmes' / self.duplicate.slug
            alias_folder.mkdir(parents=True)
            (alias_folder / '00.jpg').write_bytes(primary.read_bytes())
            (alias_folder / '01.jpg').write_bytes(b'other photograph, back view')
            initial = ['/static/' + self.canonical.static_image]
            expected_extra = '/static/core/img/femmes/' + self.duplicate.slug + '/01.jpg'
            result = imported_gallery_urls(self.canonical, initial)
            self.assertEqual(result, initial + [expected_extra])
            self.assertEqual(initial, ['/static/' + self.canonical.static_image])
            self.assertEqual((alias_folder / '00.jpg').read_bytes(), primary.read_bytes())
            self.assertEqual((alias_folder / '01.jpg').read_bytes(), b'other photograph, back view')

    def test_disabling_imported_gallery_respects_canonical_and_archived_settings(self):
        with TemporaryDirectory() as directory, override_settings(BASE_DIR=Path(directory)):
            alias_folder = Path(directory) / 'core/static/core/img/femmes' / self.duplicate.slug
            alias_folder.mkdir(parents=True)
            (alias_folder / '01.jpg').write_bytes(b'alias extra photograph')
            initial = ['/media/products/merchant-upload.webp']
            self.assertEqual(len(imported_gallery_urls(self.canonical, initial)), 2)
            Product.objects.filter(pk=self.duplicate.pk).update(include_imported_gallery=False)
            self.assertEqual(imported_gallery_urls(self.canonical, initial), initial)
            Product.objects.filter(pk=self.duplicate.pk).update(include_imported_gallery=True)
            self.canonical.include_imported_gallery = False
            self.assertEqual(imported_gallery_urls(self.canonical, initial), initial)
