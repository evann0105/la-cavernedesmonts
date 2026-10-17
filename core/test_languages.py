from unittest.mock import patch
from core.templatetags.shop_reviews import REVIEWS
from connexion.test_helpers import verify_test_user
from connexion.models import EmailVerification
from django.test import TestCase, Client
from django.contrib.auth.models import Group
from django.contrib.auth import get_user_model
User = get_user_model()
from django.urls import reverse
from django_otp.plugins.otp_totp.models import TOTPDevice
from .models import Product, Category


class LanguageTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Femmes', slug='femmes')
        self.product = Product.objects.create(name='Veste de montagne', price=40, description='Douce et chaude.', category=self.category, static_image='core/img/slide1.png', sizes='Taille unique', translations={'en':{'name':'Mountain jacket','description':'Soft and warm.'}})

    def test_old_language_cookies_and_browser_preferences_cannot_translate_pages(self):
        urls = ['/', '/produits/', '/panier/', '/magasins/', '/accounts/login/',
                '/accounts/signup/', f'/produits/{self.product.slug}/']
        for code in ['en', 'de', 'it', 'es']:
            self.client.cookies['django_language'] = code
            for url in urls:
                with self.subTest(language=code, url=url):
                    page = self.client.get(url, HTTP_ACCEPT_LANGUAGE=f'{code},en;q=0.9')
                    self.assertEqual(page.status_code, 200)
                    self.assertContains(page, '<html lang="fr">')
                    self.assertEqual(page.headers['Content-Language'], 'fr')
                    self.assertContains(page, 'Notre boutique')
                    self.assertNotContains(page, 'Our shop')

    def test_removed_language_endpoint_cannot_change_preferences(self):
        self.assertEqual(self.client.get('/i18n/setlang/').status_code, 404)
        self.assertEqual(self.client.post('/i18n/setlang/', {'language':'en','next':'https://evil.example/'}).status_code, 404)

    def test_product_displays_french_despite_existing_english_copy(self):
        self.client.cookies['django_language'] = 'en'
        page = self.client.get(f'/produits/{self.product.slug}/', HTTP_ACCEPT_LANGUAGE='en')
        self.assertContains(page, 'Veste de montagne')
        self.assertContains(page, 'Douce et chaude.')
        self.assertNotContains(page, 'Mountain jacket')
        self.assertNotContains(page, 'Soft and warm.')

    def verified_manager(self):
        manager=User.objects.create_user('language-manager',is_staff=True)
        manager.groups.add(Group.objects.get(name='Gestion du catalogue'))
        verify_test_user(manager)
        device=TOTPDevice.objects.create(user=manager,name='default',confirmed=True)
        self.client.force_login(manager,backend='connexion.backends.EmailOrUsernameBackend')
        session=self.client.session;session['otp_device_id']=device.persistent_id;session.save()
        return manager

    @patch('core.templatetags.shop_reviews.sample', return_value=list(REVIEWS[:3]))
    def test_real_reviews_are_public_and_replace_examples(self, choose):
        for authenticated in (False, True):
            if authenticated:
                self.verified_manager()
            response = self.client.get('/')
            self.assertContains(response, 'Nathalie Largillet')
            self.assertContains(response, 'Clemence Guillaume')
            self.assertContains(response, '4/5')
            self.assertContains(response, 'datetime="2021-10"')
            self.assertNotContains(response, 'Exemples fictifs')
            self.assertNotContains(response, 'Exemple fictif')

    def test_product_translation_editor_is_removed_and_existing_copy_preserved(self):
        self.verified_manager()
        url = reverse('core:catalog_edit', args=[self.product.pk])
        self.assertNotContains(self.client.get(url), 'name="description_en"')
        original = self.product.translations.copy()
        response = self.client.post(url, {
            'name':self.product.name, 'price':'40', 'description':self.product.description,
            'category':str(self.category.pk), 'sizes':'Taille unique', 'is_published':'on',
            'photos-TOTAL_FORMS':0, 'photos-INITIAL_FORMS':0,
            'stock-TOTAL_FORMS':0, 'stock-INITIAL_FORMS':0,
            'colors-TOTAL_FORMS':0, 'colors-INITIAL_FORMS':0,
        })
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.translations, original)
