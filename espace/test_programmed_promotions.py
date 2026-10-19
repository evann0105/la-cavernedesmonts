from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Category, PriceObservation, Product, ProductPromotion
from core.pricing import price_quote
from paiement.models import Order
from stocks.models import Variant


class ProgrammedPromotionCollectionTests(TestCase):
    def setUp(self):
        self.now = timezone.now() + timedelta(seconds=2)

    def product(self, name='Polaire en réduction', **values):
        return Product.objects.create(name=name, price=values.pop('price', '80.00'), **values)

    def campaign(self, product, **values):
        parameters = {'product': product, 'starts_at': self.now - timedelta(hours=1),
            'ends_at': self.now + timedelta(hours=1), 'kind': 'percent', 'value': '10',
            'enabled': True, 'reference_confirmed': True, 'all_variants': True,
            'reference_prices': {'product': str(product.price)}}
        parameters.update(values)
        return ProductPromotion.objects.create(**parameters)

    def collection(self, name='promotions', at=None):
        with patch('espace.views.timezone.now', return_value=at or self.now):
            return self.client.get(reverse(f'espace:{name}'))

    def ids(self, response):
        return [product.pk for product in response.context['products']]

    def test_active_period_enters_promotions_without_manual_category_and_exits_on_end(self):
        product = self.product()
        campaign = self.campaign(product)
        response = self.collection()
        self.assertEqual(self.ids(response), [product.pk])
        self.assertContains(response, '72,00 €')
        self.assertEqual(self.ids(self.collection(at=campaign.ends_at)), [])
        self.assertNotContains(self.collection(at=campaign.ends_at), product.name)

    def test_future_period_is_excluded_until_exact_start(self):
        product = self.product()
        campaign = self.campaign(product, starts_at=self.now + timedelta(hours=2), ends_at=self.now + timedelta(hours=3))
        self.assertEqual(self.ids(self.collection()), [])
        self.assertEqual(self.ids(self.collection(at=campaign.starts_at)), [product.pk])

    def test_disabled_unconfirmed_missing_reference_or_unpublished_rows_are_not_advertised(self):
        disabled = self.product('Annulée')
        unconfirmed = self.product('Non confirmée')
        unknown_reference = self.product('Référence absente')
        hidden = self.product('Non publiée', is_published=False)
        self.campaign(disabled, enabled=False, cancelled_at=self.now - timedelta(minutes=1))
        self.campaign(unconfirmed, reference_confirmed=False)
        self.campaign(unknown_reference, reference_prices={})
        self.campaign(hidden)
        self.assertEqual(self.ids(self.collection()), [])

    def test_only_targeted_real_variant_prices_are_used_on_promotional_cards(self):
        product = self.product(price='10.00')
        green = Variant.objects.create(product=product, color='Vert', size='Junior', price='70.00', quantity=2)
        red = Variant.objects.create(product=product, color='Rouge', size='Ado', price='80.00', quantity=2)
        campaign = self.campaign(product, all_variants=False, reference_prices={str(green.pk): '70.00'})
        campaign.targets.add(green)
        response = self.collection()
        self.assertEqual(self.ids(response), [product.pk])
        self.assertContains(response, 'À partir de 63,00 €')
        self.assertNotContains(response, '10,00 €')
        shown = list(response.context['products'])[0]
        self.assertEqual(shown.display_price, Decimal('63.00'))
        self.assertTrue(shown.has_price_range)
        self.assertEqual(price_quote(product, red, at=self.now).current, Decimal('80.00'))

    def test_manual_special_offers_collection_is_preserved_and_sorted_by_effective_price(self):
        offers = Category.objects.create(name='Offres spéciales', slug='offres-speciales')
        manual = self.product('Sélection manuelle', price='30', category=offers)
        supplementary = self.product('Rubrique complémentaire', price='40')
        supplementary.collections.add(offers)
        active = self.product('Prix variable remisé', price='100')
        variant = Variant.objects.create(product=active, size='M', color='Vert', quantity=1, price='25')
        self.campaign(active, value='50', reference_prices={str(variant.pk): '25'})
        response = self.collection()
        self.assertEqual(self.ids(response), [active.pk, manual.pk, supplementary.pk])
        self.assertContains(response, '12,50 €')
        # An expired period no longer qualifies by itself; manual selection remains.
        self.assertEqual(self.ids(self.collection(at=self.now + timedelta(hours=2))), [manual.pk, supplementary.pk])

    def test_new_collection_uses_variant_price_and_scheduled_price_then_restores_base(self):
        product = self.product(price='5')
        junior = Variant.objects.create(product=product, size='Junior', color='Vert', price='60', quantity=2)
        Variant.objects.create(product=product, size='Ado', color='Vert', price='90', quantity=2)
        campaign = self.campaign(product, all_variants=False, reference_prices={str(junior.pk): '60'})
        campaign.targets.add(junior)
        response = self.collection('new')
        self.assertContains(response, 'À partir de 54,00 €')
        self.assertNotContains(response, '5,00 €')
        self.assertContains(self.collection('new', at=campaign.ends_at), 'À partir de 60,00 €')

    def test_best_sellers_keep_sales_rank_and_show_variant_promotion_prices(self):
        first = self.product('Plus vendu', price='5')
        variant = Variant.objects.create(product=first, size='M', color='Vert', price='60', quantity=2)
        second = self.product('Second article', price='20')
        campaign = self.campaign(first, all_variants=False, reference_prices={str(variant.pk): '60'})
        campaign.targets.add(variant)
        Order.objects.create(session_key='promotion-rank', status='paid', subtotal=14000,
            items=[{'product': first.pk, 'quantity': 2}, {'product': second.pk, 'quantity': 1}])
        response = self.collection('best')
        self.assertEqual(self.ids(response), [first.pk, second.pk])
        self.assertContains(response, '54,00 €')
        self.assertNotContains(response, '5,00 €')

    def test_temporarily_lower_price_without_valid_discount_reference_is_not_advertised_as_promotion(self):
        product = self.product('Prix temporaire sans réduction annoncée')
        PriceObservation.objects.filter(product=product).update(price='50', observed_at=self.now - timedelta(days=3))
        self.campaign(product, kind='fixed', value='60')
        quote = price_quote(product, at=self.now)
        self.assertEqual(quote.current, Decimal('60.00'))
        self.assertFalse(quote.has_discount)
        self.assertEqual(self.ids(self.collection()), [])
        self.assertContains(self.collection('new'), '60,00 €')
