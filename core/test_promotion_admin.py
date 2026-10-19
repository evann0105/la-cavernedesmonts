from datetime import datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from connexion.test_helpers import verify_test_user
from core.models import Product, ProductPromotion
from core.promotion_forms import ProductPromotionForm
from stocks.models import Variant

PARIS = ZoneInfo('Europe/Paris')
UTC = datetime_timezone.utc


class ProductPromotionFormTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(name='Polaire', price='80.00')
        self.green_m = Variant.objects.create(product=self.product, size='M', color='Vert', price='70.00', quantity=2)
        self.green_l = Variant.objects.create(product=self.product, size='L', color='Vert', quantity=2)
        self.red_l = Variant.objects.create(product=self.product, size='L', color='Rouge', quantity=0)
        self.now = datetime(2030, 1, 1, 12, tzinfo=UTC)
        self.reference_patch = patch('core.promotion_forms.reference_price_limit', return_value=None)
        self.reference_patch.start()
        self.addCleanup(self.reference_patch.stop)

    def data(self, **updates):
        data = {'start_mode': 'scheduled', 'starts_at': '2030-01-02T10:00', 'ends_at': '2030-01-02T12:00',
            'scope': 'all', 'kind': 'percent', 'value': '10', 'reference_confirmed': 'on',
            f'reference_{self.green_m.pk}': '70.00', f'reference_{self.green_l.pk}': '80.00', f'reference_{self.red_l.pk}': '80.00'}
        data.update(updates)
        return data

    def form(self, **updates):
        return ProductPromotionForm(self.data(**updates), product=self.product, now=self.now)

    def test_all_target_reduction_preserves_regular_prices_and_references(self):
        form = self.form()
        self.assertTrue(form.is_valid(), form.errors)
        promotion = form.save()
        self.assertTrue(promotion.all_variants)
        self.assertEqual(promotion.targets.count(), 0)
        self.assertEqual(promotion.starts_at, datetime(2030, 1, 2, 9, tzinfo=UTC))
        self.assertEqual(promotion.reference_prices[str(self.green_m.pk)], '70.00')
        self.assertTrue(promotion.reference_confirmed)
        self.product.refresh_from_db()
        self.green_m.refresh_from_db()
        self.assertEqual((self.product.price, self.green_m.price), (Decimal('80.00'), Decimal('70.00')))

    def test_color_scope_uses_only_real_variants_for_that_color(self):
        form = self.form(scope='color', color='vert')
        self.assertTrue(form.is_valid(), form.errors)
        promotion = form.save()
        self.assertFalse(promotion.all_variants)
        self.assertEqual(set(promotion.targets.values_list('pk', flat=True)), {self.green_m.pk, self.green_l.pk})
        self.assertEqual(set(promotion.reference_prices), {str(self.green_m.pk), str(self.green_l.pk)})

    def test_specific_sizes_scope_is_checked_for_product_membership(self):
        form = self.form(scope='variants', targets=[str(self.green_m.pk)])
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(list(form.save().targets.all()), [self.green_m])
        other = Product.objects.create(name='Autre', price='15')
        foreign = Variant.objects.create(product=other, size='M', color='Vert', quantity=1)
        form = self.form(scope='variants', targets=[str(foreign.pk)])
        self.assertFalse(form.is_valid())
        self.assertIn('targets', form.errors)

    def test_empty_color_or_size_scope_is_rejected(self):
        for updates in ({'scope': 'color', 'color': ''}, {'scope': 'variants', 'targets': []}):
            with self.subTest(updates=updates):
                self.assertFalse(self.form(**updates).is_valid())

    def test_immediate_start_uses_server_clock_not_submitted_past_date(self):
        form = self.form(start_mode='immediate', starts_at='2000-01-01T12:00')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['starts_at'], self.now)

    def test_dates_require_future_start_and_strictly_later_end(self):
        for updates in ({'starts_at': ''}, {'starts_at': '2029-12-31T10:00'},
                        {'ends_at': '2030-01-02T10:00'}, {'ends_at': ''}):
            with self.subTest(updates=updates):
                self.assertFalse(self.form(**updates).is_valid())

    def test_paris_clock_change_missing_and_ambiguous_hours_are_rejected(self):
        for start in ('2030-03-31T02:30', '2030-10-27T02:30'):
            with self.subTest(start=start):
                form = self.form(starts_at=start, ends_at='2030-12-01T12:00')
                self.assertFalse(form.is_valid())
                self.assertIn('starts_at', form.errors)

    def test_percentage_amount_and_final_price_must_reduce_every_target(self):
        for updates in ({'value': '100'}, {'value': '0'}, {'kind': 'amount', 'value': '70'},
                        {'kind': 'fixed', 'value': '70'}, {'kind': 'fixed', 'value': '0'}):
            with self.subTest(updates=updates):
                form = self.form(**updates)
                self.assertFalse(form.is_valid())
                self.assertIn('value', form.errors)
        self.assertTrue(self.form(kind='amount', value='5').is_valid())
        self.assertTrue(self.form(kind='fixed', value='60').is_valid())

    def test_reference_prices_require_confirmation_and_cannot_overstate_known_minimum(self):
        form = self.form(reference_confirmed='')
        self.assertFalse(form.is_valid())
        self.assertIn('reference_confirmed', form.errors)
        for value in ('', '0', '90'):
            with self.subTest(value=value):
                form = self.form(**{f'reference_{self.green_m.pk}': value})
                self.assertFalse(form.is_valid())
                self.assertIn(f'reference_{self.green_m.pk}', form.errors)
        with patch('core.promotion_forms.reference_price_limit', return_value=Decimal('60.00')):
            form = self.form()
            self.assertFalse(form.is_valid())
            self.assertIn(f'reference_{self.green_m.pk}', form.errors)

    def test_non_target_reference_values_do_not_affect_saved_promotion(self):
        form = self.form(scope='variants', targets=[str(self.green_m.pk)], **{f'reference_{self.red_l.pk}': ''})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(set(form.save().reference_prices), {str(self.green_m.pk)})

    def test_overlapping_targets_rejected_but_disjoint_sizes_and_adjacent_dates_allowed(self):
        existing = ProductPromotion.objects.create(product=self.product, starts_at=datetime(2030, 1, 2, 9, tzinfo=UTC),
            ends_at=datetime(2030, 1, 2, 11, tzinfo=UTC), kind='percent', value='10', all_variants=False,
            reference_confirmed=True, reference_prices={str(self.green_m.pk): '70'})
        existing.targets.add(self.green_m)
        self.assertFalse(self.form(scope='variants', targets=[str(self.green_m.pk)]).is_valid())
        self.assertTrue(self.form(scope='variants', targets=[str(self.red_l.pk)]).is_valid())
        self.assertTrue(self.form(starts_at='2030-01-02T12:00', ends_at='2030-01-02T14:00').is_valid())
        existing.enabled = False
        existing.save(update_fields=['enabled'])
        self.assertTrue(self.form().is_valid())

    def test_product_without_variants_uses_one_confirmed_reference(self):
        product = Product.objects.create(name='Sans déclinaisons', price='80')
        data = self.data(reference_product='80')
        form = ProductPromotionForm(data, product=product, now=self.now)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().reference_prices, {'product': '80'})


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class CatalogPromotionAccessTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(name='Polaire à programmer', price='80')
        self.manager = get_user_model().objects.create_user('promotion-manager', is_staff=True)
        self.manager.groups.add(Group.objects.get(name='Gestion du catalogue'))
        verify_test_user(self.manager)
        self.device = TOTPDevice.objects.create(user=self.manager, name='default', confirmed=True)
        self.client.force_login(self.manager, backend='connexion.backends.EmailOrUsernameBackend')
        session = self.client.session
        session['otp_device_id'] = self.device.persistent_id
        session.save()
        self.url = reverse('core:catalog_promotions', args=[self.product.pk])
        self.reference_patch = patch('core.promotion_forms.reference_price_limit', return_value=None)
        self.reference_patch.start()
        self.addCleanup(self.reference_patch.stop)

    def data(self):
        start = timezone.localtime(timezone.now() + timedelta(days=2), PARIS)
        end = start + timedelta(days=5)
        return {'start_mode': 'scheduled', 'starts_at': start.strftime('%Y-%m-%dT%H:%M'),
            'ends_at': end.strftime('%Y-%m-%dT%H:%M'), 'scope': 'all', 'kind': 'percent', 'value': '10',
            'reference_confirmed': 'on', 'reference_product': '80'}

    def promotion(self):
        self.client.post(self.url, self.data())
        return ProductPromotion.objects.get(product=self.product)

    def test_management_page_has_calendar_reference_confirmation_and_private_cache_policy(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'datetime-local')
        self.assertContains(response, 'Paris')
        self.assertContains(response, '30 jours')
        self.assertContains(response, 'Enregistrer la réduction')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertContains(self.client.get(reverse('core:catalog_edit', args=[self.product.pk])), self.url)
        self.assertContains(self.client.get(reverse('core:catalog_list')), self.url)

    def test_create_is_audited_and_invalid_form_writes_nothing(self):
        invalid = self.data()
        invalid['value'] = '100'
        self.assertEqual(self.client.post(self.url, invalid).status_code, 200)
        self.assertEqual(ProductPromotion.objects.count(), 0)
        self.assertRedirects(self.client.post(self.url, self.data()), self.url)
        promotion = ProductPromotion.objects.get()
        self.assertTrue(LogEntry.objects.filter(user=self.manager, object_id=str(promotion.pk), content_type__model='productpromotion').exists())
        self.product.refresh_from_db()
        self.assertEqual(self.product.price, Decimal('80'))

    def test_cancel_requires_post_correct_product_and_current_revision(self):
        promotion = self.promotion()
        url = reverse('core:catalog_promotion_cancel', args=[self.product.pk, promotion.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.post(url, {'revision': promotion.revision + 1})
        promotion.refresh_from_db()
        self.assertTrue(promotion.enabled)
        self.client.post(url, {'revision': promotion.revision})
        promotion.refresh_from_db()
        self.assertFalse(promotion.enabled)
        self.assertIsNotNone(promotion.cancelled_at)
        self.assertEqual(ProductPromotion.objects.count(), 1)
        other = Product.objects.create(name='Autre fiche', price='30')
        foreign_url = reverse('core:catalog_promotion_cancel', args=[other.pk, promotion.pk])
        self.assertEqual(self.client.post(foreign_url, {'revision': promotion.revision}).status_code, 404)

    def test_unauthorized_and_csrf_requests_cannot_change_promotions(self):
        anonymous = Client()
        self.assertEqual(anonymous.get(self.url).status_code, 302)
        self.assertEqual(anonymous.post(self.url, self.data()).status_code, 302)
        for user in [get_user_model().objects.create_user('buyer'), get_user_model().objects.create_user('staff-no-catalog', is_staff=True)]:
            outsider = Client()
            outsider.force_login(user, backend='connexion.backends.EmailOrUsernameBackend')
            self.assertEqual(outsider.get(self.url).status_code, 403)
            self.assertEqual(outsider.post(self.url, self.data()).status_code, 403)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.manager, backend='connexion.backends.EmailOrUsernameBackend')
        self.assertEqual(csrf.post(self.url, self.data()).status_code, 403)
        self.assertEqual(ProductPromotion.objects.count(), 0)
