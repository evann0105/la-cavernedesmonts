from connexion.test_helpers import verify_test_user
from connexion.models import EmailVerification
from django.contrib.auth.models import User, Group
from django.test import TestCase, Client
from django.urls import reverse
from django_otp.plugins.otp_totp.models import TOTPDevice
from .models import Category, Product, HomepageSelection


class HomepageSelectionTests(TestCase):
    def setUp(self):
        HomepageSelection.objects.all().delete()
        self.products = {}
        for slot in ('femmes', 'hommes', 'enfants'):
            category = Category.objects.create(name=slot, slug=slot)
            self.products[slot] = Product.objects.create(name=f'Produit {slot}', price=20, category=category, static_image=f'core/img/{slot}.png')
        self.manager = User.objects.create_user('gerante', is_staff=True)
        self.manager.groups.add(Group.objects.get(name='Gestion du catalogue'))
        verify_test_user(self.manager)
        self.device = TOTPDevice.objects.create(user=self.manager, name='default', confirmed=True)
        self.client.force_login(self.manager, backend='connexion.backends.EmailOrUsernameBackend')
        session = self.client.session
        session['otp_device_id'] = self.device.persistent_id
        session.save()
        self.url = reverse('core:catalog_homepage')

    def payload(self):
        return {slot:str(p.pk) for slot,p in self.products.items()}

    def test_save_all_three_and_render_real_images(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertRedirects(self.client.post(self.url, self.payload()), self.url)
        self.assertEqual(HomepageSelection.objects.count(), 3)
        page = self.client.get('/')
        self.assertEqual([c['product'].pk for c in page.context['world_cards']], [p.pk for p in self.products.values()])
        for product in self.products.values():
            self.assertContains(page, product.image_url)
        self.assertContains(page, '/produits/?categorie=hommes')

    def test_new_choice_replaces_old_and_latest_photo_is_used(self):
        self.client.post(self.url, self.payload())
        alternative = Product.objects.create(name='Autre femme', category=self.products['femmes'].category, price=25, static_image='nouvelle.png')
        data = self.payload(); data['femmes'] = str(alternative.pk)
        self.client.post(self.url, data)
        alternative.static_image = 'remplacement.webp'; alternative.save()
        self.assertEqual(self.client.get('/').context['world_cards'][0]['product'], alternative)
        self.assertContains(self.client.get('/'), '/static/remplacement.webp')

    def test_invalid_category_draft_missing_image_or_id_rejected_atomically(self):
        self.client.post(self.url, self.payload())
        draft = Product.objects.create(name='Brouillon', price=20, category=self.products['femmes'].category, static_image='draft.png', is_published=False)
        no_image = Product.objects.create(name='Sans photo', price=20, category=self.products['femmes'].category)
        for invalid in (self.products['hommes'].pk, draft.pk, no_image.pk, 999999):
            data = self.payload(); data['femmes'] = str(invalid); data['hommes'] = ''
            response = self.client.post(self.url, data)
            self.assertEqual(response.status_code, 200)
            self.assertIn('femmes', response.context['form'].errors)
            self.assertEqual(HomepageSelection.objects.get(slot='hommes').product, self.products['hommes'])

    def test_withdrawn_recategorized_or_deleted_choice_does_not_leak(self):
        self.client.post(self.url, self.payload())
        product = self.products['femmes']
        product.is_published = False; product.save()
        self.assertIsNone(self.client.get('/').context['world_cards'][0]['product'])
        product.is_published = True; product.category = self.products['hommes'].category; product.save()
        self.assertIsNone(self.client.get('/').context['world_cards'][0]['product'])
        product.delete()
        self.assertEqual(self.client.get('/').status_code, 200)

    def test_additional_collection_allowed_and_automatic_fallback(self):
        product = self.products['hommes']
        product.collections.add(self.products['femmes'].category)
        data = self.payload(); data['femmes'] = str(product.pk)
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.assertEqual(self.client.get('/').context['world_cards'][0]['product'], product)
        self.assertEqual(self.client.post(self.url, {slot:'' for slot in self.products}).status_code, 302)
        self.assertEqual(HomepageSelection.objects.filter(product=None).count(), 3)
        self.assertTrue(all(c['product'] for c in self.client.get('/').context['world_cards']))

    def test_access_requires_catalog_permission_email_and_csrf(self):
        self.client.logout()
        self.assertEqual(self.client.post(self.url, self.payload()).status_code, 302)
        customer = User.objects.create_user('client')
        self.client.force_login(customer, backend='connexion.backends.EmailOrUsernameBackend')
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, self.payload()).status_code, 403)
        EmailVerification.objects.filter(user=self.manager).delete()
        self.client.force_login(self.manager, backend='connexion.backends.EmailOrUsernameBackend')
        self.assertEqual(self.client.post(self.url, self.payload()).status_code, 302)
        self.assertEqual(Client(enforce_csrf_checks=True).post(self.url, self.payload()).status_code, 403)
        self.assertFalse(HomepageSelection.objects.exists())
