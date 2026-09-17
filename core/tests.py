from decimal import Decimal
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.core.management import call_command
from django.contrib.staticfiles import finders
from .models import Product


class ShopTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('import_catalogue', verbosity=0)

    def test_catalog_images_and_pages(self):
        self.assertEqual(Product.objects.count(), 110)
        for product in Product.objects.all():
            self.assertIsNotNone(finders.find(product.static_image))
            self.assertEqual(self.client.get(f'/produits/{product.slug}/').status_code, 200)
            self.assertRedirects(self.client.get(f'/preview/{product.slug}/?price=0.01'), f'/produits/{product.slug}/', status_code=301)
            self.assertRedirects(self.client.get(f'/page-produit/{product.slug}/'), f'/produits/{product.slug}/', status_code=301)

    def test_public_pages(self):
        for url in ('/', '/produits/', '/localisation/', '/panier/', '/accounts/login/', '/accounts/signup/'):
            self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.get('/produits/inexistant/').status_code, 404)

    def test_catalog_filter_search_sort(self):
        response = self.client.get('/produits/?categorie=hommes&tri=prix')
        products = list(response.context['products'])
        self.assertTrue(all(p.category.slug == 'hommes' for p in products))
        self.assertEqual([p.price for p in products], sorted(p.price for p in products))
        response = self.client.get('/produits/?q=marmottes')
        self.assertTrue(response.context['products'])
        self.assertTrue(all('marmottes' in p.name.lower() or 'marmottes' in p.description.lower() for p in response.context['products']))

    def test_cart_uses_server_price_and_updates(self):
        p = Product.objects.get(slug='blouson-softshell-homme-impermeable')
        self.client.post(f'/panier/ajouter/{p.slug}/', {'size':'M', 'quantity':'2', 'price':'0.01'})
        response = self.client.get('/panier/')
        self.assertEqual(response.context['total'], Decimal('159.80'))
        key = response.context['cart_items'][0]['key']
        self.client.post('/panier/modifier/', {'key': key, 'quantity': 3})
        self.assertEqual(self.client.get('/panier/').context['total'], Decimal('239.70'))
        self.client.post('/panier/modifier/', {'key':key, 'quantity':0})
        self.assertEqual(self.client.get('/panier/').context['total'], 0)

    def test_bad_size_and_quantity_rejected(self):
        p = Product.objects.get(slug='blouson-softshell-homme-impermeable')
        for data in ({'size':'FAUX'}, {'size':'M','quantity':-1}, {'size':'M','quantity':'abc'}, {'size':'M','quantity':11}):
            self.client.post(f'/panier/ajouter/{p.slug}/', data)
            self.assertFalse(self.client.session.get('cart'))
        self.assertEqual(self.client.get(f'/panier/ajouter/{p.slug}/').status_code, 405)

    def test_csrf_required_for_cart(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post('/panier/modifier/', {}).status_code, 403)

    def test_signup_and_safe_login_redirect(self):
        response = self.client.post('/accounts/signup/', {'username':'test-alpin', 'email':'test@example.test', 'password1':'qZ9!mR5#vT2@xL8', 'password2':'qZ9!mR5#vT2@xL8'})
        self.assertRedirects(response, '/')
        self.assertTrue(User.objects.filter(username='test-alpin').exists())
        self.client.post('/accounts/logout/')
        page = self.client.get('/accounts/login/?next=https://malicious.example/')
        management = page.context['wizard']['management_form']
        response = self.client.post('/accounts/login/?next=https://malicious.example/', {management.add_prefix('current_step'): 'auth', 'auth-username':'test@example.test','auth-password':'qZ9!mR5#vT2@xL8'})
        self.assertRedirects(response, '/')

    def test_weak_password_and_duplicate_email_rejected(self):
        User.objects.create_user('existing', email='same@example.test', password='Strong-test-5491!!')
        response = self.client.post('/accounts/signup/', {'username':'another','email':'SAME@example.test','password1':'123','password2':'123'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='another').exists())

    def test_import_preserves_merchant_changes(self):
        p = Product.objects.first(); p.price = Decimal('45.50'); p.save()
        call_command('import_catalogue', verbosity=0)
        p.refresh_from_db(); self.assertEqual(p.price, Decimal('45.50'))


    def test_home_banners_link_to_matching_collections(self):
        response = self.client.get('/')
        for slug, count, title in [('accessoires-bebe', 10, 'Accessoires bébé'), ('polaires-bebe', 5, 'Polaires bébé'), ('offres-speciales', 0, 'Offres spéciales')]:
            self.assertContains(response, f'/produits/?categorie={slug}')
            page = self.client.get('/produits/', {'categorie': slug})
            self.assertEqual(len(page.context['products']), count)
            self.assertContains(page, f'<h1 class="page-title">{title}</h1>', html=True)
        self.assertContains(self.client.get('/produits/?categorie=offres-speciales'), 'Aucune offre spéciale pour le moment.')

    def test_shared_baby_product_is_not_duplicated(self):
        product = Product.objects.get(slug='chaussettes-antiderapantes-marmottes-blanches')
        self.assertEqual(product.category.slug, 'accessoires')
        self.assertTrue(product.collections.filter(slug='accessoires-bebe').exists())
        call_command('import_catalogue', verbosity=0)
        self.assertEqual(Product.objects.count(), 110)
        self.assertEqual(product.collections.filter(slug='accessoires-bebe').count(), 1)

    def test_merchant_can_manage_special_offers(self):
        from .models import Category
        product = Product.objects.first()
        offers = Category.objects.get(slug='offres-speciales')
        product.collections.add(offers)
        page = self.client.get('/produits/?categorie=offres-speciales')
        self.assertEqual(list(page.context['products']), [product])
        self.assertNotContains(page, 'Aucune offre spéciale pour le moment.')

    def test_import_keeps_collection_edits(self):
        product = Product.objects.get(slug='chaussettes-antiderapantes-marmottes-blanches')
        product.collections.clear()
        call_command('import_catalogue', verbosity=0)
        self.assertFalse(product.collections.exists())


class WomenImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('import_catalogue', verbosity=0)

    def test_complete_source_catalog_and_galleries(self):
        import json
        from pathlib import Path
        from django.conf import settings
        data = json.loads((Path(settings.BASE_DIR) / 'core/femmes_seed.json').read_text())
        self.assertEqual(len(data['products']), 50)
        self.assertEqual(Product.objects.filter(source_url__in=[i['source_url'] for i in data['products']]).count(), 50)
        for item in data['products']:
            product = Product.objects.get(source_url=item['source_url'])
            self.assertEqual(product.category.slug, 'femmes')
            self.assertEqual(product.name, item['name'])
            self.assertEqual(product.price, Decimal(item['price']))
            self.assertEqual(product.description, item['description'])
            self.assertFalse(product.sizes)  # Live stock has not been validated.
            page = self.client.get(f'/produits/{product.slug}/')
            for image in item['images']:
                self.assertIsNotNone(finders.find(image))
                self.assertIn('/static/' + image, page.context['images'])
        response = self.client.get('/produits/', {'categorie': 'femmes'})
        self.assertEqual(len(response.context['products']), 53)

    def test_restart_preserves_merchant_edits_and_product_identity(self):
        product = Product.objects.get(slug='veste-polaire-femme-anapurna-utern-violet')
        product.name = 'Nom personnalisé'
        product.description = 'Conseils personnalisés'
        product.price = Decimal('41.50')
        product.sizes = 'M'
        product.save()
        original_pk = product.pk
        call_command('import_catalogue', verbosity=0)
        product.refresh_from_db()
        self.assertEqual(product.pk, original_pk)
        self.assertEqual(product.name, 'Nom personnalisé')
        self.assertEqual(product.description, 'Conseils personnalisés')
        self.assertEqual(product.price, Decimal('41.50'))
        self.assertEqual(product.sizes, 'M')
        self.assertEqual(Product.objects.count(), 110)

    def test_first_enrichment_preserves_existing_custom_fields(self):
        product = Product.objects.get(slug='veste-polaire-femme-anapurna-utern-violet')
        product.source_url = None
        product.description = 'Mon texte original'
        product.price = Decimal('51.00')
        product.save()
        call_command('import_femmes', verbosity=0)
        product.refresh_from_db()
        self.assertEqual(product.description, 'Mon texte original')
        self.assertEqual(product.price, Decimal('51.00'))
        self.assertIsNotNone(product.source_url)


class MenImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('import_catalogue', verbosity=0)

    def test_complete_mens_catalog_prices_descriptions_and_galleries(self):
        import json
        from pathlib import Path
        from django.conf import settings
        data = json.loads((Path(settings.BASE_DIR) / 'core/hommes_seed.json').read_text())
        self.assertEqual(len(data['products']), 25)
        self.assertEqual(sum(len(i['images']) for i in data['products']), 105)
        response = self.client.get('/produits/', {'categorie': 'hommes'})
        self.assertEqual(len(response.context['products']), 25)
        for item in data['products']:
            product = Product.objects.get(source_url=item['source_url'])
            self.assertEqual(product.category.slug, 'hommes')
            self.assertEqual(product.name, item['name'])
            self.assertEqual(product.price, Decimal(item['price']))
            self.assertEqual(product.description, item['description'])
            if not item['existing']:
                self.assertFalse(product.sizes)
            page = self.client.get(f'/produits/{product.slug}/')
            self.assertEqual(page.status_code, 200)
            for image in item['images']:
                self.assertIsNotNone(finders.find(image))
                self.assertIn('/static/' + image, page.context['images'])

    def test_existing_red_fleece_is_reclassified_without_duplication(self):
        product = Product.objects.get(slug='veste-polaire-de-montagne-rouge')
        self.assertEqual(product.category.slug, 'hommes')
        self.assertIn('/443-', product.source_url)
        self.assertRedirects(self.client.get(f'/page-produit/{product.slug}/'),
                            f'/produits/{product.slug}/', status_code=301)
        original_id = product.pk
        call_command('import_catalogue', verbosity=0)
        product.refresh_from_db()
        self.assertEqual(product.pk, original_id)
        self.assertEqual(Product.objects.count(), 110)

    def test_reimport_preserves_merchant_edits_and_category(self):
        from .models import Category
        product = Product.objects.get(slug='veste-polaire-de-montagne-rouge')
        product.price = Decimal('49.00')
        product.name = 'Nom de la boutique'
        product.description = 'Conseils de la boutique'
        product.sizes = 'L'
        product.category = Category.objects.get(slug='femmes')
        product.save()
        call_command('import_catalogue', verbosity=0)
        product.refresh_from_db()
        self.assertEqual(product.price, Decimal('49.00'))
        self.assertEqual(product.name, 'Nom de la boutique')
        self.assertEqual(product.description, 'Conseils de la boutique')
        self.assertEqual(product.sizes, 'L')
        self.assertEqual(product.category.slug, 'femmes')
        self.assertEqual(Product.objects.count(), 110)


class ChildrenImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('import_catalogue', verbosity=0)

    def test_complete_child_catalog_and_source_galleries(self):
        import json
        from pathlib import Path
        from django.conf import settings
        data = json.loads((Path(settings.BASE_DIR) / 'core/enfants_seed.json').read_text())
        self.assertEqual(len(data['products']), 32)
        self.assertEqual(sum(len(p['images']) for p in data['products']), 97)
        response = self.client.get('/produits/', {'categorie': 'enfants'})
        displayed = {p.pk for p in response.context['products']}
        self.assertEqual(len(displayed), 32)
        for item in data['products']:
            product = Product.objects.get(source_url=item['source_url'])
            self.assertIn(product.pk, displayed)
            self.assertEqual(product.name, item['name'])
            self.assertEqual(product.price, Decimal(item['price']))
            self.assertEqual(product.description, item['description'])
            self.assertFalse(product.sizes)
            page = self.client.get(f'/produits/{product.slug}/')
            self.assertEqual(page.status_code, 200)
            for image in item['images']:
                self.assertIsNotNone(finders.find(image))
                self.assertIn('/static/' + image, page.context['images'])

    def test_baby_and_accessory_membership_preserved_without_duplicates(self):
        sock = Product.objects.get(slug='chaussettes-antiderapantes-marmottes-blanches')
        self.assertEqual(sock.category.slug, 'accessoires')
        self.assertEqual(set(sock.collections.values_list('slug', flat=True)), {'accessoires-bebe', 'enfants'})
        baby = Product.objects.get(slug='bebe-101-echarpe-bebe')
        self.assertEqual(baby.category.slug, 'accessoires-bebe')
        self.assertTrue(baby.collections.filter(slug='enfants').exists())
        original_ids = set(Product.objects.values_list('pk', flat=True))
        call_command('import_catalogue', verbosity=0)
        self.assertEqual(set(Product.objects.values_list('pk', flat=True)), original_ids)
        self.assertEqual(Product.objects.count(), 110)
        for slug, expected in [('accessoires-bebe', 10), ('polaires-bebe', 5), ('enfants', 32)]:
            self.assertEqual(len(self.client.get('/produits/', {'categorie': slug}).context['products']), expected)

    def test_enriched_baby_keeps_later_merchant_edits(self):
        product = Product.objects.get(slug='bebe-101-echarpe-bebe')
        product.name = 'Écharpe personnalisée'
        product.price = Decimal('19.00')
        product.description = 'Nouveaux conseils'
        product.sizes = 'Taille unique'
        product.save()
        product.collections.clear()
        call_command('import_catalogue', verbosity=0)
        product.refresh_from_db()
        self.assertEqual(product.name, 'Écharpe personnalisée')
        self.assertEqual(product.price, Decimal('19.00'))
        self.assertEqual(product.description, 'Nouveaux conseils')
        self.assertEqual(product.sizes, 'Taille unique')
        self.assertFalse(product.collections.exists())
