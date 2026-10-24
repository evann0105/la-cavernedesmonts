"""Prepared photos are validated together, before anything becomes public."""
from pathlib import Path
from unittest.mock import patch
from django.test import TestCase
from . import test_slideshow as fixtures
from .configuration import configuration, public_configuration
from .models import HeroSlide, HomepageConfiguration


class PreparedSlideshowTests(TestCase):
    setUp = fixtures.SlideshowTests.setUp
    data = fixtures.SlideshowTests.data
    add = fixtures.SlideshowTests.add

    def prepare(self, photos, **changes):
        data = self.data(**changes)
        start = int(data['slides-INITIAL_FORMS'])
        data['slides-TOTAL_FORMS'] = str(start + len(photos))
        for offset, fields in enumerate(photos):
            index = start + offset
            row = {'id': '', 'position': str(index + 1), 'alt': 'Randonnée en montagne', **fields}
            data.update({f'slides-{index}-{key}': value for key, value in row.items()})
        return data

    def test_prepared_descriptions_and_mobile_images_are_published_in_one_save(self):
        self.assertFalse(HeroSlide.objects.exists())
        data = self.prepare([
            {'image': fixtures.image('green'), 'mobile_image': fixtures.image('blue'), 'alt': 'Le sentier et ses fleurs'},
            {'image': fixtures.image('red'), 'alt': '<b>La boutique</b>'},
        ])
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        photos = list(HeroSlide.objects.order_by('position'))
        self.assertEqual([p.alt for p in photos], ['Le sentier et ses fleurs', '<b>La boutique</b>'])
        self.assertTrue(photos[0].mobile_image)
        home = public_configuration()
        self.assertEqual(len(home['hero_slides']), 3)
        self.assertEqual(home['hero_slides'][1]['mobile_image']['url'], photos[0].mobile_image.url)
        self.assertContains(self.client.get('/'), '&lt;b&gt;La boutique&lt;/b&gt;')

    def test_new_photo_can_be_placed_before_existing_photos(self):
        self.add()
        original = HeroSlide.objects.get()
        data = self.prepare([{'image': fixtures.image('blue'), 'alt': 'Nouvelle photo', 'position': '1'}])
        data['slides-0-position'] = '2'
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        photos = list(HeroSlide.objects.order_by('position'))
        self.assertEqual([p.alt for p in photos], ['Nouvelle photo', original.alt])
        self.assertEqual(photos[1].pk, original.pk)
        self.assertEqual([p.position for p in photos], [1, 2])

    def test_description_required_before_publication_and_no_partial_save(self):
        data = self.prepare([{'image': fixtures.image(), 'alt': ''}, {'image': fixtures.image('blue')}])
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['formset'].errors[0].get('alt'))
        self.assertFalse(HeroSlide.objects.exists()); self.assertFalse(HomepageConfiguration.objects.exists())
        self.assertFalse(list(self.media.rglob('*.webp')))
        self.assertContains(response, 'Resélectionnez')

    def test_invalid_new_row_preserves_existing_file_and_settings(self):
        self.add()
        original = HeroSlide.objects.get(); original_path = original.image.path
        data = self.prepare([{'alt': 'Photo sans fichier'}], interval='10')
        self.assertEqual(self.client.post(self.url, data).status_code, 200)
        original.refresh_from_db()
        self.assertEqual(original.image.path, original_path)
        self.assertTrue(Path(original_path).exists())
        self.assertEqual(HeroSlide.objects.count(), 1)
        self.assertEqual(configuration()['slideshow']['interval'], 6)

    def test_blank_extra_row_does_not_require_a_photo(self):
        data = self.prepare([{'position': '', 'alt': ''}])
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.assertFalse(HeroSlide.objects.exists())

    def test_removed_unpublished_photo_is_ignored_without_upload(self):
        data = self.prepare([{'position': '', 'alt': '', 'DELETE': 'on'}])
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.assertFalse(HeroSlide.objects.exists())

    def test_stale_prepared_upload_never_overwrites_the_latest_revision(self):
        stale = self.prepare([{'image': fixtures.image(), 'alt': 'Ancien onglet'}])
        self.add()
        self.assertEqual(self.client.post(self.url, stale).status_code, 409)
        self.assertEqual(HeroSlide.objects.count(), 1)
        self.assertNotEqual(HeroSlide.objects.get().alt, 'Ancien onglet')
        self.assertEqual(len(list(self.media.rglob('*.webp'))), 1)

    def test_saved_and_prepared_positions_must_be_distinct(self):
        self.add()
        data = self.prepare([{'image': fixtures.image(), 'position': '1'}])
        self.assertEqual(self.client.post(self.url, data).status_code, 200)
        self.assertEqual(HeroSlide.objects.count(), 1)
        self.assertEqual(configuration()['revision'], 1)

    def test_failed_prepared_save_rolls_back_and_removes_new_files(self):
        original_save = HeroSlide.save
        calls = []
        def fail_second(instance, *args, **kwargs):
            calls.append(instance)
            if len(calls) == 2: raise RuntimeError('Échec simulé du second fichier')
            return original_save(instance, *args, **kwargs)
        data = self.prepare([{'image': fixtures.image()}, {'image': fixtures.image('blue')}])
        with patch.object(HeroSlide, 'save', autospec=True, side_effect=fail_second):
            with self.assertRaises(RuntimeError): self.client.post(self.url, data)
        self.assertFalse(HeroSlide.objects.exists()); self.assertFalse(HomepageConfiguration.objects.exists())
        self.assertFalse(list(self.media.rglob('*.webp')))

    def test_preview_is_read_only_visitor_html_with_escaped_text(self):
        title = '</script><img src=x onerror=alert(1)>'
        HomepageConfiguration.objects.create(data={'values': {'hero': {'title_line1': title}}})
        before_revision = configuration()['revision']
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        preview = response.context['preview_html']
        self.assertNotIn('<script', preview.lower())
        self.assertNotIn('Déconnexion', preview)
        self.assertIn('inert', preview)
        self.assertIn('&lt;/script&gt;', preview)
        self.assertNotIn(title, preview)
        self.assertFalse(HeroSlide.objects.exists())
        self.assertEqual(configuration()['revision'], before_revision)
