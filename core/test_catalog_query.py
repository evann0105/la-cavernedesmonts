from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.http import QueryDict
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from core.catalog_query import catalog_queryset, normalize_catalog_filters
from core.models import Category, Product, ProductPromotion
from stocks.models import Variant


class CatalogFilterNormalizationTests(SimpleTestCase):
    def test_only_supported_filter_values_survive(self):
        filters = normalize_catalog_filters(QueryDict(
            'q=++polaire++&categorie=femmes&statut=draft&attention=missing_stock&tri=-price&page=7'
        ))
        self.assertEqual(filters, {'q': 'polaire', 'categorie': 'femmes', 'statut': 'draft',
                                   'attention': 'missing_stock', 'tri': '-price'})

    def test_invalid_values_and_non_text_values_use_safe_defaults(self):
        self.assertEqual(normalize_catalog_filters({
            'q': ['polaire'], 'categorie': '../../femmes', 'statut': 'all',
            'attention': 'quantity__lt=0', 'tri': 'information_sources',
        }), dict.fromkeys(('q', 'categorie', 'statut', 'attention', 'tri'), ''))
        self.assertEqual(normalize_catalog_filters({'q': 'x' * 150})['q'], 'x' * 100)
        self.assertEqual(normalize_catalog_filters({'categorie': 'x' * 141})['categorie'], '')


