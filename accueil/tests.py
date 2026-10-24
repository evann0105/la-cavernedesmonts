"""Editorial settings preserve publication rules, safe links and existing defaults."""
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from PIL import Image
from connexion.models import EmailVerification
from connexion.test_helpers import verify_test_user
from core.models import Product
from .configuration import configuration
from .models import FeaturedHomepageProduct, HomepageConfiguration
from .schema import OPTIONAL_SECTIONS, SECTIONS

BACKEND = 'connexion.backends.EmailOrUsernameBackend'


def photo():
    output = BytesIO()
    Image.new('RGB', (120, 80), (31, 61, 52)).save(output, format='PNG')
    return SimpleUploadedFile('photo.png', output.getvalue(), content_type='image/png')


class Structure(HTMLParser):
    def __init__(self, html):
        super().__init__(); self.h1s = 0; self.ids = []; self.feed(html)
    def handle_starttag(self, tag, attrs):
        if tag == 'h1': self.h1s += 1
        if 'id' in dict(attrs): self.ids.append(dict(attrs)['id'])


@override_settings(LOCAL_SKIP_EMAIL_VERIFICATION=False, EMAIL_DELIVERY_ENABLED=False)
class HomepageManagementTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        settings = override_settings(MEDIA_ROOT=self.directory.name)
        settings.enable(); self.addCleanup(settings.disable)
        self.manager = get_user_model().objects.create_user('homepage-manager',
            email='homepage-manager@example.test', is_staff=True, is_superuser=True)
        verify_test_user(self.manager)
        self.client.force_login(self.manager, backend=BACKEND)
        self.products = [Product.objects.create(name=f'Article accueil {i}', price=20 + i,
                         static_image=f'core/img/home-test-{i}.webp') for i in range(5)]

    def url(self, key): return reverse('homepage:section', kwargs={'section': key})
    def data(self, key='hero', **changes):
        data = {field: spec['default'] for field, spec in SECTIONS[key]['fields'].items()}
        data['revision'] = configuration()['revision']; data.update(changes); return data
    def save(self, key='hero', **changes): return self.client.post(self.url(key), self.data(key, **changes))
    def products_data(self, products):
        return {'revision': configuration()['revision'], **{
            f'position_{i+1}': str(product.pk) if product else '' for i, product in enumerate(products)}}

    def test_public_and_every_admin_get_create_no_settings(self):
        urls = ['/', reverse('core:catalog_homepage'), reverse('homepage:sections'),
                reverse('homepage:featured'), reverse('core:homepage_collections')]
        for url in urls + [self.url(key) for key in SECTIONS]:
            with self.subTest(url=url): self.assertEqual(self.client.get(url).status_code, 200)
        self.assertFalse(HomepageConfiguration.objects.exists())
        self.assertFalse(FeaturedHomepageProduct.objects.exists())

    def test_default_home_keeps_text_images_links_and_one_h1(self):
        page = self.client.get('/')
        for expected in ('Un peu plus dehors.', 'Votre envie du moment.',
                         'montagne-mobile-600.webp', 'randonnee-1536.webp', 'categorie=offres-speciales'):
            self.assertContains(page, expected)
        structure = Structure(page.content.decode())
        self.assertEqual(structure.h1s, 1)
        self.assertEqual(len(structure.ids), len(set(structure.ids)))

    def test_independent_sections_and_escaped_public_text(self):
        self.assertEqual(self.save('feature', title_line1='Une invitation personnalisée.').status_code, 302)
        title = '<script>alert("test")</script>'
        self.assertEqual(self.save(title_line1=title).status_code, 302)
        page = self.client.get('/')
        self.assertContains(page, 'Une invitation personnalisée.')
        self.assertContains(page, '&lt;script&gt;'); self.assertNotContains(page, title)
        self.assertEqual(configuration()['revision'], 2)
        self.assertEqual(Structure(page.content.decode()).h1s, 1)

    def test_invalid_destinations_and_empty_title_do_not_write(self):
        for changes in ({'button_target': 'javascript:alert(1)'},
                        {'button_target': 'https://outside.example.test/'}, {'title_line1': ''}):
            with self.subTest(changes=changes):
                response = self.save(**changes)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['form'].errors)
                self.assertFalse(HomepageConfiguration.objects.exists())
        self.assertEqual(self.client.get(self.url('unknown')).status_code, 404)

    def test_button_destination_is_used(self):
        self.assertEqual(self.save(button_label='Découvrir les nouveautés', button_target='nouveautes').status_code, 302)
        self.assertContains(self.client.get('/'), 'href="' + reverse('espace:new') + '"')
        self.assertContains(self.client.get('/'), 'Découvrir les nouveautés')
        self.assertEqual(self.save(button_target='accessoires').status_code, 302)
        self.assertContains(self.client.get('/'), 'href="' + reverse('core:product_list') + '?categorie=accessoires"')

    def test_old_tab_cannot_overwrite_latest_content(self):
        old = self.data(title_line1='Ancienne fenêtre')
        self.assertEqual(self.save(title_line1='Dernière version').status_code, 302)
        self.assertEqual(self.client.post(self.url('hero'), old).status_code, 409)
        self.assertContains(self.client.get('/'), 'Dernière version')
        self.assertNotContains(self.client.get('/'), 'Ancienne fenêtre')
        self.assertEqual(configuration()['revision'], 1)

    def test_hidden_sections_leave_one_h1_and_no_broken_inspiration_anchor(self):
        self.assertEqual(self.client.post(reverse('homepage:sections'), {'revision': 0}).status_code, 302)
        self.assertTrue(all(not configuration()['visible'][key] for key in OPTIONAL_SECTIONS))
        page = self.client.get('/')
        for missing in ('href="#instants"', 'id="worlds-title"', 'id="selection-title"'):
            self.assertNotContains(page, missing)
        self.assertContains(page, 'id="summit-title"')
        self.assertEqual(Structure(page.content.decode()).h1s, 1)

    def test_hiding_manifesto_keeps_other_sections(self):
        data = {'revision': 0, **{key: 'on' for key in OPTIONAL_SECTIONS if key != 'manifesto'}}
        self.assertEqual(self.client.post(reverse('homepage:sections'), data).status_code, 302)
        page = self.client.get('/')
        self.assertNotContains(page, 'href="#instants"'); self.assertContains(page, 'id="worlds-title"')

    def test_featured_positions_determine_public_order(self):
        selected = [self.products[3], self.products[0], self.products[4], self.products[1]]
        self.assertEqual(self.client.post(reverse('homepage:featured'), self.products_data(selected)).status_code, 302)
        expected = [product.pk for product in selected]
        self.assertEqual(configuration()['featured_ids'], expected)
        self.assertEqual([p.pk for p in self.client.get('/').context['products']], expected)

    def test_invalid_products_or_duplicates_do_not_replace_selection(self):
        selected = self.products[:4]
        self.client.post(reverse('homepage:featured'), self.products_data(selected))
        draft = Product.objects.create(name='Article privé', price=20, is_published=False, static_image='private.webp')
        no_photo = Product.objects.create(name='Sans photo', price=20)
        for values in ([selected[0], selected[0], None, None], [draft, None, None, None], [no_photo, None, None, None]):
            result = self.client.post(reverse('homepage:featured'), self.products_data(values))
            self.assertEqual(result.status_code, 200); self.assertTrue(result.context['form'].errors)
            self.assertEqual(configuration()['featured_ids'], [p.pk for p in selected])

    def test_withdrawn_or_deleted_product_is_never_exposed(self):
        chosen = self.products[:4]
        self.client.post(reverse('homepage:featured'), self.products_data(chosen))
        chosen[0].is_published = False; chosen[0].save(update_fields=['is_published'])
        chosen[1].delete()
        self.assertEqual([p.pk for p in self.client.get('/').context['products']], [chosen[2].pk, chosen[3].pk])
        self.assertNotContains(self.client.get('/'), chosen[0].name)
        Product.objects.filter(pk__in=[chosen[2].pk, chosen[3].pk]).update(is_published=False)
        self.assertEqual([p.pk for p in self.client.get('/').context['products']], [self.products[4].pk])

    def test_clearing_products_restores_automatic_selection(self):
        self.client.post(reverse('homepage:featured'), self.products_data(self.products[:4]))
        self.assertEqual(self.client.post(reverse('homepage:featured'), self.products_data([None]*4)).status_code, 302)
        self.assertFalse(FeaturedHomepageProduct.objects.exists())
        expected = list(Product.objects.filter(is_published=True).order_by('-is_featured', 'id').values_list('pk', flat=True)[:4])
        self.assertEqual([p.pk for p in self.client.get('/').context['products']], expected)

    def test_image_upload_and_restoration(self):
        data = self.data(); data['hero_image'] = photo()
        self.assertEqual(self.client.post(self.url('hero'), data).status_code, 302)
        instance = HomepageConfiguration.objects.get(pk=1)
        self.assertTrue(instance.hero_image.name.startswith('homepage/'))
        with Image.open(instance.hero_image.path) as image:
            self.assertEqual(image.format, 'WEBP'); self.assertEqual(image.size, (120, 80))
        self.assertContains(self.client.get('/'), instance.hero_image.url)
        clear = self.data(); clear['hero_image-clear'] = 'on'
        self.assertEqual(self.client.post(self.url('hero'), clear).status_code, 302)
        self.assertFalse(HomepageConfiguration.objects.get(pk=1).hero_image)
        self.assertContains(self.client.get('/'), 'montagne-1536.webp')

    def test_invalid_and_stale_uploads_leave_no_files(self):
        data = self.data()
        data['hero_image'] = SimpleUploadedFile('photo.svg', b'<svg/>', content_type='image/svg+xml')
        self.assertEqual(self.client.post(self.url('hero'), data).status_code, 200)
        self.assertFalse(HomepageConfiguration.objects.exists())
        stale = self.data(); stale['hero_image'] = photo()
        self.save(title_line1='Version récente')
        self.assertEqual(self.client.post(self.url('hero'), stale).status_code, 409)
        self.assertFalse(HomepageConfiguration.objects.get(pk=1).hero_image)
        self.assertFalse(any(Path(self.directory.name).rglob('*')))

    def test_permission_email_and_csrf_are_required(self):
        self.client.logout()
        self.assertEqual(self.client.post(self.url('hero'), self.data()).status_code, 302)
        customer = get_user_model().objects.create_user('homepage-customer', email='customer@example.test')
        self.client.force_login(customer, backend=BACKEND)
        self.assertEqual(self.client.get(self.url('hero')).status_code, 403)
        self.assertEqual(self.client.post(self.url('hero'), self.data()).status_code, 403)
        EmailVerification.objects.filter(user=self.manager).delete()
        self.client.force_login(self.manager, backend=BACKEND)
        self.assertEqual(self.client.post(self.url('hero'), self.data()).status_code, 302)
        strict = Client(enforce_csrf_checks=True); strict.force_login(self.manager, backend=BACKEND)
        self.assertEqual(strict.post(self.url('hero'), self.data()).status_code, 403)
        self.assertFalse(HomepageConfiguration.objects.exists())

    def test_unrelated_hub_post_does_not_reset_collection_cards(self):
        self.assertEqual(self.client.post(reverse('core:catalog_homepage'), {'revision': 0, 'title': 'Wrong route'}).status_code, 400)
        self.assertFalse(HomepageConfiguration.objects.exists())
