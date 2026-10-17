from connexion.test_helpers import verify_test_user
from connexion.models import EmailVerification
import tempfile
from io import BytesIO
from decimal import Decimal
from django.contrib.auth.models import Group
from django.contrib.auth import get_user_model
User = get_user_model()
from django.contrib.admin.models import LogEntry
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, SimpleTestCase, Client, override_settings
from django.urls import reverse
from django_otp.plugins.otp_totp.models import TOTPDevice
from PIL import Image
from .models import Product, ProductImage, Category


def photo(name='photo.png'):
    stream = BytesIO()
    Image.new('RGB', (60, 40), 'green').save(stream, format='PNG')
    return SimpleUploadedFile(name, stream.getvalue(), content_type='image/png')


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class CatalogManagementTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.manager = User.objects.create_user('manager', is_staff=True)
        self.manager.groups.add(Group.objects.get(name='Gestion du catalogue'))
        self.customer = User.objects.create_user('customer')
        self.category = Category.objects.create(name='Femmes', slug='femmes')
        self.baby = Category.objects.create(name='Polaires bébé', slug='polaires-bebe')
        self.product = Product.objects.create(name='Une polaire', slug='une-polaire', price='30', category=self.category, description='Douce et chaude', sizes='M, L', static_image='core/img/slide1.png')
        self.device = TOTPDevice.objects.create(user=self.manager, name='default', confirmed=True)
        self.login_verified()

    def login_verified(self, client=None):
        client = client or self.client
        client.force_login(self.manager, backend='connexion.backends.EmailOrUsernameBackend')
        verify_test_user(self.manager)
        session = client.session
        session['otp_device_id'] = self.device.persistent_id
        session.save()

    def data(self, **overrides):
        result = {'name':'Une polaire', 'price':'39,90', 'description':'Une description personnalisée', 'sizes':'M, L, M', 'category':str(self.category.pk), 'collections':[str(self.baby.pk)], 'is_published':'on', 'include_imported_gallery':'on', 'photos-TOTAL_FORMS':'0', 'photos-INITIAL_FORMS':'0', 'photos-MIN_NUM_FORMS':'0', 'photos-MAX_NUM_FORMS':'50'}
        result.update({'stock-TOTAL_FORMS':'1', 'stock-INITIAL_FORMS':'0', 'stock-MIN_NUM_FORMS':'0', 'stock-MAX_NUM_FORMS':'50'})
        result.update({'colors-TOTAL_FORMS':'1', 'colors-INITIAL_FORMS':'0', 'colors-MIN_NUM_FORMS':'0', 'colors-MAX_NUM_FORMS':'30'})
        result.update(overrides)
        return result

    def edit_url(self, product=None):
        return reverse('core:catalog_edit', args=[(product or self.product).pk])

    def test_management_pages_and_private_preview(self):
        for url in [reverse('core:catalog_list'), reverse('core:catalog_add'), self.edit_url(), reverse('core:catalog_preview', args=[self.product.pk])]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertIn('no-store', response['Cache-Control'])
        self.assertNotContains(self.client.get('/'), 'Gérer les produits')
        self.assertContains(self.client.get('/mon-compte/'), 'Gérer les produits')

    def test_guards_for_anonymous_customer_staff_and_unverified_manager(self):
        paths = [reverse('core:catalog_list'), reverse('core:catalog_add'), self.edit_url(), reverse('core:catalog_preview', args=[self.product.pk])]
        self.client.logout()
        for path in paths:
            self.assertEqual(self.client.get(path).status_code, 302)
            self.assertEqual(self.client.post(path, self.data()).status_code, 302)
        for user in [self.customer, User.objects.create_user('staff-only', is_staff=True)]:
            self.client.force_login(user, backend='connexion.backends.EmailOrUsernameBackend')
            for path in paths:
                self.assertEqual(self.client.get(path).status_code, 403)
                self.assertEqual(self.client.post(path, self.data()).status_code, 403)
        EmailVerification.objects.filter(user=self.manager).delete()
        self.client.force_login(self.manager, backend='connexion.backends.EmailOrUsernameBackend')
        for path in paths:
            self.assertEqual(self.client.get(path).status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.price, Decimal('30'))

    def test_manager_without_verified_email_goes_to_confirmation(self):
        EmailVerification.objects.filter(user=self.manager).delete()
        self.assertRedirects(self.client.get(self.edit_url()), reverse('connexion:verify_email'), fetch_redirect_response=False)

    def test_create_and_publish_with_images_category_and_price(self):
        response = self.client.post(reverse('core:catalog_add'), self.data(name='Nouveau bébé', main_image=photo(), new_photos=[photo('second.png'), photo('third.png')]))
        self.assertEqual(response.status_code, 302)
        p = Product.objects.get(name='Nouveau bébé')
        self.assertRedirects(response, reverse('core:product_detail', args=[p.slug]))
        self.assertEqual(p.price, Decimal('39.90'))
        self.assertEqual(p.sizes, 'M, L')
        self.assertTrue(p.main_image.name.endswith('.webp'))
        self.assertEqual(p.images.count(), 2)
        self.assertContains(self.client.get('/produits/?categorie=polaires-bebe'), p.name)
        self.assertContains(self.client.get(f'/produits/{p.slug}/'), p.main_image.url)
        self.assertTrue(LogEntry.objects.filter(user=self.manager, object_id=str(p.pk)).exists())

    def test_edit_redirects_to_published_product_or_private_draft_preview(self):
        response = self.client.post(self.edit_url(), self.data())
        self.assertRedirects(response, reverse('core:product_detail', args=[self.product.slug]))
        response = self.client.post(self.edit_url(), self.data(is_published=''))
        self.assertRedirects(response, reverse('core:catalog_preview', args=[self.product.pk]))
        anonymous = Client()
        self.assertEqual(anonymous.get(reverse('core:product_detail', args=[self.product.slug])).status_code, 404)
        self.assertEqual(anonymous.get(response.url).status_code, 302)

    def test_create_product_with_stock_variants_and_audit(self):
        from stocks.models import Variant
        response = self.client.post(reverse('core:catalog_add'), self.data(
            name='Stock initial', main_image=photo(), **{
                'stock-TOTAL_FORMS':'2', 'stock-0-size':' M ', 'stock-0-color':'Bleu',
                'stock-0-quantity':'3', 'stock-0-reference':'REF-M',
                'stock-1-size':'L', 'stock-1-color':'Bleu', 'stock-1-quantity':'0'}))
        product = Product.objects.get(name='Stock initial')
        self.assertRedirects(response, reverse('core:product_detail', args=[product.slug]))
        self.assertEqual(list(product.stock_variants.values_list('size', 'quantity').order_by('size')), [('L', 0), ('M', 3)])
        variant = product.stock_variants.get(size='M')
        self.assertEqual((variant.available, variant.reserved, variant.revision), (3, 0, 1))
        self.assertTrue(LogEntry.objects.filter(user=self.manager, object_id=str(variant.pk), content_type__model='variant').exists())
        cart_response = self.client.post(reverse('core:cart_add', args=[product.slug]), {'variant':variant.pk, 'quantity':2})
        self.assertEqual(cart_response.status_code, 302)
        self.assertContains(self.client.get(reverse('core:cart')), 'Bleu')

    def test_quantity_only_creates_single_size_color_variant(self):
        response = self.client.post(reverse('core:catalog_add'), self.data(
            name='Taille unique', is_published='', **{'stock-0-quantity':'5'}))
        self.assertEqual(response.status_code, 302)
        variant = Product.objects.get(name='Taille unique').stock_variants.get()
        self.assertEqual((variant.size, variant.color, variant.quantity), ('Taille unique', 'Coloris unique', 5))

    def test_invalid_stock_rejects_whole_product_and_preserves_entries(self):
        cases = [
            {'stock-0-quantity':'-1'},
            {'stock-0-quantity':'1.5'},
            {'stock-0-size':'M'},
            {'stock-TOTAL_FORMS':'2', 'stock-0-size':' M ', 'stock-0-color':' BLEU ', 'stock-0-quantity':'1', 'stock-1-size':'m', 'stock-1-color':'bleu', 'stock-1-quantity':'2'},
        ]
        for values in cases:
            with self.subTest(values=values):
                response = self.client.post(reverse('core:catalog_add'), self.data(name='Invalide', is_published='', **values))
                self.assertEqual(response.status_code, 200)
                self.assertFalse(Product.objects.filter(name='Invalide').exists())
                self.assertContains(response, 'Stock initial')

    def test_deleted_stock_row_is_not_saved_and_edit_does_not_reset_stock(self):
        from stocks.models import Variant
        response = self.client.post(reverse('core:catalog_add'), self.data(name='Ignorer stock', is_published='', **{'stock-0-quantity':'4', 'stock-0-DELETE':'on'}))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Product.objects.get(name='Ignorer stock').stock_variants.exists())
        variant = Variant.objects.create(product=self.product, size='M', color='Vert', quantity=5, reserved=2)
        self.assertEqual(self.client.post(self.edit_url(), self.data(**{'stock-0-quantity':'0'})).status_code, 302)
        variant.refresh_from_db()
        self.assertEqual((variant.quantity, variant.reserved), (5, 2))

    def test_missing_or_excessive_stock_management_is_rejected(self):
        for overrides in [{'stock-TOTAL_FORMS':'51'}, {'stock-TOTAL_FORMS':''}]:
            response = self.client.post(reverse('core:catalog_add'), self.data(name='Gestion invalide', is_published='', **overrides))
            self.assertEqual(response.status_code, 200)
            self.assertFalse(Product.objects.filter(name='Gestion invalide').exists())

    def test_color_photo_and_stock_can_be_added_when_editing(self):
        response = self.client.post(self.edit_url(), self.data(**{
            'colors-0-name':' Rouge ', 'colors-0-image':photo('red.jpeg'),
            'stock-0-size':'M', 'stock-0-color':'Rouge', 'stock-0-quantity':'3'}))
        self.assertEqual(response.status_code, 302)
        color = self.product.color_photos.get()
        variant = self.product.stock_variants.get()
        self.assertEqual(color.name, 'Rouge')
        self.assertTrue(color.image.name.endswith('.webp'))
        self.assertEqual(variant.quantity, 3)
        response = self.client.get(reverse('core:product_detail', args=[self.product.slug]))
        self.assertContains(response, 'data-product-color="rouge"')
        self.assertContains(response, 'data-variant-color="rouge"')
        self.assertContains(response, color.image.url)
        self.assertNotContains(self.client.get(self.edit_url()), 'Traductions de la fiche produit')

    def test_color_photo_replacement_and_removal_preserve_stock(self):
        from .models import ProductColor
        from stocks.models import Variant
        color = ProductColor.objects.create(product=self.product, name='Rouge', image=photo())
        variant = Variant.objects.create(product=self.product, size='M', color='Rouge', quantity=4, reserved=1)
        values = {'colors-INITIAL_FORMS':'1', 'colors-0-id':color.pk, 'colors-0-product':self.product.pk, 'colors-0-name':'Rouge'}
        response = self.client.post(self.edit_url(), self.data(**values, **{'colors-0-image':photo('replacement.png')}))
        self.assertEqual(response.status_code, 302)
        color.refresh_from_db()
        self.assertTrue(color.image.name.endswith('.webp'))
        response = self.client.post(self.edit_url(), self.data(**values, **{'colors-0-DELETE':'on'}))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.product.color_photos.exists())
        variant.refresh_from_db()
        self.assertEqual((variant.quantity, variant.reserved), (4, 1))

    def test_duplicate_colors_or_invalid_image_reject_all_changes(self):
        for values in [
            {'colors-0-name':'Rouge', 'colors-0-image':SimpleUploadedFile('bad.jpeg', b'bad', content_type='image/jpeg')},
            {'colors-TOTAL_FORMS':'2', 'colors-0-name':' Rouge ', 'colors-0-image':photo(), 'colors-1-name':'rouge', 'colors-1-image':photo()},
        ]:
            response = self.client.post(self.edit_url(), self.data(price='99', **values))
            self.assertEqual(response.status_code, 200)
            self.product.refresh_from_db()
            self.assertEqual(self.product.price, Decimal('30'))
            self.assertFalse(self.product.color_photos.exists())

    def test_foreign_color_cannot_be_attached_or_deleted(self):
        from .models import ProductColor
        other = Product.objects.create(name='Autre couleur', price=10)
        color = ProductColor.objects.create(product=other, name='Rouge', image=photo())
        values = {'colors-INITIAL_FORMS':'1', 'colors-0-id':color.pk, 'colors-0-product':self.product.pk, 'colors-0-name':'Rouge', 'colors-0-image':photo()}
        response = self.client.post(self.edit_url(), self.data(price='99', **values))
        self.assertEqual(response.status_code, 200)
        color.refresh_from_db()
        self.assertEqual(color.product_id, other.pk)
        self.product.refresh_from_db()
        self.assertEqual(self.product.price, Decimal('30'))

    def test_price_text_and_gallery_edits_preserve_slug_and_escape_text(self):
        old_slug = self.product.slug
        response = self.client.post(self.edit_url(), self.data(name='Renommé', description='<script>alert(1)</script>', new_photos=[photo()]))
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.slug, old_slug)
        self.assertEqual(self.product.price, Decimal('39.90'))
        self.assertContains(self.client.get(f'/produits/{old_slug}/'), '&lt;script&gt;')
        self.assertEqual(self.product.images.count(), 1)

    def test_drafts_are_private_across_catalog_home_cart_and_aliases(self):
        self.client.post(f'/panier/ajouter/{self.product.slug}/', {'size':'M','quantity':1})
        self.client.post(self.edit_url(), self.data(is_published=''))
        self.product.refresh_from_db()
        self.assertFalse(self.product.is_published)
        self.assertNotIn(self.product, self.client.get('/').context['products'])
        self.assertNotIn(self.product, self.client.get('/produits/').context['products'])
        for url in [f'/produits/{self.product.slug}/', f'/preview/{self.product.slug}/', f'/page-produit/{self.product.slug}/']:
            self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(f'/panier/ajouter/{self.product.slug}/', {'size':'M'}).status_code, 404)
        self.assertFalse(self.client.get('/panier/').context['cart_items'])
        self.assertEqual(self.client.get(reverse('core:catalog_preview', args=[self.product.pk])).status_code, 200)

    def test_empty_draft_allowed_publication_requires_photo_description_and_price(self):
        response = self.client.post(reverse('core:catalog_add'), self.data(name='Brouillon', description='', is_published=''))
        self.assertEqual(response.status_code, 302)
        p = Product.objects.get(name='Brouillon')
        self.assertRedirects(response, reverse('core:catalog_preview', args=[p.pk]))
        self.assertFalse(p.is_published)
        response = self.client.post(self.edit_url(p), self.data(description='', price='-1'))
        self.assertEqual(response.status_code, 200)
        for field in ['description', 'price', 'main_image']:
            self.assertIn(field, response.context['form'].errors)
        p.refresh_from_db()
        self.assertFalse(p.is_published)

    def test_invalid_upload_is_rejected_without_partial_product_changes(self):
        for upload in [SimpleUploadedFile('bad.png', b'<script>alert(1)</script>', content_type='image/png'), SimpleUploadedFile('huge.jpg', b'a'*(10*1024*1024+1), content_type='image/jpeg')]:
            response = self.client.post(self.edit_url(), self.data(price='99', main_image=upload))
            self.assertEqual(response.status_code, 200)
            self.assertIn('main_image', response.context['form'].errors)
            self.product.refresh_from_db()
            self.assertEqual(self.product.price, Decimal('30'))

    def test_invalid_main_photo_has_no_misleading_missing_photo_error(self):
        response = self.client.post(reverse('core:catalog_add'), self.data(
            main_image=SimpleUploadedFile('bad.jpeg', b'not an image', content_type='image/jpeg')))
        self.assertEqual(response.status_code, 200)
        errors = response.context['form'].errors['main_image']
        self.assertEqual(len(errors), 1)
        self.assertNotIn('Ajoutez une photo principale avant de publier.', errors)

    def test_gallery_replace_order_and_remove(self):
        image = ProductImage.objects.create(product=self.product, image=photo(), order=1)
        data = self.data(**{'photos-TOTAL_FORMS':'1','photos-INITIAL_FORMS':'1','photos-0-id':str(image.pk),'photos-0-product':str(self.product.pk),'photos-0-order':'4','photos-0-alt':'Vue du dos','photos-0-image':photo('replacement.png')})
        self.assertEqual(self.client.post(self.edit_url(), data).status_code, 302)
        image.refresh_from_db()
        self.assertEqual(image.order, 4)
        self.assertEqual(image.alt, 'Vue du dos')
        self.assertTrue(image.image.name.endswith('.webp'))
        data.pop('photos-0-image')
        data['photos-0-DELETE'] = 'on'
        self.assertEqual(self.client.post(self.edit_url(), data).status_code, 302)
        self.assertFalse(ProductImage.objects.filter(pk=image.pk).exists())

    def test_foreign_gallery_id_rejected(self):
        other = Product.objects.create(name='Autre', price='10')
        image = ProductImage.objects.create(product=other, image=photo())
        response = self.client.post(self.edit_url(), self.data(**{'photos-TOTAL_FORMS':'1','photos-INITIAL_FORMS':'1','photos-0-id':str(image.pk),'photos-0-product':str(self.product.pk),'photos-0-order':'1','photos-0-DELETE':'on'}))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(ProductImage.objects.filter(pk=image.pk, product=other).exists())

    def test_same_name_products_have_unique_stable_urls(self):
        for _ in range(2):
            self.assertEqual(self.client.post(reverse('core:catalog_add'), self.data(name='Même nom', is_published='')).status_code, 302)
        self.assertEqual(Product.objects.filter(name='Même nom').values('slug').distinct().count(), 2)

    def test_post_needs_csrf(self):
        client = Client(enforce_csrf_checks=True)
        self.login_verified(client)
        self.assertEqual(client.post(self.edit_url(), self.data()).status_code, 403)

    def test_original_photo_can_be_replaced_and_gallery_disabled(self):
        self.assertEqual(self.client.post(self.edit_url(), self.data(remove_original='on', include_imported_gallery='', main_image=photo())).status_code, 302)
        self.product.refresh_from_db()
        self.assertFalse(self.product.static_image)
        self.assertFalse(self.product.include_imported_gallery)

    def test_search_and_status_filters(self):
        Product.objects.create(name='Invisible', price='5', is_published=False)
        response = self.client.get(reverse('core:catalog_list'), {'statut':'draft','q':'Invisible'})
        self.assertEqual(len(response.context['page_obj']), 1)
        self.assertEqual(response.context['page_obj'][0].name, 'Invisible')


