from datetime import datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal
import json
from unittest.mock import patch

from django.test import TestCase

from core.models import Category, Product, ProductColor, ProductPromotion
from core.pricing import load_pricing_context
from core.seo_schema import build_product_schema
from stocks.models import Variant


class ProductSchemaTests(TestCase):
    def setUp(self):
        self.at = datetime(2026, 10, 20, 12, tzinfo=datetime_timezone.utc)
        category = Category.objects.create(name='Femmes', slug='femmes')
        with patch('django.utils.timezone.now', return_value=self.at - timedelta(days=40)):
            self.product = Product.objects.create(
                name='Polaire Peak Mountain', slug='polaire-peak-mountain', price='80.00',
                description='Polaire zippée.', category=category, brand='Peak Mountain',
                composition='100 % polyester', main_color='Rouge',
                main_image='products/polaire-rouge.jpg', availability='available')
            self.red = Variant.objects.create(product=self.product, size='M', color='Rouge', quantity=0)
            self.green = Variant.objects.create(product=self.product, size='L', color='Vert', quantity=3,
                                                reserved=1, price='100.00', reference='libellé modifiable')
        ProductColor.objects.create(product=self.product, name='Vert', image='products/colors/vert.jpg')
        self.url = 'https://boutique.example/produits/polaire-peak-mountain/'

    def schema(self, *, variants=None, product=None, **kwargs):
        product = product or self.product
        if variants is None:
            variants = list(product.stock_variants.all())
        load_pricing_context([product], at=kwargs.get('at', self.at))
        return build_product_schema(
            product, url=kwargs.pop('url', self.url),
            images=kwargs.pop('images', [product.image_url] if product.image_url else []),
            variants=variants, at=kwargs.pop('at', self.at),
            can_sell=kwargs.pop('can_sell', True), **kwargs)

    def by_color(self, schema):
        return {item['color']: item for item in schema['hasVariant']}

    def promotion(self):
        return ProductPromotion.objects.create(
            product=self.product, starts_at=self.at - timedelta(hours=1),
            ends_at=self.at + timedelta(hours=1), kind='percent', value=Decimal('10.00'),
            reference_confirmed=True,
            reference_prices={str(self.red.pk): '80.00', str(self.green.pk): '100.00'})

    def test_group_uses_distinct_real_variant_urls_and_stable_identifiers(self):
        schema = self.schema(url=self.url + '?variante=invalid&utm_source=test#gallery')
        self.assertEqual(schema['@type'], 'ProductGroup')
        self.assertEqual(schema['url'], self.url)
        self.assertEqual(schema['productGroupID'], f'LCDM-P{self.product.pk}')
        self.assertCountEqual(schema['variesBy'], ['https://schema.org/size', 'https://schema.org/color'])
        items = self.by_color(schema)
        for variant, color in ((self.red, 'Rouge'), (self.green, 'Vert')):
            item = items[color]
            self.assertEqual(item['url'], self.url + f'?variante={variant.pk}')
            self.assertEqual(item['offers']['url'], item['url'])
            self.assertEqual(item['sku'], f'LCDM-P{self.product.pk}-V{variant.pk}')
        self.assertNotEqual(items['Rouge']['sku'], items['Vert']['sku'])

    def test_exact_color_photos_and_primary_color_are_used(self):
        items = self.by_color(self.schema())
        self.assertEqual(items['Rouge']['image'], ['https://boutique.example/media/products/polaire-rouge.jpg'])
        self.assertEqual(items['Vert']['image'], ['https://boutique.example/media/products/colors/vert.jpg'])

    def test_unpictured_color_does_not_inherit_a_different_known_color_photo(self):
        blue = Variant.objects.create(product=self.product, color='Bleu', size='S', quantity=2)
        item = self.by_color(self.schema())['Bleu']
        self.assertNotIn('image', item)
        self.assertEqual(item['sku'], f'LCDM-P{self.product.pk}-V{blue.pk}')

    def test_offers_use_each_exact_price_and_reserved_stock(self):
        items = self.by_color(self.schema())
        self.assertEqual(items['Rouge']['offers']['price'], '80.00')
        self.assertEqual(items['Vert']['offers']['price'], '100.00')
        self.assertEqual(items['Rouge']['offers']['availability'], 'https://schema.org/OutOfStock')
        self.assertEqual(items['Vert']['offers']['availability'], 'https://schema.org/InStock')
        self.assertEqual(items['Vert']['offers']['priceCurrency'], 'EUR')
        self.green.reserved = 3
        self.green.save(update_fields=['reserved'])
        self.assertEqual(self.by_color(self.schema())['Vert']['offers']['availability'], 'https://schema.org/OutOfStock')

    def test_active_and_expired_prices_follow_same_clock_as_checkout(self):
        campaign = self.promotion()
        items = self.by_color(self.schema())
        self.assertEqual(items['Rouge']['offers']['price'], '72.00')
        self.assertEqual(items['Vert']['offers']['price'], '90.00')
        self.assertEqual(items['Vert']['offers']['validThrough'], campaign.ends_at.isoformat())
        expired = self.by_color(self.schema(at=campaign.ends_at))
        self.assertEqual(expired['Rouge']['offers']['price'], '80.00')
        self.assertEqual(expired['Vert']['offers']['price'], '100.00')
        self.assertNotIn('validThrough', expired['Vert']['offers'])

    def test_online_sales_disabled_does_not_announce_purchasable_offers(self):
        schema = self.schema(can_sell=False)
        self.assertTrue(schema['hasVariant'])
        self.assertTrue(all('offers' not in item for item in schema['hasVariant']))

    def test_unknown_count_omits_offer_instead_of_inventing_stock(self):
        self.green.quantity = None
        self.green.reserved = 0
        self.green.save(update_fields=['quantity', 'reserved'])
        items = self.by_color(self.schema())
        self.assertNotIn('offers', items['Vert'])
        self.assertEqual(items['Rouge']['offers']['availability'], 'https://schema.org/OutOfStock')

    def test_global_product_unavailable_overrides_positive_stock(self):
        self.product.availability = 'unavailable'
        items = self.by_color(self.schema())
        self.assertTrue(all(item['offers']['availability'] == 'https://schema.org/OutOfStock'
                            for item in items.values()))

    def test_no_manufacturer_ean_rating_or_shipping_is_fabricated(self):
        schema = self.schema()
        rendered = json.dumps(schema)
        for field in ('gtin', 'gtin13', 'mpn', 'manufacturer', 'aggregateRating', 'review',
                      'shippingDetails', 'hasMerchantReturnPolicy'):
            self.assertNotIn('"' + field + '"', rendered)
        self.assertEqual(schema['brand'], {'@type': 'Brand', 'name': 'Peak Mountain'})
        self.assertEqual(schema['material'], '100 % polyester')
        self.assertEqual(schema['category'], 'Femmes')

    def test_missing_or_unconfirmed_attributes_are_omitted(self):
        self.product.brand = 'À confirmer'
        self.product.composition = 'Non communiquée par le fabricant'
        schema = self.schema()
        self.assertNotIn('brand', schema)
        self.assertNotIn('material', schema)

    def test_single_variant_is_one_product_with_exact_price_color_size(self):
        schema = self.schema(variants=[self.green])
        self.assertEqual(schema['@type'], 'Product')
        self.assertEqual(schema['color'], 'Vert')
        self.assertEqual(schema['size'], 'L')
        self.assertEqual(schema['offers']['price'], '100.00')
        self.assertEqual(schema['url'], self.url + f'?variante={self.green.pk}')
        self.assertEqual(schema['brand']['name'], 'Peak Mountain')

    def test_legacy_product_cannot_announce_online_offer_without_counted_variant(self):
        schema = self.schema(variants=[])
        self.assertEqual(schema['@type'], 'Product')
        self.assertNotIn('offers', schema)
        self.product.availability = 'unknown'
        self.assertNotIn('offers', self.schema(variants=[]))
        self.product.availability = 'unavailable'
        self.assertNotIn('offers', self.schema(variants=[]))

    def test_no_images_does_not_create_placeholder_urls(self):
        product = Product.objects.create(name='Nouveau produit', price='15.00')
        schema = self.schema(product=product, images=[], variants=[], can_sell=False)
        self.assertNotIn('image', schema)
        self.assertNotIn('brand', schema)
        self.assertNotIn('offers', schema)

    def test_identifiers_do_not_depend_on_mutable_reference_or_color(self):
        before = self.schema(variants=[self.green])['sku']
        self.green.reference = 'autre référence'
        self.green.color = 'Kaki'
        after = self.schema(variants=[self.green])['sku']
        self.assertEqual(before, after)

    def test_gallery_image_urls_are_absolute_unique_and_safe_schemes(self):
        schema = self.schema(images=[
            {'url': '/media/detail.jpg', 'color': 'Vert'}, '/media/detail.jpg',
            'javascript:alert(1)', 'data:image/png;base64,test', '',
        ])
        self.assertEqual(schema['image'].count('https://boutique.example/media/detail.jpg'), 1)
        self.assertTrue(all(image.startswith('https://boutique.example/') for image in schema['image']))
        self.assertIn('https://boutique.example/media/detail.jpg', self.by_color(schema)['Vert']['image'])

    def test_preloaded_relations_and_prices_do_not_query_once_per_variant(self):
        product = Product.objects.select_related('category').prefetch_related(
            'stock_variants', 'color_photos', 'collections').get(pk=self.product.pk)
        variants = list(product.stock_variants.all())
        load_pricing_context([product], at=self.at)
        with self.assertNumQueries(0):
            schema = build_product_schema(product, url=self.url, images=[product.image_url],
                                          variants=variants, at=self.at, can_sell=True)
        self.assertEqual(len(schema['hasVariant']), 2)