class CatalogQueryTests(TestCase):
    def setUp(self):
        self.femmes = Category.objects.create(name='Femmes', slug='femmes')
        self.hommes = Category.objects.create(name='Hommes', slug='hommes')
        self.offres = Category.objects.create(name='Offres', slug='offres')

    def product(self, name, **values):
        defaults = {'name': name, 'price': '40.00', 'category': self.femmes, 'brand': 'Peak Mountain',
                    'main_image': 'products/exemple.webp', 'is_published': True}
        defaults.update(values)
        return Product.objects.create(**defaults)

    def ids(self, **filters):
        return list(catalog_queryset(filters).values_list('pk', flat=True))

    def test_combined_category_search_status_and_attention_do_not_duplicate_rows(self):
        matching = self.product('Polaire femme', main_image='', brand='', is_published=False)
        matching.collections.add(self.femmes, self.offres)
        complementary = self.product('Polaire mixte', category=self.hommes, main_image='', brand='', is_published=False)
        complementary.collections.add(self.femmes)
        self.product('Polaire publiée', main_image='', brand='')
        self.product('Polaire homme', category=self.hommes, main_image='', brand='', is_published=False)
        self.product('Pantalon femme', main_image='', brand='', is_published=False)
        self.product('Polaire avec photo', brand='', is_published=False)
        filters = {'q': 'polaire', 'categorie': 'femmes', 'statut': 'draft', 'attention': 'missing_photo'}
        results = catalog_queryset(filters)
        self.assertEqual(results.count(), 2)
        self.assertCountEqual(results.values_list('pk', flat=True), [matching.pk, complementary.pk])

    def test_search_includes_brand_and_description(self):
        branded = self.product('Veste', brand='Norway')
        described = self.product('Blouson', description='Collection Norway')
        self.product('Pantalon')
        self.assertCountEqual(self.ids(q='norway'), [branded.pk, described.pk])

    def test_missing_photo_requires_both_possible_main_sources_to_be_empty(self):
        empty = self.product('Sans photo', main_image='', static_image='')
        null = self.product('Photo nulle', main_image=None, static_image='')
        uploaded = self.product('Photo téléversée', static_image='')
        imported = self.product('Photo importée', main_image=None, static_image='core/img/photo.webp')
        self.assertCountEqual(self.ids(attention='missing_photo'), [empty.pk, null.pk])
        products = {item.pk: item for item in catalog_queryset({})}
        self.assertTrue(products[empty.pk]._needs_photo)
        self.assertFalse(products[uploaded.pk]._needs_photo)
        self.assertFalse(products[imported.pk]._needs_photo)

    def test_missing_brand_accepts_empty_and_whitespace_only(self):
        empty = self.product('Sans marque', brand='')
        spaces = self.product('Espaces', brand=' \t\n ')
        named = self.product('Marque renseignée', brand=' Peak Mountain ')
        self.assertCountEqual(self.ids(attention='missing_brand'), [empty.pk, spaces.pk])
        self.assertFalse(catalog_queryset({}).get(pk=named.pk)._needs_brand)

    def test_unknown_stock_is_not_reported_as_sold_out(self):
        no_variants = self.product('Sans déclinaison', availability='unavailable')
        unknown = self.product('Quantité à renseigner')
        Variant.objects.create(product=unknown, size='M', color='Vert', quantity=None)
        Variant.objects.create(product=unknown, size='L', color='Vert', quantity=0)
        partly_unknown = self.product('Partiellement renseigné')
        Variant.objects.create(product=partly_unknown, size='M', color='Vert', quantity=None)
        Variant.objects.create(product=partly_unknown, size='L', color='Vert', quantity=2)
        self.assertCountEqual(self.ids(attention='missing_stock'), [no_variants.pk, unknown.pk, partly_unknown.pk])
        self.assertEqual(self.ids(attention='out_of_stock'), [])
        for product in catalog_queryset({}):
            self.assertTrue(product._needs_stock)
            self.assertFalse(product._out_of_stock)

    def test_sold_out_means_every_known_variant_is_empty_or_fully_reserved(self):
        empty = self.product('Tout à zéro')
        Variant.objects.create(product=empty, size='M', color='Rouge', quantity=0)
        reserved = self.product('Tout réservé')
        Variant.objects.create(product=reserved, size='M', color='Vert', quantity=3, reserved=3)
        Variant.objects.create(product=reserved, size='L', color='Vert', quantity=0)
        available = self.product('Encore disponible')
        Variant.objects.create(product=available, size='M', color='Vert', quantity=3, reserved=2)
        Variant.objects.create(product=available, size='L', color='Vert', quantity=0)
        self.assertCountEqual(self.ids(attention='out_of_stock'), [empty.pk, reserved.pk])
        self.assertEqual(self.ids(attention='missing_stock'), [])
        rows = {item.pk: item for item in catalog_queryset({})}
        self.assertTrue(rows[reserved.pk]._out_of_stock)
        self.assertFalse(rows[available.pk]._out_of_stock)

    def test_archive_filter_is_explicit_and_normal_status_filters_still_work(self):
        source = self.product('Original')
        duplicate = self.product('Ancien doublon', duplicate_of=source, is_published=False)
        draft = self.product('Brouillon', is_published=False)
        self.assertEqual(self.ids(statut='duplicate'), [duplicate.pk])
        self.assertEqual(self.ids(statut='published'), [source.pk])
        self.assertCountEqual(self.ids(statut='draft'), [duplicate.pk, draft.pk])

    def test_name_sort_is_case_insensitive_and_equal_names_are_stable(self):
        zulu = self.product('Zulu')
        lower = self.product('alpha')
        upper = self.product('Alpha')
        self.assertEqual(self.ids(tri='name'), [lower.pk, upper.pk, zulu.pk])
        self.assertEqual(self.ids(tri='-name'), [zulu.pk, lower.pk, upper.pk])

    def test_recent_sort_uses_identifier_to_break_equal_creation_dates(self):
        earlier = self.product('Premier')
        later = self.product('Second')
        Product.objects.update(created_at=timezone.now())
        self.assertEqual(self.ids(), [later.pk, earlier.pk])

    def test_price_sort_uses_real_variant_prices_and_stable_name_ties(self):
        low = self.product('Alpha', price='90.00')
        Variant.objects.create(product=low, size='Junior', color='Vert', quantity=0, price='25.00')
        Variant.objects.create(product=low, size='Ado', color='Vert', quantity=3, price='35.00')
        tied = self.product('Beta', price='25.00')
        high = self.product('Gamma', price='30.00')
        self.assertEqual(self.ids(tri='price'), [low.pk, tied.pk, high.pk])
        self.assertEqual(self.ids(tri='-price'), [high.pk, low.pk, tied.pk])
        self.assertEqual(catalog_queryset({}).get(pk=low.pk).display_price, Decimal('25.00'))

    def test_price_sort_reflects_current_promotion_not_a_future_one(self):
        now = timezone.now()
        with patch('django.utils.timezone.now', return_value=now - timedelta(days=40)):
            promoted = self.product('Polaire soldée', price='80.00')
            variant = Variant.objects.create(product=promoted, size='M', color='Vert', quantity=2)
            future = self.product('Polaire à venir', price='90.00')
            future_variant = Variant.objects.create(product=future, size='M', color='Rouge', quantity=2)
        other = self.product('Polaire habituelle', price='50.00')
        ProductPromotion.objects.create(product=promoted, starts_at=now - timedelta(hours=1),
            ends_at=now + timedelta(days=1), kind='percent', value=50, reference_confirmed=True,
            reference_prices={str(variant.pk): '80.00'})
        ProductPromotion.objects.create(product=future, starts_at=now + timedelta(hours=1),
            ends_at=now + timedelta(days=1), kind='percent', value=90, reference_confirmed=True,
            reference_prices={str(future_variant.pk): '90.00'})
        with patch('django.utils.timezone.now', return_value=now):
            self.assertEqual(self.ids(tri='price'), [promoted.pk, other.pk, future.pk])
            self.assertEqual(catalog_queryset({}).get(pk=promoted.pk).display_price, Decimal('40.00'))

    def test_page_rows_have_prices_flags_and_related_objects_without_n_plus_one_queries(self):
        source = self.product('Source')
        copy = self.product('Doublon', duplicate_of=source, main_image='', brand='')
        Variant.objects.create(product=copy, size='M', color='Vert', quantity=0)
        with self.assertNumQueries(2):
            rows = list(catalog_queryset({}))
            for product in rows:
                self.assertEqual(product.category.name, 'Femmes')
                self.assertEqual(product.display_price, Decimal('40.00'))
                if product.duplicate_of_id:
                    self.assertEqual(product.duplicate_of.name, 'Source')
                self.assertIsInstance(product._needs_stock, bool)
                self.assertIsInstance(product._out_of_stock, bool)
