"""Small, in-memory regressions for both shop image entry points."""
from contextlib import nullcontext
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from PIL import Image

from core.catalog_forms import ShopImageField
from phototheque.services import PhotoError, _normalize


def encoded(image, image_format='PNG', **options):
    output = BytesIO()
    image.save(output, format=image_format, **options)
    return output.getvalue()


class ImageSafetyTests(SimpleTestCase):
    def direct(self, data):
        upload = SimpleUploadedFile('photo.jpeg', data, content_type='image/jpeg')
        result = ShopImageField().clean(upload)
        self.assertEqual(upload.tell(), 0)
        return result.read()

    def normalized(self, data):
        result, thumbnail, width, height = _normalize(data)
        with Image.open(BytesIO(thumbnail)) as preview:
            self.assertLessEqual(max(preview.size), 360)
        return result

    def assert_output(self, content, size):
        with Image.open(BytesIO(content)) as image:
            image.load()
            self.assertEqual(image.format, 'WEBP')
            self.assertEqual(image.size, size)
            self.assertEqual(getattr(image, 'n_frames', 1), 1)
            self.assertFalse(image.getexif())
            return image.copy()

    def test_all_supported_formats_and_mpo_keep_primary_picture(self):
        for image_format, options in (
            ('JPEG', {}),
            ('MPO', {'save_all': True, 'append_images': [Image.new('RGB', (12, 8), 'blue')]}),
            ('PNG', {}),
            ('WEBP', {}),
        ):
            data = encoded(Image.new('RGB', (12, 8), 'red'), image_format, **options)
            for normalize in (self.direct, self.normalized):
                with self.subTest(format=image_format, entry=normalize.__name__):
                    image = self.assert_output(normalize(data), (12, 8))
                    self.assertGreater(image.convert('RGB').getpixel((0, 0))[0], 200)

    def test_disallowed_parsers_are_not_called_even_for_a_renamed_image(self):
        Image.init()
        inputs = (
            ('GIF', encoded(Image.new('RGB', (2, 2)), 'GIF')),
            ('XBM', encoded(Image.new('1', (8, 8)), 'XBM')),
        )
        for image_format, data in inputs:
            parser, accept = Image.OPEN[image_format]
            forbidden_parser = Mock(side_effect=AssertionError('Disallowed decoder was called'))
            with self.subTest(format=image_format), patch.dict(Image.OPEN, {image_format: (forbidden_parser, accept)}):
                with self.assertRaises(ValidationError):
                    self.direct(data)
                with self.assertRaises(PhotoError):
                    self.normalized(data)
                forbidden_parser.assert_not_called()

    def test_palette_and_rgb_transparency_survive_both_entry_points(self):
        palette = Image.new('P', (2, 1), 0)
        palette.putpalette([0, 0, 0, 255, 0, 0] + [0, 0, 0] * 254)
        palette.putpixel((1, 0), 1)
        rgb = Image.new('RGB', (2, 1), (255, 0, 255))
        rgb.putpixel((1, 0), (255, 0, 0))
        luma = Image.new('L', (2, 1), 0)
        luma.putpixel((1, 0), 255)
        for image, transparent in ((palette, 0), (rgb, (255, 0, 255)), (luma, 0)):
            data = encoded(image, transparency=transparent)
            for normalize in (self.direct, self.normalized):
                with self.subTest(mode=image.mode, entry=normalize.__name__):
                    result = self.assert_output(normalize(data), (2, 1)).convert('RGBA')
                    self.assertEqual(result.getpixel((0, 0))[3], 0)
                    self.assertEqual(result.getpixel((1, 0))[3], 255)

    def test_exif_orientation_is_applied_and_metadata_is_removed(self):
        exif = Image.Exif()
        exif[274] = 6
        data = encoded(Image.new('RGB', (12, 8), 'red'), 'JPEG', exif=exif)
        for normalize in (self.direct, self.normalized):
            with self.subTest(entry=normalize.__name__):
                self.assert_output(normalize(data), (8, 12))

    def test_animated_png_becomes_a_single_primary_picture(self):
        data = encoded(Image.new('RGBA', (12, 8), 'red'), save_all=True,
                       append_images=[Image.new('RGBA', (12, 8), 'blue')], duration=100, loop=0)
        for normalize in (self.direct, self.normalized):
            with self.subTest(entry=normalize.__name__):
                image = self.assert_output(normalize(data), (12, 8))
                self.assertGreater(image.convert('RGB').getpixel((0, 0))[0], 200)

    def test_library_pixel_limit_precedes_draft_and_pixel_decoding(self):
        header = SimpleNamespace(format='PNG', width=8001, height=8000,
                                 load=Mock(), draft=Mock(), seek=Mock())
        with patch('phototheque.services.Image.open', return_value=nullcontext(header)):
            with self.assertRaisesMessage(PhotoError, '64 mégapixels'):
                _normalize(b'small synthetic header')
        header.load.assert_not_called()
        header.draft.assert_not_called()
        header.seek.assert_not_called()
