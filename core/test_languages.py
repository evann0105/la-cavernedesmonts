from django.test import TestCase, Client
from django.contrib.auth.models import User, Group
from django.urls import reverse
from django_otp.plugins.otp_totp.models import TOTPDevice
from .models import Product, Category


class LanguageTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Femmes', slug='femmes')
        self.product = Product.objects.create(name='Veste de montagne', price=40, description='Douce et chaude.', category=self.category, static_image='core/img/slide1.png', sizes='Taille unique', translations={'en':{'name':'Mountain jacket','description':'Soft and warm.'}})

    def test_five_languages_and_preference_persistence(self):
        labels={'fr':'Trouver mon bonheur','en':'Find my little joy','de':'Mein Lieblingsstück finden','it':'Trova la tua gioia','es':'Encuentra tu alegría'}
        for code,label in labels.items():
            response=self.client.post(reverse('set_language'), {'language':code,'next':'/produits/?categorie=femmes'})
            self.assertRedirects(response, '/produits/?categorie=femmes')
            self.assertEqual(response.cookies['django_language'].value,code)
            page=self.client.get('/')
            self.assertContains(page, label)
            self.assertContains(page, f'<html lang="{code}">')
            self.assertEqual(page.headers['Content-Language'],code)
            for url in ['/produits/','/panier/','/localisation/','/accounts/login/','/accounts/signup/',f'/produits/{self.product.slug}/']:
                page=self.client.get(url)
                self.assertEqual(page.status_code,200)
                self.assertEqual(page.headers['Content-Language'],code)

    def test_language_post_security_and_invalid_codes(self):
        self.assertEqual(Client(enforce_csrf_checks=True).post(reverse('set_language'), {'language':'en'}).status_code,403)
        self.assertEqual(self.client.get(reverse('set_language')).status_code,405)
        self.assertEqual(self.client.post(reverse('set_language'), {'language':'ru'}).status_code,400)
        response=self.client.post(reverse('set_language'),{'language':'en','next':'https://evil.example/'})
        self.assertEqual(response.url,'/')

    def test_translation_and_french_description_fallback(self):
        self.client.cookies['django_language']='en'
        page=self.client.get(f'/produits/{self.product.slug}/')
        self.assertContains(page,'Mountain jacket')
        self.assertContains(page,'Soft and warm.')
        self.assertContains(page,'value="Taille unique"')
        self.assertContains(page,'One size')
        self.assertNotContains(page,'Description available in French.')
        self.assertContains(self.client.get('/produits/?q=Mountain'), 'Mountain jacket')
        self.client.cookies['django_language']='de'
        page=self.client.get(f'/produits/{self.product.slug}/')
        self.assertContains(page,'Beschreibung auf Französisch verfügbar.')
        self.assertContains(page,'Douce et chaude.')
        self.assertContains(page,'lang="fr"')

    def verified_manager(self):
        manager=User.objects.create_user('language-manager',is_staff=True)
        manager.groups.add(Group.objects.get(name='Gestion du catalogue'))
        device=TOTPDevice.objects.create(user=manager,name='default',confirmed=True)
        self.client.force_login(manager,backend='connexion.backends.EmailOrUsernameBackend')
        session=self.client.session;session['otp_device_id']=device.persistent_id;session.save()
        return manager

    def test_reviews_are_private_labeled_examples(self):
        self.assertNotContains(self.client.get('/'),'Exemples fictifs')
        manager=self.verified_manager()
        self.assertContains(self.client.get('/'),'Exemples fictifs, non publiés aux visiteurs')
        session=self.client.session;session.pop('otp_device_id');session.save()
        self.assertNotContains(self.client.get('/'),'Exemples fictifs')

    def test_manager_edits_product_translations(self):
        self.verified_manager()
        url=reverse('core:catalog_edit',args=[self.product.pk])
        self.assertContains(self.client.get(url),'name="description_en"')
        response=self.client.post(url,{'name':self.product.name,'price':'40','description':self.product.description,'category':str(self.category.pk),'sizes':'Taille unique','is_published':'on','name_en':'New jacket','description_en':'Updated description','name_de':'Bergjacke','description_de':'Weich und warm.','photos-TOTAL_FORMS':0,'photos-INITIAL_FORMS':0,'photos-MIN_NUM_FORMS':0,'photos-MAX_NUM_FORMS':50})
        self.assertEqual(response.status_code,302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.translations['de']['name'],'Bergjacke')
        self.client.cookies['django_language']='de'
        self.assertContains(self.client.get(f'/produits/{self.product.slug}/'),'Weich und warm.')
