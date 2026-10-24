"""The grand-air carousel owns its photos and settings independently of the hero."""
from html.parser import HTMLParser
from pathlib import Path

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from . import test_slideshow as fixtures
from .configuration import configuration, public_configuration
from .models import HeroSlide, HomepageConfiguration
from .schema import OPTIONAL_SECTIONS, SECTIONS


class SlideshowMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.roots = []
        self.ids = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'data-home-slideshow' in attrs:
            self.roots.append(attrs)
        if 'id' in attrs:
            self.ids.append(attrs['id'])


@override_settings(LOCAL_SKIP_EMAIL_VERIFICATION=False, EMAIL_DELIVERY_ENABLED=False)
class FeatureSlideshowTests(TestCase):
    setUp = fixtures.SlideshowTests.setUp

    def route(self, section='feature'):
        return reverse('homepage:feature_slides' if section == 'feature' else 'homepage:slides')

    def data(self, section='feature', **changes):
        rows = list(HeroSlide.objects.filter(section=section).order_by('position', 'pk'))
        data = {'revision': configuration()['revision'], 'autoplay': 'on', 'interval': '6',
                'slides-TOTAL_FORMS': str(len(rows)), 'slides-INITIAL_FORMS': str(len(rows)),
                'slides-MIN_NUM_FORMS': '0', 'slides-MAX_NUM_FORMS': '5'}
        for index, row in enumerate(rows):
            data.update({f'slides-{index}-id': str(row.pk),
                         f'slides-{index}-position': str(row.position),
                         f'slides-{index}-alt': row.alt})
        data.update(changes)
        return data

    def prepare(self, photos, section='feature', **changes):
        data = self.data(section, **changes)
        start = int(data['slides-INITIAL_FORMS'])
        data['slides-TOTAL_FORMS'] = str(start + len(photos))
        for offset, fields in enumerate(photos):
            index = start + offset
            row = {'id': '', 'position': str(index + 1), 'alt': 'Le grand air', **fields}
            data.update({f'slides-{index}-{name}': value for name, value in row.items()})
        return data

    def add(self, section='feature', **changes):
        return self.client.post(self.route(section), self.prepare([
            {'image': fixtures.image('green'), 'alt': section + ' · photo supplémentaire'}
        ], section=section, **changes))

    def feature_markup(self, response):
        html = response.content.decode()
        image_id = html.index('id="home-feature"')
        return html[html.rfind('<section', 0, image_id):html.index('</section>', image_id) + 10]

    def test_default_feature_is_read_only_and_remains_a_single_photo(self):
        self.assertEqual(self.client.get(self.route()).status_code, 200)
        home = public_configuration()
        self.assertEqual(len(home['feature_slides']), 1)
        self.assertTrue(home['feature_slides'][0]['primary'])
        self.assertFalse(home['feature_slideshow']['enabled'])
        self.assertFalse(HomepageConfiguration.objects.exists())
        self.assertFalse(HeroSlide.objects.exists())
        self.assertNotIn('data-home-slideshow', self.feature_markup(self.client.get('/')))

    def test_prepared_feature_photo_description_and_mobile_crop_publish_together(self):
        payload = self.prepare([{'image': fixtures.image('green'),
            'mobile_image': fixtures.image('blue'), 'alt': '<b>La montagne et ses sentiers</b>'}])
        self.assertEqual(self.client.post(self.route(), payload).status_code, 302)
        row = HeroSlide.objects.get(section='feature')
        self.assertEqual(row.alt, '<b>La montagne et ses sentiers</b>')
        for file in (row.image, row.mobile_image):
            with fixtures.Image.open(file.path) as stored:
                self.assertEqual(stored.format, 'WEBP')
        home = public_configuration()
        self.assertEqual(len(home['feature_slides']), 2)
        self.assertEqual(home['feature_slides'][1]['image']['url'], row.image.url)
        self.assertEqual(home['feature_slides'][1]['mobile_image']['url'], row.mobile_image.url)
        self.assertTrue(home['feature_slideshow']['enabled'])
        self.assertEqual(len(home['hero_slides']), 1)
        self.assertFalse(home['slideshow']['enabled'])
        response = self.client.get('/')
        self.assertContains(response, row.image.url)
        self.assertContains(response, row.mobile_image.url)
        self.assertContains(response, '&lt;b&gt;La montagne et ses sentiers&lt;/b&gt;')
        self.assertContains(response, 'id="feature-slideshow-photos"')

    def test_both_carousels_keep_their_photos_and_playback_settings(self):
        self.assertEqual(self.add('hero', interval='10').status_code, 302)
        feature_data = self.prepare([{'image': fixtures.image('red'), 'alt': 'Grand air'}], interval='5')
        feature_data.pop('autoplay')
        self.assertEqual(self.client.post(self.route(), feature_data).status_code, 302)
        hero = HeroSlide.objects.get(section='hero')
        feature = HeroSlide.objects.get(section='feature')
        home = public_configuration()
        self.assertTrue(home['slideshow']['autoplay'])
        self.assertEqual(home['slideshow']['interval'], 10)
        self.assertFalse(home['feature_slideshow']['autoplay'])
        self.assertEqual(home['feature_slideshow']['interval'], 5)

        hero_data = self.data('hero', interval='8')
        hero_data.pop('autoplay')
        self.assertEqual(self.client.post(self.route('hero'), hero_data).status_code, 302)
        home = public_configuration()
        self.assertEqual(home['feature_slideshow']['interval'], 5)
        self.assertFalse(home['feature_slideshow']['autoplay'])
        self.assertEqual(self.client.post(self.route(), self.data(interval='6')).status_code, 302)
        home = public_configuration()
        self.assertFalse(home['slideshow']['autoplay'])
        self.assertEqual(home['slideshow']['interval'], 8)
        self.assertTrue(home['feature_slideshow']['autoplay'])
        self.assertEqual(home['hero_slides'][1]['image']['url'], hero.image.url)
        self.assertEqual(home['feature_slides'][1]['image']['url'], feature.image.url)
        markup = SlideshowMarkup(self.client.get('/').content.decode())
        self.assertEqual(len(markup.roots), 2)
        self.assertEqual(len(markup.ids), len(set(markup.ids)))
        self.assertIn('hero-slideshow-photos', markup.ids)
        self.assertIn('feature-slideshow-photos', markup.ids)

    def test_ids_from_the_other_carousel_are_rejected_before_new_files_are_saved(self):
        self.add('hero'); self.add('feature')
        before = list(HeroSlide.objects.order_by('pk').values(
            'pk', 'section', 'image', 'mobile_image', 'alt', 'position'))
        files = set(self.media.rglob('*.webp'))
        revision = configuration()['revision']
        for destination, source in (('feature', 'hero'), ('hero', 'feature')):
            with self.subTest(destination=destination):
                foreign = HeroSlide.objects.get(section=source)
                data = self.prepare([{'image': fixtures.image('blue')}], section=destination)
                data['slides-0-id'] = str(foreign.pk)
                self.assertEqual(self.client.post(self.route(destination), data).status_code, 200)
                self.assertEqual(configuration()['revision'], revision)
                self.assertEqual(list(HeroSlide.objects.order_by('pk').values(
                    'pk', 'section', 'image', 'mobile_image', 'alt', 'position')), before)
                self.assertEqual(set(self.media.rglob('*.webp')), files)

    def test_stale_feature_upload_cannot_overwrite_either_carousel(self):
        stale = self.prepare([{'image': fixtures.image('red'), 'alt': 'Ancien onglet'}])
        self.assertEqual(self.add('hero').status_code, 302)
        self.assertEqual(self.client.post(self.route(), stale).status_code, 409)
        self.assertFalse(HeroSlide.objects.filter(section='feature').exists())
        self.assertEqual(HeroSlide.objects.filter(section='hero').count(), 1)
        self.assertEqual(len(list(self.media.rglob('*.webp'))), 1)

    def test_replacing_the_feature_primary_keeps_both_carousels_and_the_hero_primary(self):
        hero_values = {name: spec['default'] for name, spec in SECTIONS['hero']['fields'].items()}
        hero_values.update({'revision': configuration()['revision'], 'hero_image': fixtures.image('red')})
        self.assertEqual(self.client.post(reverse('homepage:section', kwargs={'section': 'hero'}), hero_values).status_code, 302)
        self.add('hero'); self.add('feature')
        before_rows = list(HeroSlide.objects.order_by('pk').values('pk', 'section', 'image', 'alt'))
        hero_before = configuration()['images']['hero_image']
        values = {name: spec['default'] for name, spec in SECTIONS['feature']['fields'].items()}
        values.update({'revision': configuration()['revision'], 'feature_image': fixtures.image('blue'),
                       'image_alt': 'Une autre vue de montagne'})
        self.assertEqual(self.client.post(reverse('homepage:section', kwargs={'section': 'feature'}), values).status_code, 302)
        home = public_configuration()
        self.assertTrue(home['feature_slides'][0]['image']['custom'])
        self.assertEqual(home['feature_slides'][0]['alt'], 'Une autre vue de montagne')
        self.assertTrue(home['images']['hero_image']['custom'])
        self.assertEqual(home['images']['hero_image'], hero_before)
        self.assertEqual(list(HeroSlide.objects.order_by('pk').values('pk', 'section', 'image', 'alt')), before_rows)
        self.assertTrue(home['feature_slideshow']['enabled'])
        self.assertTrue(home['slideshow']['enabled'])

    def test_removing_the_last_feature_extra_restores_its_static_photo_only(self):
        self.add('hero'); self.add('feature')
        feature = HeroSlide.objects.get(section='feature')
        previous_file = Path(feature.image.path)
        hero = HeroSlide.objects.get(section='hero')
        data = self.data(); data['slides-0-DELETE'] = 'on'
        self.assertEqual(self.client.post(self.route(), data).status_code, 302)
        home = public_configuration()
        self.assertFalse(home['feature_slideshow']['enabled'])
        self.assertEqual(len(home['feature_slides']), 1)
        self.assertTrue(home['slideshow']['enabled'])
        self.assertEqual(home['hero_slides'][1]['image']['url'], hero.image.url)
        self.assertTrue(previous_file.exists())
        response = self.client.get('/')
        feature_html = self.feature_markup(response)
        self.assertNotIn('data-home-slideshow', feature_html)
        self.assertNotIn('feature-slideshow-photos', feature_html)
        self.assertIn(home['feature_slides'][0]['image']['url'], feature_html)
        self.assertEqual(len(SlideshowMarkup(response.content.decode()).roots), 1)

    def test_a_hidden_feature_does_not_load_its_carousel_or_its_only_assets(self):
        self.add('feature')
        feature = HeroSlide.objects.get(section='feature')
        visibility = {'revision': configuration()['revision'], **{
            name: 'on' for name in OPTIONAL_SECTIONS if name != 'feature'}}
        self.assertEqual(self.client.post(reverse('homepage:sections'), visibility).status_code, 302)
        self.assertFalse(public_configuration()['feature_slideshow']['enabled'])
        response = self.client.get('/')
        self.assertNotContains(response, 'id="home-feature"')
        self.assertNotContains(response, feature.image.url)
        self.assertNotContains(response, 'accueil/css/slideshow.css')
        self.assertNotContains(response, 'accueil/js/slideshow.js')
        self.assertTrue(HeroSlide.objects.filter(pk=feature.pk).exists())
        self.add('hero')
        response = self.client.get('/')
        self.assertContains(response, 'accueil/css/slideshow.css')
        self.assertContains(response, 'accueil/js/slideshow.js')
        self.assertEqual(len(SlideshowMarkup(response.content.decode()).roots), 1)

    def test_feature_preview_is_read_only_visitor_html_and_keeps_authentication_private(self):
        title = '</script><img src=x onerror=alert(1)>'
        HomepageConfiguration.objects.create(data={'values': {'feature': {'title_line1': title}}})
        before = configuration()['revision']
        response = self.client.get(self.route())
        self.assertEqual(response.status_code, 200)
        preview = response.context['preview_html']
        self.assertIn('home-feature', preview)
        self.assertIn('inert', preview)
        self.assertIn('&lt;/script&gt;', preview)
        self.assertNotIn(title, preview)
        self.assertNotIn('<script', preview.lower())
        self.assertNotIn('Déconnexion', preview)
        self.assertNotIn(self.manager.username, preview)
        self.assertIn('private', response['Cache-Control'])
        self.assertEqual(configuration()['revision'], before)
        self.assertFalse(HeroSlide.objects.exists())
        self.assertEqual(Client().get(self.route()).status_code, 302)
