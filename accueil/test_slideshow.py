"""Exercise photo management and public fallbacks without changing live content."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from PIL import Image
from connexion.test_helpers import verify_test_user
from .configuration import configuration, public_configuration
from .models import HeroSlide, HomepageConfiguration
from .schema import SECTIONS


def image(color='red'):
    data = BytesIO()
    Image.new('RGB', (180, 120), color).save(data, 'PNG')
    return SimpleUploadedFile('accueil.png', data.getvalue(), content_type='image/png')


@override_settings(LOCAL_SKIP_EMAIL_VERIFICATION=False, EMAIL_DELIVERY_ENABLED=False)
class SlideshowTests(TestCase):
    def setUp(self):
        directory = TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.media = Path(directory.name)
        setting = override_settings(MEDIA_ROOT=directory.name)
        setting.enable(); self.addCleanup(setting.disable)
        self.manager = get_user_model().objects.create_user('slides-manager',
            email='slides@example.test', is_staff=True, is_superuser=True)
        verify_test_user(self.manager)
        self.client.force_login(self.manager, backend='connexion.backends.EmailOrUsernameBackend')
        self.url = reverse('homepage:slides')

    def data(self, **changes):
        rows = list(HeroSlide.objects.order_by('position', 'pk'))
        data = {'revision': configuration()['revision'], 'autoplay': 'on', 'interval': '6',
                'slides-TOTAL_FORMS': str(len(rows)), 'slides-INITIAL_FORMS': str(len(rows)),
                'slides-MIN_NUM_FORMS': '0', 'slides-MAX_NUM_FORMS': '5'}
        for index, row in enumerate(rows):
            data.update({f'slides-{index}-id': str(row.pk), f'slides-{index}-position': str(row.position),
                         f'slides-{index}-alt': row.alt})
        data.update(changes)
        return data

    def add(self, count=1):
        return self.client.post(self.url, self.data(new_photos=[image('red' if i % 2 else 'green') for i in range(count)]))

    def test_read_only_default_has_one_photo_and_no_slider_controls(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)
        home = public_configuration()
        self.assertEqual(len(home['hero_slides']), 1)
        self.assertFalse(home['slideshow']['enabled'])
        self.assertFalse(HomepageConfiguration.objects.exists())
        self.assertFalse(HeroSlide.objects.exists())
        self.assertContains(self.client.get('/'), 'montagne-mobile-600.webp')

    def test_multiple_uploads_are_optimized_and_add_to_public_photos(self):
        self.assertEqual(self.add(2).status_code, 302)
        self.assertEqual(HeroSlide.objects.count(), 2)
        photos = public_configuration()['hero_slides']
        self.assertEqual(len(photos), 3)
        self.assertTrue(photos[0]['primary']); self.assertTrue(public_configuration()['slideshow']['enabled'])
        for row in HeroSlide.objects.all():
            self.assertTrue(row.image.name.startswith('homepage/'))
            with Image.open(row.image.path) as uploaded:
                self.assertEqual(uploaded.format, 'WEBP'); self.assertEqual(uploaded.size, (180, 120))
            self.assertContains(self.client.get('/'), row.image.url)

    def test_extra_mobile_crop_is_not_another_slide(self):
        self.add()
        row = HeroSlide.objects.get()
        data = self.data(); data['slides-0-mobile_image'] = image('blue')
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        row.refresh_from_db()
        photos = public_configuration()['hero_slides']
        self.assertEqual(len(photos), 2); self.assertEqual(photos[1]['mobile_image']['url'], row.mobile_image.url)
        self.assertContains(self.client.get('/'), row.mobile_image.url)
        data = self.data(); data['slides-0-mobile_image-clear'] = 'on'
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        row.refresh_from_db(); self.assertFalse(row.mobile_image)

    def test_order_description_and_playback_settings_are_saved(self):
        self.add(2)
        rows = list(HeroSlide.objects.order_by('position'))
        data = self.data(interval='10'); data.pop('autoplay')
        data.update({'slides-0-position': '2', 'slides-1-position': '1', 'slides-0-alt': '<b>Forêt</b>'})
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        home = public_configuration()
        self.assertEqual([p['image']['url'] for p in home['hero_slides'][1:]], [rows[1].image.url, rows[0].image.url])
        self.assertFalse(home['slideshow']['autoplay']); self.assertEqual(home['slideshow']['interval'], 10)
        self.assertContains(self.client.get('/'), '&lt;b&gt;Forêt&lt;/b&gt;')

    def test_deleting_last_extra_returns_to_static_hero_and_retains_old_file(self):
        self.add()
        row = HeroSlide.objects.get(); old_path = Path(row.image.path)
        data = self.data(); data['slides-0-DELETE'] = 'on'
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.assertFalse(HeroSlide.objects.exists()); self.assertFalse(public_configuration()['slideshow']['enabled'])
        self.assertTrue(old_path.exists())

    def test_invalid_photo_limit_and_invalid_speed_do_not_partially_save(self):
        svg = SimpleUploadedFile('photo.svg', b'<svg/>', content_type='image/svg+xml')
        for changes in ({'new_photos': [svg]}, {'new_photos': [image() for _ in range(6)]}, {'interval': '1'}):
            with self.subTest(changes=list(changes)):
                response = self.client.post(self.url, self.data(**changes))
                self.assertEqual(response.status_code, 200)
                self.assertFalse(HeroSlide.objects.exists()); self.assertFalse(HomepageConfiguration.objects.exists())
                self.assertFalse(list(self.media.rglob('*.webp')))

    def test_stale_upload_and_omitted_rows_never_replace_current_slides(self):
        stale = self.data(new_photos=[image()])
        self.add()
        current = list(HeroSlide.objects.values_list('pk', flat=True))
        self.assertEqual(self.client.post(self.url, stale).status_code, 409)
        omitted = self.data(); omitted.update({'slides-TOTAL_FORMS': '0', 'slides-INITIAL_FORMS': '0'})
        self.assertEqual(self.client.post(self.url, omitted).status_code, 200)
        self.assertEqual(list(HeroSlide.objects.values_list('pk', flat=True)), current)
        self.assertEqual(len(list(self.media.rglob('*.webp'))), 1)

    def test_duplicate_position_is_rejected_without_losing_photos(self):
        self.add(2)
        data = self.data(); data['slides-1-position'] = data['slides-0-position']
        self.assertEqual(self.client.post(self.url, data).status_code, 200)
        self.assertEqual(HeroSlide.objects.count(), 2)
        self.assertEqual(configuration()['revision'], 1)

    def test_permissions_and_csrf(self):
        self.client.logout(); self.assertEqual(self.client.get(self.url).status_code, 302)
        customer = get_user_model().objects.create_user('slides-customer', email='client@example.test')
        verify_test_user(customer)
        self.client.force_login(customer, backend='connexion.backends.EmailOrUsernameBackend')
        self.assertEqual(self.client.get(self.url).status_code, 403)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.manager, backend='connexion.backends.EmailOrUsernameBackend')
        self.assertEqual(strict.post(self.url, self.data()).status_code, 403)
        self.assertFalse(HeroSlide.objects.exists())

    def test_main_mobile_crop_alone_keeps_one_photo(self):
        values = {name: spec['default'] for name, spec in SECTIONS['hero']['fields'].items()}
        values.update({'revision': 0, 'hero_image': image(), 'hero_mobile_image': image('blue')})
        self.assertEqual(self.client.post(reverse('homepage:section', kwargs={'section':'hero'}), values).status_code, 302)
        self.assertEqual(len(public_configuration()['hero_slides']), 1)
        self.assertFalse(public_configuration()['slideshow']['enabled'])

    def test_failed_second_file_save_rolls_back_rows_revision_and_new_files(self):
        original_save = HeroSlide.save
        calls = []
        def fail_second(slide, *args, **kwargs):
            calls.append(slide)
            if len(calls) == 2:
                raise RuntimeError('Échec simulé pendant le second enregistrement')
            return original_save(slide, *args, **kwargs)
        with patch.object(HeroSlide, 'save', autospec=True, side_effect=fail_second):
            with self.assertRaises(RuntimeError):
                self.add(2)
        self.assertFalse(HeroSlide.objects.exists())
        self.assertFalse(HomepageConfiguration.objects.exists())
        self.assertFalse(list(self.media.rglob('*.webp')))
