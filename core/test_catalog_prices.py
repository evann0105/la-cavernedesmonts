from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase, TestCase

from core.models import Category, Product
from core.pricing import product_price_summary, with_catalog_prices
from stocks.models import Variant


class PriceSummaryTests(SimpleTestCase):
    def product(self, price='40.00', **attributes):
        return SimpleNamespace(price=Decimal(price), pk=None, **attributes)

    def variant(self, price, quantity=1):
        return SimpleNamespace(price=Decimal(price) if price is not None else None, quantity=quantity)

    def test_base_price_is_not_advertised_when_every_real_variant_overrides_it(self):
        summary = product_price_summary(self.product(price='10.00'), [self.variant('25.00'), self.variant('35.00')])
        self.assertEqual((summary.minimum, summary.maximum), (Decimal('25.00'), Decimal('35.00')))
        self.assertTrue(summary.has_range)

    def test_only_blank_variant_prices_fall_back_to_general_price(self):
        summary = product_price_summary(self.product(), [self.variant(None), self.variant('25.00')])
        self.assertEqual((summary.minimum, summary.maximum), (Decimal('25.00'), Decimal('40.00')))
        self.assertEqual(product_price_summary(self.product(), []).minimum, Decimal('40.00'))

    def test_equal_prices_have_no_range_and_stockout_does_not_change_catalog_price(self):
        summary = product_price_summary(self.product(), [self.variant('25.00', quantity=0), self.variant('25.00')])
        self.assertEqual(summary.minimum, Decimal('25.00'))
        self.assertFalse(summary.has_range)

    def test_annotations_and_prefetched_variants_do_not_access_the_related_manager(self):
        manager = Mock()
        manager.all.side_effect = AssertionError('Prices must use the already loaded variants.')
        annotated = self.product(_catalog_min_price=Decimal('25.00'), _catalog_max_price=Decimal('35.00'), stock_variants=manager)
        self.assertEqual(product_price_summary(annotated).minimum, Decimal('25.00'))
        prefetched = self.product(_prefetched_objects_cache={'stock_variants': [self.variant('29.00')]}, stock_variants=manager)
        self.assertEqual(product_price_summary(prefetched).minimum, Decimal('29.00'))


class CatalogVariantPriceTests(TestCase):
    def setUp(self):
        category = Category.objects.create(name='Enfants', slug='enfants')
        self.product = Product.objects.create(name='Polaire junior et ado', category=category, price='10.00')
        Variant.objects.create(product=self.product, size='Junior', color='Vert', quantity=0, price='25.00')
        Variant.objects.create(product=self.product, size='Ado', color='Vert', quantity=3, price='35.00')
        self.other = Product.objects.create(name='Autre polaire', category=category, price='30.00')

    def test_catalog_annotations_use_real_prices_without_additional_queries(self):
        with self.assertNumQueries(1):
            products = list(with_catalog_prices(Product.objects.all()).order_by('_catalog_min_price'))
            self.assertEqual([product.pk for product in products], [self.product.pk, self.other.pk])
            self.assertEqual([(product.display_price, product.has_price_range) for product in products], [
                (Decimal('25.00'), True), (Decimal('30.00'), False),
            ])

    def test_size_joins_do_not_restrict_the_global_catalog_price(self):
        product = with_catalog_prices(Product.objects.filter(stock_variants__size='Ado')).get()
        self.assertEqual(product.display_price, Decimal('25.00'))

    def test_public_price_sort_and_range_label_follow_variant_prices(self):
        ascending = self.client.get('/produits/', {'tri': 'prix'})
        self.assertEqual([product.pk for product in ascending.context['products']], [self.product.pk, self.other.pk])
        self.assertContains(ascending, 'À partir de 25,00 €')
        self.assertNotContains(ascending, '10,00 €')
        descending = self.client.get('/produits/', {'tri': '-prix'})
        self.assertEqual([product.pk for product in descending.context['products']], [self.other.pk, self.product.pk])

    def test_uniform_variant_price_has_no_starting_price_label(self):
        self.product.stock_variants.update(price='25.00')
        response = self.client.get('/produits/', {'q': 'junior'})
        self.assertContains(response, '25,00 €')
        self.assertNotContains(response, 'À partir de')
