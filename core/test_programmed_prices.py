from datetime import datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import PriceObservation, Product, ProductPromotion
from core.pricing import (discounted_price, load_pricing_context, price_quote,
                          product_price_summary, reference_price_limit, with_catalog_prices)
from stocks.models import Variant

UTC = datetime_timezone.utc


class ProgrammedPriceTests(TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 20, 12, tzinfo=UTC)
        with patch('django.utils.timezone.now', return_value=self.now - timedelta(days=40)):
            self.product = Product.objects.create(name='Polaire', price='80.00')
            self.red = Variant.objects.create(product=self.product, size='Junior', color='Rouge', quantity=0)
            self.green = Variant.objects.create(product=self.product, size='Ado', color='Vert', quantity=2, price='100.00')

    def promotion(self, **changes):
        values = dict(product=self.product, starts_at=self.now - timedelta(hours=1),
                      ends_at=self.now + timedelta(hours=1), kind='percent', value=Decimal('10.00'),
                      reference_confirmed=True,
                      reference_prices={str(self.red.pk): '80.00', str(self.green.pk): '100.00'})
        values.update(changes)
        return ProductPromotion.objects.create(**values)

    def quote(self, variant=None, at=None):
        load_pricing_context([self.product], at=at or self.now)
        return price_quote(self.product, variant or self.red, at=at or self.now)

    def test_date_boundaries_are_start_inclusive_and_end_exclusive(self):
        promotion = self.promotion(starts_at=self.now, ends_at=self.now + timedelta(hours=1))
        self.assertEqual(self.quote(at=self.now - timedelta(microseconds=1)).current, Decimal('80.00'))
        self.assertEqual(self.quote(at=promotion.starts_at).current, Decimal('72.00'))
        self.assertEqual(self.quote(at=promotion.ends_at - timedelta(microseconds=1)).current, Decimal('72.00'))
        self.assertEqual(self.quote(at=promotion.ends_at).current, Decimal('80.00'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.price, Decimal('80.00'))

    def test_daylight_saving_boundary_uses_aware_instants(self):
        paris = ZoneInfo('Europe/Paris')
        first = datetime(2026, 10, 25, 2, 15, tzinfo=paris, fold=0)
        second = datetime(2026, 10, 25, 2, 15, tzinfo=paris, fold=1)
        self.promotion(starts_at=first, ends_at=second)
        self.assertEqual(self.quote(at=first.astimezone(UTC)).current, Decimal('72.00'))
        self.assertEqual(self.quote(at=second.astimezone(UTC)).current, Decimal('80.00'))

    def test_percent_rounding_is_decimal_half_up(self):
        self.assertEqual(discounted_price('80.05', 'percent', '10'), Decimal('72.05'))
        self.assertEqual(discounted_price('29.99', 'percent', '15'), Decimal('25.49'))

    def test_euro_and_final_price_are_distinct(self):
        self.assertEqual(discounted_price('80', 'amount', '10'), Decimal('70.00'))
        self.assertEqual(discounted_price('80', 'fixed', '10'), Decimal('10.00'))
        self.assertIsNone(discounted_price('80', 'amount', '80'))
        self.assertIsNone(discounted_price('80', 'fixed', '90'))

    def test_percent_and_amount_use_confirmed_reference_not_high_regular_price(self):
        self.promotion(reference_prices={str(self.red.pk): '70.00'})
        quote = self.quote()
        self.assertEqual((quote.current, quote.regular, quote.reference),
                         (Decimal('63.00'), Decimal('80.00'), Decimal('70.00')))

    def test_variant_targets_keep_other_color_at_its_regular_price(self):
        promotion = self.promotion(all_variants=False)
        promotion.targets.add(self.red)
        self.assertEqual(self.quote(self.red).current, Decimal('72.00'))
        self.assertEqual(self.quote(self.green).current, Decimal('100.00'))

    def test_unconfirmed_or_missing_reference_never_activates(self):
        self.promotion(reference_confirmed=False)
        self.assertFalse(self.quote().has_discount)
        ProductPromotion.objects.all().delete()
        self.promotion(reference_prices={str(self.green.pk): '100.00'})
        self.assertEqual(self.quote().current, Decimal('80.00'))

    def test_cancellation_preserves_only_the_actual_elapsed_period(self):
        promotion = self.promotion(starts_at=self.now - timedelta(days=2), ends_at=self.now + timedelta(days=2),
                                   enabled=False, cancelled_at=self.now)
        self.assertEqual(self.quote(at=self.now - timedelta(seconds=1)).current, Decimal('72.00'))
        self.assertEqual(self.quote(at=self.now).current, Decimal('80.00'))
        ProductPromotion.objects.filter(pk=promotion.pk).update(cancelled_at=promotion.starts_at - timedelta(seconds=1))
        self.assertEqual(self.quote(at=self.now).current, Decimal('80.00'))

    def test_regular_price_drop_before_start_lowers_reference(self):
        with patch('django.utils.timezone.now', return_value=self.now - timedelta(hours=2)):
            self.red.price = Decimal('70.00')
            self.red.save(update_fields=['price'])
        self.promotion()
        quote = self.quote()
        self.assertEqual((quote.reference, quote.current), (Decimal('70.00'), Decimal('63.00')))

    def test_regular_price_drop_after_start_caps_final_price_without_rewriting_reference(self):
        self.promotion()
        with patch('django.utils.timezone.now', return_value=self.now - timedelta(minutes=20)):
            self.red.price = Decimal('60.00')
            self.red.save(update_fields=['price'])
        quote = self.quote()
        self.assertEqual((quote.reference, quote.current), (Decimal('80.00'), Decimal('60.00')))

    def test_old_known_regular_price_still_in_force_in_window_counts(self):
        self.promotion(reference_prices={str(self.red.pk): '100.00'})
        self.assertEqual(self.quote().reference, Decimal('80.00'))

    def test_later_regular_price_is_never_backdated_into_reference(self):
        self.promotion()
        with patch('django.utils.timezone.now', return_value=self.now + timedelta(days=1)):
            self.red.price = Decimal('50.00')
            self.red.save(update_fields=['price'])
        self.red.price = Decimal('80.00')
        self.assertEqual(self.quote().reference, Decimal('80.00'))

    def test_prior_real_promotion_lowers_reference_chronologically(self):
        self.promotion(starts_at=self.now - timedelta(days=20), ends_at=self.now - timedelta(days=19), value=Decimal('20'))
        second = self.promotion(starts_at=self.now - timedelta(days=10), ends_at=self.now - timedelta(days=9), value=Decimal('10'))
        current = self.promotion()
        quote = self.quote()
        # 80 -> 64 during first promotion -> 57.60 during second -> 51.84 now.
        self.assertEqual((quote.reference, quote.current), (Decimal('57.60'), Decimal('51.84')))
        self.assertEqual(quote.promotion_id, current.pk)

    def test_cancelled_future_promotion_is_not_a_price_ever_charged(self):
        self.promotion(starts_at=self.now - timedelta(days=10), ends_at=self.now - timedelta(days=9),
                       kind='fixed', value=Decimal('20'), enabled=False,
                       cancelled_at=self.now - timedelta(days=11))
        self.promotion()
        self.assertEqual(self.quote().reference, Decimal('80.00'))

    def test_stopped_prior_promotion_counts_only_until_cancellation(self):
        self.promotion(starts_at=self.now - timedelta(days=35), ends_at=self.now - timedelta(days=20),
                       kind='fixed', value=Decimal('20'), enabled=False,
                       cancelled_at=self.now - timedelta(days=31))
        self.promotion()
        self.assertEqual(self.quote().reference, Decimal('80.00'))

    def test_future_promotion_is_not_assumed_to_have_run_when_confirming_reference(self):
        self.promotion(starts_at=self.now + timedelta(days=1), ends_at=self.now + timedelta(days=2),
                       kind='fixed', value=Decimal('20'))
        with patch('django.utils.timezone.now', return_value=self.now):
            self.assertEqual(reference_price_limit(self.product, self.red, self.now + timedelta(days=3)), Decimal('80.00'))

    def test_no_observation_before_start_does_not_invent_thirty_days_of_history(self):
        PriceObservation.objects.all().delete()
        with patch('django.utils.timezone.now', return_value=self.now):
            self.assertIsNone(reference_price_limit(self.product, self.red, self.now - timedelta(days=1)))
        self.promotion(reference_prices={str(self.red.pk): '75.00'})
        self.assertEqual(self.quote().reference, Decimal('75.00'))

    def test_stockout_is_not_hidden_or_made_purchasable_by_reduction(self):
        self.promotion()
        self.assertEqual(self.quote().current, Decimal('72.00'))
        self.red.refresh_from_db()
        self.assertEqual((self.red.quantity, self.red.available), (0, 0))

    def test_summary_and_sort_respect_real_variant_targets(self):
        promotion = self.promotion(kind='fixed', value=Decimal('20'), all_variants=False)
        promotion.targets.add(self.green)
        other = Product.objects.create(name='Autre article', price='50.00')
        with self.assertNumQueries(7):
            products = list(with_catalog_prices(Product.objects.all(), at=self.now).order_by('_catalog_min_price'))
            self.assertEqual([p.pk for p in products], [self.product.pk, other.pk])
            self.assertEqual((products[0].display_price, products[0].has_price_range), (Decimal('20.00'), True))
        self.assertEqual(self.quote(self.red).current, Decimal('80.00'))

    def test_loading_once_provides_quotes_without_queries_per_variant(self):
        self.promotion()
        load_pricing_context([self.product], at=self.now)
        variants = self.product._pricing_context.variants[self.product.pk]
        with self.assertNumQueries(0), patch('django.utils.timezone.now', return_value=self.now):
            quotes = [variant.effective_price for variant in variants]
            self.assertEqual(quotes, [Decimal('90.00'), Decimal('72.00')])
            summary = product_price_summary(self.product, variants, at=self.now)
            self.assertEqual((summary.minimum, summary.maximum), (Decimal('72.00'), Decimal('90.00')))

    def test_reads_never_record_new_observations(self):
        self.promotion()
        before = PriceObservation.objects.count()
        for _ in range(3):
            self.quote()
            list(with_catalog_prices(Product.objects.all(), at=self.now))
        self.assertEqual(PriceObservation.objects.count(), before)

    def test_product_change_observes_inherited_variants_but_not_overrides(self):
        self.product.price = Decimal('90.00')
        self.product.save(update_fields=['price'])
        self.assertEqual(PriceObservation.objects.filter(variant=self.red).last().price, Decimal('90.00'))
        self.assertEqual(PriceObservation.objects.filter(variant=self.green).last().price, Decimal('100.00'))

    def test_model_rejects_invalid_period_percentage_and_final_price(self):
        promotion = ProductPromotion(product=self.product, starts_at=self.now, ends_at=self.now,
                                      kind='percent', value=100)
        with self.assertRaises(ValidationError):
            promotion.full_clean()
        promotion.ends_at = self.now + timedelta(hours=1)
        promotion.kind, promotion.value = 'fixed', Decimal('90')
        promotion.reference_prices = {str(self.red.pk): '80.00'}
        with self.assertRaises(ValidationError):
            promotion.full_clean()

    def test_same_loaded_variant_expires_without_a_database_write_or_reload(self):
        promotion = self.promotion()
        load_pricing_context([self.product], at=self.now)
        self.red._state.fields_cache['product'] = self.product
        with self.assertNumQueries(0), patch('django.utils.timezone.now', return_value=self.now):
            self.assertEqual(self.red.effective_price, Decimal('72.00'))
        with self.assertNumQueries(0), patch('django.utils.timezone.now', return_value=promotion.ends_at):
            self.assertEqual(self.red.effective_price, Decimal('80.00'))

    def test_stock_writes_do_not_discard_shared_price_context(self):
        self.promotion()
        load_pricing_context([self.product], at=self.now)
        self.green._state.fields_cache['product'] = self.product
        context = self.product._pricing_context
        self.green.reserved = 1
        self.green.save(update_fields=['reserved'])
        self.assertIs(self.product._pricing_context, context)

    def test_next_boundary_is_the_next_real_start_or_end(self):
        promotion = self.promotion(starts_at=self.now + timedelta(hours=1), ends_at=self.now + timedelta(hours=2))
        self.assertEqual(self.quote().next_boundary, promotion.starts_at)
        self.assertEqual(self.quote(at=promotion.starts_at).next_boundary, promotion.ends_at)
        self.assertIsNone(self.quote(at=promotion.ends_at).next_boundary)


    def test_future_fixed_price_survives_a_lower_reference_without_false_savings(self):
        self._assert_successive_scheduled_prices('fixed', '50.00')

    def test_future_euro_remise_preserves_original_final_and_actual_history(self):
        self._assert_successive_scheduled_prices('amount', '50.00')

    def _assert_successive_scheduled_prices(self, kind, value):
        with patch('django.utils.timezone.now', return_value=self.now - timedelta(days=40)):
            product = Product.objects.create(name='Polaire à 100 €', price='100.00')
        first = ProductPromotion.objects.create(
            product=product, starts_at=self.now + timedelta(days=1), ends_at=self.now + timedelta(days=2),
            kind='fixed', value=Decimal('20.00'), reference_confirmed=True,
            reference_prices={'product': '100.00'},
        )
        second = ProductPromotion.objects.create(
            product=product, starts_at=self.now + timedelta(days=2), ends_at=self.now + timedelta(days=3),
            kind=kind, value=Decimal(value), reference_confirmed=True,
            reference_prices={'product': '100.00'},
        )
        load_pricing_context([product], at=self.now)
        self.assertEqual(price_quote(product, at=self.now).current, Decimal('100.00'))
        self.assertEqual(price_quote(product, at=first.starts_at).current, Decimal('20.00'))
        quote = price_quote(product, at=second.starts_at)
        self.assertEqual(quote.current, Decimal('50.00'))
        self.assertEqual(quote.promotion_id, second.pk)
        self.assertFalse(quote.has_discount)
        self.assertIsNone(quote.reference)
        # Both the first actual price and the preserved second price are kept
        # as chronological periods, even when the latter cannot claim savings.
        later = second.ends_at + timedelta(seconds=1)
        context = product._pricing_context
        context.at, context._period_cache = later, {}
        periods = context.periods(product.pk, 'product')
        self.assertEqual([period['price'] for period in periods], [Decimal('20.00'), Decimal('50.00')])
        self.assertIsNone(periods[1]['reference'])
        self.assertEqual(price_quote(product, at=second.ends_at).current, Decimal('100.00'))