class ShopImageUploadTests(SimpleTestCase):
    def upload(self, fmt, name='phone.jpeg', **options):
        stream = BytesIO()
        Image.new('RGB', (80, 60), 'red').save(stream, format=fmt, **options)
        return SimpleUploadedFile(name, stream.getvalue(), content_type='image/jpeg')

    def test_jpeg_and_multi_picture_jpeg_are_normalized(self):
        from .catalog_forms import ShopImageField
        for fmt, options in [('JPEG', {}), ('MPO', {'save_all': True, 'append_images': [Image.new('RGB', (80, 60), 'blue')]})]:
            with self.subTest(format=fmt):
                upload = self.upload(fmt, **options)
                self.assertEqual(Image.open(upload).format, fmt)
                upload.seek(0)
                normalized = ShopImageField().clean(upload)
                image = Image.open(normalized)
                self.assertEqual(image.format, 'WEBP')
                self.assertEqual(image.size, (80, 60))
                self.assertEqual(getattr(image, 'n_frames', 1), 1)
                self.assertGreater(image.getpixel((0, 0))[0], 200)
                self.assertEqual(upload.tell(), 0)

    def test_renaming_unsupported_image_does_not_bypass_validation(self):
        from django.core.exceptions import ValidationError
        from .catalog_forms import ShopImageField
        with self.assertRaisesMessage(ValidationError, 'Format détecté : GIF'):
            ShopImageField().clean(self.upload('GIF'))


    def test_48_megapixel_phone_photo_is_resized_and_orientation_preserved(self):
        from .catalog_forms import ShopImageField
        stream = BytesIO()
        exif = Image.Exif()
        exif[274] = 6
        with Image.new('RGB', (8000, 6000), 'green') as original:
            original.save(stream, format='JPEG', exif=exif)
        upload = SimpleUploadedFile('phone-48mp.jpeg', stream.getvalue(), content_type='image/jpeg')
        result = ShopImageField().clean(upload)
        with Image.open(result) as normalized:
            self.assertEqual(normalized.format, 'WEBP')
            self.assertEqual(normalized.size, (1800, 2400))
            self.assertFalse(normalized.getexif())

    def test_excessive_pixel_count_rejected_before_decoding(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from django.core.exceptions import ValidationError
        from .catalog_forms import ShopImageField
        upload = self.upload('JPEG')
        header = SimpleNamespace(format='JPEG', width=8001, height=8000)
        with patch('core.catalog_forms.Image.open', return_value=header):
            with self.assertRaisesMessage(ValidationError, '64 mégapixels'):
                ShopImageField().clean(upload)
        self.assertEqual(upload.tell(), 0)
