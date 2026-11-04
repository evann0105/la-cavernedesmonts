from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django import forms
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from connexion.test_helpers import verify_test_user
from core.catalog_forms import MultiPhotoField, ProductColorForm, ShopImageField
from core.models import Category, Product
from .models import Photo
from .services import archive_photo, is_library_photo_file, resolve_photo, upload_photos
from .widgets import LibraryClearableFileInput, LibraryFileInput


def upload(color='red'):
    stream = BytesIO()
    Image.new('RGB', (72, 48), color).save(stream, 'PNG')
    return SimpleUploadedFile('photo.png', stream.getvalue(), content_type='image/png')


class SingleForm(forms.Form):
    title = forms.CharField()
    image = ShopImageField()


class OptionalForm(forms.Form):
    image = ShopImageField(required=False)


class MultipleForm(forms.Form):
    image = MultiPhotoField(required=False)


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'], EMAIL_DELIVERY_ENABLED=False)
class PhotoLibraryFormTests(TestCase):
    def setUp(self):
        directory = TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.media = Path(directory.name)
        setting = override_settings(MEDIA_ROOT=directory.name)
        setting.enable(); self.addCleanup(setting.disable)
        self.actor = get_user_model().objects.create_user('photo-library-manager', is_staff=True, is_superuser=True)
        verify_test_user(self.actor)
        self.client.force_login(self.actor, backend='connexion.backends.EmailOrUsernameBackend')
        self.photos = [photo for photo, created in upload_photos([upload('red'), upload('green'), upload('blue')], actor=self.actor)]

    def choice(self, *photos):
        return json.dumps([str(photo.pk) for photo in photos])

    def test_required_choice_is_file_and_second_validation_keeps_exact_bytes(self):
        before = list(Photo.objects.values())
        form = SingleForm({'title': 'Mon texte', 'image-library': self.choice(self.photos[0])})
        self.assertTrue(form.is_valid(), form.errors)
        selected = form.cleaned_data['image']
        self.assertTrue(is_library_photo_file(selected))
        self.assertIs(ShopImageField().clean(selected), selected)
        self.assertEqual(sha256(selected.read()).hexdigest(), self.photos[0].digest)
        self.assertEqual(list(Photo.objects.values()), before)
        second = resolve_photo(self.photos[0].pk)
        self.assertIsNot(second, selected)
        self.assertEqual(second.tell(), 0)

    def test_invalid_form_keeps_selection_and_text_without_library_mutation(self):
        before = list(Photo.objects.values())
        form = SingleForm({'title': '', 'image-library': self.choice(self.photos[0])})
        self.assertFalse(form.is_valid())
        html = form.as_p()
        self.assertIn('name="image-library"', html)
        self.assertIn('data-library-id="' + str(self.photos[0].pk) + '"', html)
        self.assertIn(self.photos[0].title, html)
        self.assertIn('Choisir dans la bibliothèque', html)
        self.assertEqual(list(Photo.objects.values()), before)

    def test_invalid_library_references_are_never_interpreted_as_paths(self):
        for raw in ('"/etc/passwd"', '["../../secret"]', '["https://example.test/a.webp"]',
                    '[true]', '{"id":1}', '[0]', '[-1]', '[9999999999999999999999999]', '[999999999]'):
            with self.subTest(raw=raw):
                form = SingleForm({'title': 'Conservé', 'image-library': raw})
                self.assertFalse(form.is_valid())
                self.assertIn('image', form.errors)
                self.assertEqual(form['title'].value(), 'Conservé')

    def test_archived_or_corrupted_photo_is_rejected_at_validation(self):
        archive_photo(self.photos[0].pk, actor=self.actor)
        form = SingleForm({'title': 'Test', 'image-library': self.choice(self.photos[0])})
        self.assertFalse(form.is_valid())
        self.assertIn('archivée', str(form.errors))
        (self.media / self.photos[1].image.name).write_bytes(b'corrompu')
        form = SingleForm({'title': 'Test', 'image-library': self.choice(self.photos[1])})
        self.assertFalse(form.is_valid())
        self.assertIn('image', form.errors)

    def test_clear_upload_and_library_contradictions_do_not_clear_existing_picture(self):
        for files in ({}, {'image': upload()}):
            form = OptionalForm({'image-clear': 'on', 'image-library': self.choice(self.photos[0])}, files)
            self.assertFalse(form.is_valid())
            self.assertIn('contradiction', form.errors.as_data()['image'][0].code)
        form = SingleForm({'title': 'Test', 'image-library': self.choice(self.photos[0])}, {'image': upload()})
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors.as_data()['image'][0].code, 'conflicting_photo_sources')
        form = OptionalForm({'image-clear': 'on'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIs(form.cleaned_data['image'], False)

    def test_multiple_choices_and_uploads_combine_with_total_limit(self):
        form = MultipleForm({'image-library': self.choice(*self.photos)}, {'image': [upload('orange')]})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(len(form.cleaned_data['image']), 4)
        form = MultipleForm({'image-library': self.choice(*self.photos)}, {'image': [upload() for _ in range(10)]})
        self.assertFalse(form.is_valid())
        self.assertIn('12', str(form.errors))
        form = MultipleForm({'image-clear': 'on', 'image-library': self.choice(self.photos[0])})
        self.assertFalse(form.is_valid())

    def test_required_color_prefixed_field_and_empty_form_keep_correct_companion(self):
        form = ProductColorForm({'colors-3-name': 'Rouge', 'colors-3-image-library': self.choice(self.photos[0])}, prefix='colors-3')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(is_library_photo_file(form.cleaned_data['image']))
        self.assertIn('name="colors-3-image-library"', str(form['image']))
        blank = ProductColorForm(prefix='colors-__prefix__')
        self.assertIn('name="colors-__prefix__-image-library"', str(blank['image']))
        self.assertIsInstance(form.fields['image'].widget, LibraryClearableFileInput)
        self.assertIsInstance(ShopImageField(widget=forms.FileInput).widget, LibraryFileInput)

    def test_slideshow_limit_includes_library_selections(self):
        from accueil.slideshow import SlidesForm
        data = {'revision': 0, 'autoplay': 'on', 'interval': 6, 'new_photos-library': self.choice(*self.photos)}
        form = SlidesForm(data, {'new_photos': [upload(), upload(), upload()]})
        self.assertFalse(form.is_valid())
        self.assertIn('5', str(form.errors))
        self.assertEqual(form.fields['new_photos'].widget.library_limit, 5)

    def test_product_post_without_uploads_saves_main_gallery_and_color_independently(self):
        category = Category.objects.create(name='Femmes', slug='photo-femmes')
        data = {'name': 'Produit bibliothèque', 'price': '40', 'description': 'Description conservée',
            'category': category.pk, 'is_published': 'on', 'main_image-library': self.choice(self.photos[0]),
            'new_photos-library': self.choice(self.photos[1], self.photos[2]),
            'photos-TOTAL_FORMS': 0, 'photos-INITIAL_FORMS': 0, 'photos-MIN_NUM_FORMS': 0, 'photos-MAX_NUM_FORMS': 50,
            'stock-TOTAL_FORMS': 0, 'stock-INITIAL_FORMS': 0, 'stock-MIN_NUM_FORMS': 0, 'stock-MAX_NUM_FORMS': 50,
            'colors-TOTAL_FORMS': 1, 'colors-INITIAL_FORMS': 0, 'colors-MIN_NUM_FORMS': 0, 'colors-MAX_NUM_FORMS': 30,
            'colors-0-name': 'Rouge', 'colors-0-image-library': self.choice(self.photos[0])}
        response = self.client.post(reverse('core:catalog_add'), data)
        self.assertEqual(response.status_code, 302, getattr(response, 'context', None))
        product = Product.objects.get(name='Produit bibliothèque')
        self.assertEqual(product.images.count(), 2)
        self.assertEqual(product.color_photos.count(), 1)
        image_path = self.media / product.main_image.name
        self.assertNotEqual(product.main_image.name, self.photos[0].image.name)
        self.assertEqual(sha256(image_path.read_bytes()).hexdigest(), self.photos[0].digest)
        archive_photo(self.photos[0].pk, actor=self.actor)
        self.assertTrue(image_path.is_file())
        self.assertEqual(sha256(image_path.read_bytes()).hexdigest(), self.photos[0].digest)

    def test_homepage_section_post_saves_library_copy_after_second_validation(self):
        from accueil.configuration import configuration
        from accueil.models import HomepageConfiguration
        from accueil.schema import SECTIONS
        data = {name: spec['default'] for name, spec in SECTIONS['hero']['fields'].items()}
        data.update({'revision': configuration()['revision'], 'hero_image-library': self.choice(self.photos[0]),
                     'hero_mobile_image-library': self.choice(self.photos[1])})
        response = self.client.post(reverse('homepage:section', kwargs={'section': 'hero'}), data)
        self.assertEqual(response.status_code, 302)
        saved = HomepageConfiguration.objects.get()
        self.assertEqual(sha256((self.media / saved.hero_image.name).read_bytes()).hexdigest(), self.photos[0].digest)
        self.assertEqual(sha256((self.media / saved.hero_mobile_image.name).read_bytes()).hexdigest(), self.photos[1].digest)

    def test_page_post_saves_library_copy_and_keeps_own_alt_text(self):
        from boutique.editorial_configuration import page_content
        from boutique.models import ContentPage
        current = page_content('about')
        data = dict(current['values'], revision=current['revision'], action='publish')
        data.update({'image-library': self.choice(self.photos[0]), 'image_alt': 'Notre magasin en hiver'})
        response = self.client.post(reverse('editorial:page_edit', kwargs={'key': 'about'}), data)
        self.assertEqual(response.status_code, 302)
        page = ContentPage.objects.get(key='about')
        self.assertEqual(page.data['image_alt'], 'Notre magasin en hiver')
        self.assertEqual(sha256((self.media / page.image.name).read_bytes()).hexdigest(), self.photos[0].digest)

    def test_slideshow_formset_post_accepts_required_desktop_and_mobile_choices(self):
        from accueil.configuration import configuration
        from accueil.models import HeroSlide
        response = self.client.post(reverse('homepage:slides'), {
            'revision': configuration()['revision'], 'autoplay': 'on', 'interval': 6,
            'slides-TOTAL_FORMS': 1, 'slides-INITIAL_FORMS': 0, 'slides-MIN_NUM_FORMS': 0, 'slides-MAX_NUM_FORMS': 5,
            'slides-0-position': 1, 'slides-0-alt': 'Promenade en montagne',
            'slides-0-image-library': self.choice(self.photos[0]),
            'slides-0-mobile_image-library': self.choice(self.photos[1])})
        self.assertEqual(response.status_code, 302)
        slide = HeroSlide.objects.get()
        self.assertEqual(sha256((self.media / slide.image.name).read_bytes()).hexdigest(), self.photos[0].digest)
        self.assertEqual(sha256((self.media / slide.mobile_image.name).read_bytes()).hexdigest(), self.photos[1].digest)

    def test_library_choice_html_escapes_photo_titles(self):
        photo = self.photos[0]
        photo.title = '<script>alert(1)</script>'
        photo.save(update_fields=['title'])
        form = SingleForm({'image-library': self.choice(photo)})
        self.assertFalse(form.is_valid())
        html = form.as_p()
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>alert(1)</script>', html)
