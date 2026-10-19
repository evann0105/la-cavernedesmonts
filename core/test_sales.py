from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from connexion.test_helpers import verify_test_user
from core.models import Category, Product, ProductPromotion, SaleCampaign
from core.pricing import price_quote
from core.sales_forms import SaleCampaignForm, SaleSelectionForm
from core.sales_services import (cancel_campaign, cancellation_token, create_campaign, matching_rows,
    read_preview, reference_targets, selection_from_snapshot, sign_preview)
from stocks.models import Variant


class SaleCriteriaTests(TestCase):
    def setUp(self):
        self.women = Category.objects.create(name='Femmes', slug='femmes')
        self.gifts = Category.objects.create(name='Petits bonheurs', slug='petits-bonheurs')
        self.product = Product.objects.create(name='Polaire montagne', price=80, category=self.women)
        self.product.collections.add(self.gifts)
        self.red_m = Variant.objects.create(product=self.product, color='Rouge', size='M', price=40, quantity=2)
        self.green_l = Variant.objects.create(product=self.product, color='Vert', size='L', quantity=0)

    def criteria(self, **updates):
        result = {'stock': 'all', 'publication': 'published'}
        result.update(updates)
        return result

    def keys(self, **updates):
        return {target['key'] for row in matching_rows(self.criteria(**updates)) for target in row['targets']}

    def test_price_color_and_size_match_one_variant_and_not_separate_joins(self):
        self.assertEqual(self.keys(color='rouge', size='M', price_max='50'), {f'v{self.red_m.pk}'})
        self.assertFalse(self.keys(color='rouge', size='L'))
        self.assertFalse(self.keys(color='rouge', price_min='70'))
        self.assertEqual(self.keys(color=' vert ', price_min='70', price_max='80'), {f'v{self.green_l.pk}'})

    def test_category_or_collection_with_type_query_and_variant_price_fallback(self):
        for category in (self.women, self.gifts):
            self.assertEqual(self.keys(categories=[category.pk], article_type='polaires', q='montagne', price_min='75'), {f'v{self.green_l.pk}'})
        self.assertFalse(self.keys(article_type='vestes'))

    def test_multiple_categories_are_combined_without_replacing_the_first(self):
        men = Category.objects.create(name='Hommes', slug='hommes')
        male_product = Product.objects.create(name='Polaire homme', price=60, category=men)
        gift = Product.objects.create(name='Cadeau seul', price=20, category=self.gifts)
        women_keys = {f'v{self.red_m.pk}', f'v{self.green_l.pk}'}
        self.assertEqual(self.keys(categories=[self.women.pk]), women_keys)
        self.assertEqual(self.keys(categories=[self.women.pk, men.pk]), women_keys | {f'p{male_product.pk}'})
        self.assertNotIn(f'p{gift.pk}', self.keys(categories=[self.women.pk, men.pk]))

    def test_stock_filters_distinguish_unknown_from_zero_and_reserved_from_available(self):
        unknown = Variant.objects.create(product=self.product, color='Bleu', size='S', quantity=None)
        self.assertEqual(self.keys(stock='zero'), {f'v{self.green_l.pk}'})
        self.assertEqual(self.keys(stock='available'), {f'v{self.red_m.pk}'})
        self.assertIn(f'v{unknown.pk}', self.keys())
        self.red_m.reserved = 2
        self.red_m.save(update_fields=['reserved'])
        self.assertFalse(self.keys(stock='available'))
        self.assertEqual(self.keys(stock='zero'), {f'v{self.red_m.pk}', f'v{self.green_l.pk}'})

    def test_no_variants_can_match_price_category_but_never_variant_filters(self):
        product = Product.objects.create(name='Cadeau', price=20, category=self.gifts, is_published=False)
        self.assertNotIn(f'p{product.pk}', self.keys())
        self.assertIn(f'p{product.pk}', self.keys(publication='all', categories=[self.gifts.pk], price_max='25'))
        for updates in ({'color': 'rouge'}, {'size': 'M'}, {'stock': 'available'}, {'stock': 'zero'}):
            self.assertNotIn(f'p{product.pk}', self.keys(publication='all', **updates))

    def test_filter_form_rejects_unknown_categories_and_reversed_price_range(self):
        self.assertFalse(SaleSelectionForm({'stock': 'all', 'publication': 'all', 'categories': ['999999']}).is_valid())
        form = SaleSelectionForm({'stock': 'all', 'publication': 'all', 'price_min': '80', 'price_max': '20'})
        self.assertFalse(form.is_valid())
        self.assertIn('price_max', form.errors)


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class SalesWorkspaceTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user('sales-manager', is_staff=True)
        self.manager.groups.add(Group.objects.get(name='Gestion du catalogue'))
        verify_test_user(self.manager)
        self.device = TOTPDevice.objects.create(user=self.manager, name='default', confirmed=True)
        self.client.force_login(self.manager, backend='connexion.backends.EmailOrUsernameBackend')
        session = self.client.session
        session['otp_device_id'] = self.device.persistent_id
        session.save()
        self.product = Product.objects.create(name='Polaire test', price=80)
        self.green_m = Variant.objects.create(product=self.product, color='Vert', size='M', quantity=2)
        self.red_l = Variant.objects.create(product=self.product, color='Rouge', size='L', quantity=2)
        self.other = Product.objects.create(name='Autre polaire', price=60)
        self.url = reverse('core:sales_create')
        self.reference_patch = patch('core.promotion_forms.reference_price_limit', return_value=None)
        self.reference_patch.start()
        self.addCleanup(self.reference_patch.stop)
        self.sales_reference_patch = patch('core.sales_services.reference_price_limit', return_value=None)
        self.sales_reference_patch.start()
        self.addCleanup(self.sales_reference_patch.stop)

    def criteria(self, **updates):
        return {'stock': 'all', 'publication': 'published', **updates}

    def preview(self, **updates):
        data = self.criteria(**updates)
        data['product_ids'] = [self.product.pk, self.other.pk]
        data['action'] = 'preview'
        return self.client.post(self.url, data)

    def data(self, token, **updates):
        start = timezone.localtime(timezone.now() + timedelta(days=2))
        data = {'action': 'save', 'preview_token': token, 'title': 'Polaires d’hiver',
            'start_mode': 'scheduled', 'starts_at': start.strftime('%Y-%m-%dT%H:%M'),
            'ends_at': (start + timedelta(days=2)).strftime('%Y-%m-%dT%H:%M'),
            'kind': 'percent', 'value': '10', 'reference_confirmed': 'on',
            f'ref_v{self.green_m.pk}': '80', f'ref_v{self.red_l.pk}': '80', f'ref_p{self.other.pk}': '60'}
        data.update(updates)
        return data

    def create(self):
        preview = self.preview()
        self.assertEqual(preview.status_code, 200)
        response = self.client.post(self.url, self.data(preview.context['preview_token']))
        self.assertEqual(response.status_code, 302)
        return SaleCampaign.objects.get()

    def test_selection_get_and_preview_are_read_only_and_defaults_are_bound(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['filter_form'].is_valid())
        self.assertEqual(len(response.context['candidate_rows']), 2)
        response = self.preview()
        self.assertEqual(response.context['stage'], 'preview')
        self.assertEqual(response.context['preview_summary'], {'product_count': 2, 'target_count': 3})
        self.assertContains(response, 'datetime-local')
        self.assertEqual(SaleCampaign.objects.count(), 0)
        self.assertEqual(ProductPromotion.objects.count(), 0)
        self.assertIn('no-store', response['Cache-Control'])

    def test_live_category_results_and_submitted_criteria_stay_in_sync(self):
        women = Category.objects.create(name='Femmes', slug='femmes')
        men = Category.objects.create(name='Hommes', slug='hommes')
        self.product.category = women
        self.product.save(update_fields=['category'])
        self.other.category = men
        self.other.save(update_fields=['category'])
        headers = {'HTTP_X_SALES_SELECTION': '1'}
        response = self.client.get(self.url, {'categories': [women.pk]}, **headers)
        self.assertTemplateUsed(response, 'core/sales/selection.html')
        self.assertNotContains(response, '<html')
        self.assertEqual([row['product'].pk for row in response.context['candidate_rows']], [self.product.pk])
        response = self.client.get(self.url, {'categories': [women.pk, men.pk]}, **headers)
        self.assertEqual({row['product'].pk for row in response.context['candidate_rows']}, {self.product.pk, self.other.pk})
        replay = {}
        for item in response.context['filter_hidden']:
            replay.setdefault(item['name'], []).append(item['value'])
        self.assertEqual(set(replay['categories']), {women.pk, men.pk})
        replay.update(action='preview', product_ids=[self.product.pk, self.other.pk])
        preview = self.client.post(self.url, replay)
        self.assertEqual(preview.context['stage'], 'preview')
        self.assertEqual(preview.context['preview_summary']['product_count'], 2)
        self.assertEqual((SaleCampaign.objects.count(), ProductPromotion.objects.count()), (0, 0))
        self.assertIn('no-store', response['Cache-Control'])

    def test_invalid_live_price_criteria_clear_the_old_product_selection(self):
        response = self.client.get(self.url, {'price_min': 80, 'price_max': 20}, HTTP_X_SALES_SELECTION='1')
        self.assertContains(response, 'Vérifiez les critères de sélection.')
        self.assertNotContains(response, 'data-sales-product-choice')
        self.assertNotContains(response, 'data-sales-selection-submit')
        self.assertEqual(ProductPromotion.objects.count(), 0)

    def test_confirmed_creation_freezes_variants_applies_pricing_and_audits(self):
        campaign = self.create()
        self.assertEqual(campaign.promotions.count(), 2)
        promotion = campaign.promotions.get(product=self.product)
        self.assertFalse(promotion.all_variants)
        self.assertEqual(set(promotion.targets.values_list('pk', flat=True)), {self.green_m.pk, self.red_l.pk})
        active = promotion.starts_at + timedelta(seconds=1)
        self.product.refresh_from_db()
        self.assertEqual(price_quote(self.product, self.green_m, at=active).current, Decimal('72.00'))
        new = Variant.objects.create(product=self.product, size='XL', color='Bleu', quantity=1)
        self.product.refresh_from_db()
        self.assertEqual(price_quote(self.product, new, at=active).current, Decimal('80.00'))
        self.assertTrue(LogEntry.objects.filter(content_type__model='salecampaign', object_id=str(campaign.pk)).exists())
        self.assertTrue(LogEntry.objects.filter(content_type__model='productpromotion', object_id=str(promotion.pk)).exists())

    def test_criteria_freeze_only_matching_color_price_and_size_with_selected_products(self):
        rows = matching_rows(self.criteria(color='vert', size='M', price_min='70'))
        token = sign_preview(rows, self.criteria(color='vert', size='M', price_min='70'), self.manager)
        response = self.client.post(self.url, self.data(token))
        self.assertEqual(response.status_code, 302)
        promotion = ProductPromotion.objects.get()
        self.assertEqual(list(promotion.targets.all()), [self.green_m])
        self.assertEqual(promotion.reference_prices, {str(self.green_m.pk): '80'})

    def test_invalid_second_product_reference_or_overlap_writes_no_partial_campaign(self):
        preview = self.preview()
        token = preview.context['preview_token']
        response = self.client.post(self.url, self.data(token, **{f'ref_p{self.other.pk}': '600'}))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(SaleCampaign.objects.exists())
        self.assertFalse(ProductPromotion.objects.exists())
        data = self.data(token)
        from core.promotion_forms import ProductPromotionForm
        overlap = ProductPromotionForm({**data, 'scope': 'all', 'reference_product': '60'}, product=self.other)
        self.assertTrue(overlap.is_valid(), overlap.errors)
        overlap.save()
        response = self.client.post(self.url, self.data(token))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(SaleCampaign.objects.exists())
        self.assertEqual(ProductPromotion.objects.count(), 1)

    def test_snapshot_rejects_price_color_and_new_matching_variant_changes(self):
        for change in ('price', 'color', 'new'):
            with self.subTest(change=change):
                rows = matching_rows(self.criteria())
                snapshot = read_preview(sign_preview(rows, self.criteria(), self.manager), self.manager)
                if change == 'price':
                    self.product.price = Decimal('81')
                    self.product.save(update_fields=['price'])
                elif change == 'color':
                    self.green_m.color = 'Bleu'
                    self.green_m.save(update_fields=['color'])
                else:
                    Variant.objects.create(product=self.product, color='Orange', size='S', quantity=1)
                with self.assertRaises(ValidationError):
                    selection_from_snapshot(snapshot)

    def test_snapshot_is_order_independent_for_non_alphabetic_variant_creation(self):
        Variant.objects.create(product=self.product, size='XL', color='Jaune', quantity=1)
        Variant.objects.create(product=self.product, size='S', color='Noir', quantity=1)
        rows = matching_rows(self.criteria())
        snapshot = read_preview(sign_preview(rows, self.criteria(), self.manager), self.manager)
        self.assertEqual(len(selection_from_snapshot(snapshot)), 2)

    def test_invalid_and_foreign_tokens_and_product_ids_are_rejected(self):
        preview = self.preview()
        token = preview.context['preview_token']
        stranger = get_user_model().objects.create_user('stranger')
        with self.assertRaises(ValidationError):
            read_preview(token, stranger)
        self.assertEqual(self.client.post(self.url, self.data(token + 'tamper')).status_code, 200)
        response = self.client.post(self.url, {'action': 'preview', 'product_ids': [999999]})
        self.assertEqual(response.context['stage'], 'selection')
        self.assertFalse(SaleCampaign.objects.exists())

    def test_unconfirmed_or_invalid_discount_and_dates_write_nothing(self):
        token = self.preview().context['preview_token']
        for updates in ({'reference_confirmed': ''}, {'value': '100'}, {'kind': 'fixed', 'value': '70'}, {'ends_at': '2000-01-01T12:00'}):
            with self.subTest(updates=updates):
                self.assertEqual(self.client.post(self.url, self.data(token, **updates)).status_code, 200)
                self.assertFalse(SaleCampaign.objects.exists())
                self.assertFalse(ProductPromotion.objects.exists())

    def test_immediate_amount_and_fixed_campaigns_use_shared_pricing(self):
        for kind, value, expected in [('amount', '5', '75.00'), ('fixed', '50', '50.00')]:
            with self.subTest(kind=kind):
                token = self.preview().context['preview_token']
                response = self.client.post(self.url, self.data(token, kind=kind, value=value, start_mode='immediate'))
                self.assertEqual(response.status_code, 302)
                promo = ProductPromotion.objects.filter(product=self.product, enabled=True).get()
                # Each simulated request gets fresh objects; a previous quote's
                # request-scoped context must not leak into the next operation.
                product = Product.objects.get(pk=self.product.pk)
                variant = Variant.objects.get(pk=self.green_m.pk)
                self.assertEqual(price_quote(product, variant).current, Decimal(expected))
                ProductPromotion.objects.update(enabled=False)

    def test_dashboard_includes_grouped_campaign_and_existing_standalone_reductions(self):
        campaign = self.create()
        start = timezone.now() - timedelta(hours=1)
        standalone = ProductPromotion.objects.create(product=self.other, starts_at=start, ends_at=start+timedelta(days=1),
            kind='percent', value=5, reference_confirmed=True, reference_prices={'product': '60'})
        response = self.client.get(reverse('core:sales_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['counts']['scheduled'], 1)
        self.assertEqual(response.context['counts']['active'], 1)
        self.assertEqual(response.context['standalone_rows'][0]['promotion'], standalone)
        self.assertEqual(response.context['campaign_rows'][0]['campaign'], campaign)
        response = self.client.get(reverse('core:sales_dashboard'), {'status': 'scheduled'})
        self.assertFalse(response.context['standalone_rows'])

    def test_active_products_exclude_unconfirmed_or_invalid_reference_promotions(self):
        start = timezone.now() - timedelta(hours=1)
        promotion = ProductPromotion.objects.create(product=self.product, starts_at=start, ends_at=start+timedelta(days=1),
            kind='percent', value=10, reference_confirmed=False,
            reference_prices={str(self.green_m.pk): '80', str(self.red_l.pk): '80'})
        response = self.client.get(reverse('core:sales_dashboard'))
        self.assertEqual(response.context['standalone_rows'][0]['target_count'], 2)
        self.assertEqual(response.context['active_product_count'], 0)
        promotion.reference_confirmed = True
        promotion.save(update_fields=['reference_confirmed'])
        response = self.client.get(reverse('core:sales_dashboard'))
        self.assertEqual(response.context['active_product_count'], 1)

    def test_cancellation_rejects_stale_child_revision_then_preserves_history(self):
        campaign = self.create()
        detail = self.client.get(reverse('core:sales_detail', args=[campaign.pk]))
        token = detail.context['row']['cancel_token']
        child = campaign.promotions.first()
        child.revision += 1
        child.save(update_fields=['revision'])
        url = reverse('core:sales_cancel', args=[campaign.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.post(url, {'revision': campaign.revision, 'cancel_token': token})
        self.assertEqual(campaign.promotions.filter(enabled=True).count(), 2)
        fresh = self.client.get(reverse('core:sales_detail', args=[campaign.pk])).context['row']['cancel_token']
        self.client.post(url, {'revision': campaign.revision, 'cancel_token': fresh})
        campaign.refresh_from_db()
        self.assertEqual(campaign.revision, 1)
        self.assertFalse(campaign.promotions.filter(enabled=True).exists())
        self.assertEqual(campaign.promotions.filter(cancelled_at__isnull=False).count(), 2)
        self.assertEqual(SaleCampaign.objects.count(), 1)
        response = self.client.get(reverse('core:sales_dashboard'), {'status': 'cancelled'})
        self.assertEqual(response.context['counts']['cancelled'], 1)

    def test_partial_cancellation_status_stays_scheduled_with_cancelled_count(self):
        campaign = self.create()
        child = campaign.promotions.first()
        child.enabled = False
        child.save(update_fields=['enabled'])
        row = self.client.get(reverse('core:sales_detail', args=[campaign.pk])).context['row']
        self.assertEqual(row['status'], 'scheduled')
        self.assertEqual(row['cancelled_product_count'], 1)
        self.assertEqual(row['live_product_count'], 1)

    def test_anonymous_customer_staff_without_permission_and_csrf_cannot_mutate(self):
        anonymous = Client()
        self.assertEqual(anonymous.get(reverse('core:sales_dashboard')).status_code, 302)
        self.assertEqual(anonymous.post(self.url, {'action': 'preview'}).status_code, 302)
        for staff in (False, True):
            user = get_user_model().objects.create_user(f'outsider-{staff}', is_staff=staff)
            outsider = Client()
            outsider.force_login(user, backend='connexion.backends.EmailOrUsernameBackend')
            self.assertEqual(outsider.get(reverse('core:sales_dashboard')).status_code, 403)
            self.assertEqual(outsider.post(self.url, {'action': 'preview'}).status_code, 403)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.manager, backend='connexion.backends.EmailOrUsernameBackend')
        self.assertEqual(strict.post(self.url, {'action': 'preview'}).status_code, 403)
