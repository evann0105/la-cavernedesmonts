"""Stock alerts observe availability without changing quantities or sending duplicates."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings

from connexion.test_helpers import verify_test_user
from core.models import Product
from stocks.models import Variant
from .mail import deliver_pending
from .models import AlertEvent, AlertNotice, AlertSettings, StockWatch
from .stock import scan_low_stock, stock_notice_is_current, stock_snapshot, update_rule


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   EMAIL_DELIVERY_ENABLED=True, EMAIL_TEST_MODE=False,
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class StockAlertTests(TestCase):
    def setUp(self):
        self.seller = get_user_model().objects.create_user('stock-alert-seller', email='seller@example.test', is_staff=True)
        self.seller.user_permissions.add(Permission.objects.get(content_type__app_label='core', codename='manage_catalog'))
        verify_test_user(self.seller)
        self.preferences = AlertSettings.objects.create(recipient='seller@example.test',
            stock_notifications_enabled=True, stock_threshold=3)
        self.product = Product.objects.create(name='Polaire Écureuil', price=Decimal('80.00'), is_published=True)
        self.variant = Variant.objects.create(product=self.product, size='M', color='Vert', quantity=8)

    def quantity(self, quantity, reserved=0, variant=None):
        Variant.objects.filter(pk=(variant or self.variant).pk).update(quantity=quantity, reserved=reserved)

    def notices(self):
        return AlertNotice.objects.filter(kind='low_stock')

    def test_snapshot_is_read_only_and_uses_available_stock_and_unknowns(self):
        self.quantity(5, reserved=3)
        Variant.objects.create(product=self.product, size='L', color='Vert', quantity=None)
        Variant.objects.create(product=self.product, size='XL', color='Vert', quantity=0)
        hidden = Product.objects.create(name='Polaire non publiée', price=Decimal('60'), is_published=False)
        Variant.objects.create(product=hidden, size='M', color='Vert', quantity=0)
        result = stock_snapshot()
        self.assertEqual(result['counts'], {'low': 1, 'out': 1, 'unknown': 1, 'disabled': 0, 'normal': 0, 'total': 3})
        row = next(row for row in result['rows'] if row['variant_id'] == self.variant.pk)
        self.assertEqual((row['quantity'], row['reserved'], row['available'], row['threshold']), (5, 3, 2, 3))
        self.assertEqual(result['unknown_count'], 1)
        self.assertEqual(len(result['low_rows']), 2)
        self.assertFalse(StockWatch.objects.exists())
        self.assertFalse(AlertNotice.objects.exists())

    def test_snapshot_search_handles_product_size_colour_and_accents(self):
        result = stock_snapshot('ecureuil vert M')
        self.assertEqual([row['variant_id'] for row in result['rows']], [self.variant.pk])
        self.assertFalse(stock_snapshot('rouge')['rows'])
        self.assertEqual(stock_snapshot('rouge')['counts']['total'], 1)

    def test_snapshot_excludes_duplicate_products_and_disabled_watch_is_not_low(self):
        duplicate = Product.objects.create(name='Polaire doublon', price=Decimal('80'), duplicate_of=self.product)
        Variant.objects.create(product=duplicate, size='L', color='Rouge', quantity=0)
        self.quantity(0)
        StockWatch.objects.create(variant=self.variant, enabled=False)
        result = stock_snapshot()
        self.assertEqual(result['counts']['total'], 1)
        self.assertEqual(result['counts']['disabled'], 1)
        self.assertEqual(result['low_count'], 0)

    def test_threshold_crossing_queues_once_then_rearms_after_restock(self):
        self.assertEqual(scan_low_stock(), 0)
        self.quantity(5, reserved=3)
        self.assertEqual(scan_low_stock(), 1)
        self.assertEqual(scan_low_stock(), 0)
        first = self.notices().get()
        self.assertTrue(stock_notice_is_current(first))
        self.assertEqual(first.payload['available'], 2)
        self.assertEqual(deliver_pending(), 1)
        self.assertEqual(deliver_pending(), 0)
        self.quantity(0)
        self.assertEqual(scan_low_stock(), 0)
        self.quantity(7)
        self.assertEqual(scan_low_stock(), 0)
        self.assertFalse(StockWatch.objects.get(variant=self.variant).was_low)
        self.quantity(3)
        self.assertEqual(scan_low_stock(), 1)
        self.assertEqual(self.notices().count(), 2)
        self.assertEqual(StockWatch.objects.get(variant=self.variant).episode, 2)
        self.assertEqual(deliver_pending(), 1)
        self.assertEqual(len(mail.outbox), 2)

    def test_pending_mail_is_cancelled_if_quantity_recovered_without_scan(self):
        self.quantity(2)
        scan_low_stock()
        notice = self.notices().get()
        self.quantity(4)
        self.assertFalse(stock_notice_is_current(notice))
        self.assertEqual(deliver_pending(), 0)
        notice.refresh_from_db()
        self.assertIsNotNone(notice.cancelled_at)
        self.assertEqual(len(mail.outbox), 0)

    def test_unknown_hidden_disabled_and_deleted_variants_cannot_send_stale_alerts(self):
        for condition in ('unknown', 'hidden', 'disabled', 'deleted'):
            with self.subTest(condition=condition):
                variant = Variant.objects.create(product=self.product, size=condition, color='Bleu', quantity=1)
                scan_low_stock()
                notice = self.notices().get(variant=variant)
                if condition == 'unknown':
                    self.quantity(None, variant=variant)
                elif condition == 'hidden':
                    Product.objects.filter(pk=self.product.pk).update(is_published=False)
                elif condition == 'disabled':
                    StockWatch.objects.filter(variant=variant).update(enabled=False)
                else:
                    variant.delete()
                    notice.refresh_from_db()
                self.assertFalse(stock_notice_is_current(notice))
                self.assertEqual(deliver_pending(notice_id=notice.pk), 0)
                notice.refresh_from_db()
                self.assertIsNotNone(notice.cancelled_at)
                Product.objects.filter(pk=self.product.pk).update(is_published=True)
        self.assertEqual(len(mail.outbox), 0)

    def test_config_change_uses_new_snapshot_without_sending_to_old_recipient(self):
        self.quantity(2)
        scan_low_stock()
        old = self.notices().get()
        self.preferences.recipient = 'new-seller@example.test'
        self.preferences.revision += 1
        self.preferences.stock_generation += 1
        self.preferences.save()
        self.assertFalse(stock_notice_is_current(old))
        self.assertEqual(scan_low_stock(), 1)
        self.assertEqual(deliver_pending(), 1)
        old.refresh_from_db()
        self.assertIsNotNone(old.cancelled_at)
        self.assertEqual(mail.outbox[0].to, ['new-seller@example.test'])

    def test_enabling_mail_after_visual_observation_does_not_consume_alert(self):
        self.preferences.stock_notifications_enabled = False
        self.preferences.save()
        self.quantity(1)
        self.assertEqual(scan_low_stock(), 0)
        self.assertTrue(StockWatch.objects.get(variant=self.variant).was_low)
        self.assertFalse(self.notices().exists())
        self.preferences.stock_notifications_enabled = True
        self.preferences.revision += 1
        self.preferences.stock_generation += 1
        self.preferences.save()
        self.assertEqual(scan_low_stock(), 1)
        self.assertEqual(scan_low_stock(), 0)

    def test_custom_threshold_zero_and_restoring_default(self):
        self.quantity(2)
        watch = update_rule(self.seller, self.variant.pk, threshold=0, enabled=True, revision=1)
        self.assertEqual(watch.revision, 2)
        self.assertFalse(stock_snapshot()['rows'][0]['is_low'])
        self.assertEqual(scan_low_stock(), 0)
        self.quantity(0)
        self.assertEqual(scan_low_stock(), 1)
        old = self.notices().get()
        watch = update_rule(self.seller, self.variant.pk, threshold=None, enabled=True, revision=2)
        old.refresh_from_db()
        self.assertIsNotNone(old.cancelled_at)
        self.assertEqual(stock_snapshot()['rows'][0]['threshold'], 3)
        self.assertEqual(scan_low_stock(), 1)
        self.assertEqual(AlertEvent.objects.filter(kind='stock_rule').count(), 2)

    def test_update_rule_does_not_mutate_stock_and_requires_permissions_and_revision(self):
        self.quantity(5, 2)
        self.variant.refresh_from_db()
        before = Variant.objects.values().get(pk=self.variant.pk)
        watch = update_rule(self.seller, self.variant.pk, threshold=9, enabled=False, revision=1)
        self.assertEqual(Variant.objects.values().get(pk=self.variant.pk), before)
        self.assertEqual(watch.revision, 2)
        with self.assertRaises(ValidationError):
            update_rule(self.seller, self.variant.pk, threshold=3, enabled=True, revision=1)
        self.seller.user_permissions.clear()
        self.seller = get_user_model().objects.get(pk=self.seller.pk)
        with self.assertRaises(PermissionDenied):
            update_rule(self.seller, self.variant.pk, threshold=3, enabled=True, revision=2)
        self.assertEqual(AlertEvent.objects.count(), 1)

    def test_rule_invalid_values_never_write(self):
        for threshold in (-1, 100000, True, '3'):
            with self.subTest(threshold=threshold), self.assertRaises(ValidationError):
                update_rule(self.seller, self.variant.pk, threshold, True, 1)
        self.assertFalse(StockWatch.objects.exists())
        self.assertFalse(AlertEvent.objects.exists())

    def test_scan_limit_never_starves_later_variants_and_still_rearms(self):
        self.quantity(2)
        second = Variant.objects.create(product=self.product, size='L', color='Rouge', quantity=1)
        third = Variant.objects.create(product=self.product, size='XL', color='Rouge', quantity=0)
        self.assertEqual(scan_low_stock(limit=1), 1)
        self.assertEqual(scan_low_stock(limit=1), 1)
        self.assertEqual(scan_low_stock(limit=1), 1)
        self.assertEqual(scan_low_stock(limit=1), 0)
        self.assertEqual(self.notices().count(), 3)
        self.quantity(8, variant=third)
        scan_low_stock(limit=1)
        self.assertFalse(StockWatch.objects.get(variant=third).was_low)
        self.assertIsNotNone(self.notices().get(variant=third).cancelled_at)

    def test_unknown_quantity_never_creates_stock_alert_or_false_rupture(self):
        self.quantity(None)
        self.assertEqual(scan_low_stock(), 0)
        self.assertFalse(StockWatch.objects.exists())
        row = stock_snapshot()['rows'][0]
        self.assertIsNone(row['available'])
        self.assertFalse(row['is_low'])
        self.assertEqual(row['state'], 'unknown')

    def test_disabling_rule_cancels_pending_notice_and_reenable_can_alert_again(self):
        self.quantity(2)
        scan_low_stock()
        notice = self.notices().get()
        update_rule(self.seller, self.variant.pk, threshold=None, enabled=False, revision=1)
        notice.refresh_from_db()
        self.assertIsNotNone(notice.cancelled_at)
        update_rule(self.seller, self.variant.pk, threshold=None, enabled=True, revision=2)
        self.assertEqual(scan_low_stock(), 1)
        self.assertEqual(StockWatch.objects.get(variant=self.variant).episode, 2)

    def settings_data(self, **extra):
        self.preferences.refresh_from_db()
        values = {name: getattr(self.preferences, name) for name in (
            'revision', 'recipient', 'order_notifications_enabled',
            'stock_notifications_enabled', 'stock_threshold')}
        values.update(extra)
        return values

    def enable_configuration_access(self):
        self.seller.user_permissions.add(Permission.objects.get(content_type__app_label='retours', codename='manage_returns'))
        self.seller = get_user_model().objects.get(pk=self.seller.pk)

    def test_paid_order_toggle_preserves_pending_stock_and_never_resends_current_episode(self):
        from .settings_service import save_settings
        self.enable_configuration_access()
        self.quantity(2)
        scan_low_stock()
        notice = self.notices().get()
        before_generation = self.preferences.stock_generation
        changed = save_settings(self.seller, self.settings_data(order_notifications_enabled=True))
        self.assertEqual(changed.stock_generation, before_generation)
        self.assertTrue(stock_notice_is_current(notice))
        self.assertEqual(scan_low_stock(), 0)
        self.assertEqual(deliver_pending(), 1)
        save_settings(self.seller, self.settings_data(order_notifications_enabled=False))
        self.assertEqual(scan_low_stock(), 0)
        self.assertEqual(deliver_pending(), 0)
        self.assertEqual(self.notices().count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_relevant_global_threshold_change_advances_stock_generation(self):
        from .settings_service import save_settings
        self.enable_configuration_access()
        self.quantity(2)
        scan_low_stock()
        old = self.notices().get()
        generation = self.preferences.stock_generation
        changed = save_settings(self.seller, self.settings_data(stock_threshold=4))
        self.assertEqual(changed.stock_generation, generation + 1)
        self.assertFalse(stock_notice_is_current(old))
        self.assertEqual(scan_low_stock(), 1)
        self.assertEqual(deliver_pending(), 1)
        old.refresh_from_db()
        self.assertIsNotNone(old.cancelled_at)

    def test_sender_observes_recovery_between_scans_and_next_low_episode_can_alert(self):
        self.quantity(2)
        scan_low_stock()
        old = self.notices().get()
        self.quantity(8)
        self.assertEqual(deliver_pending(), 0)
        old.refresh_from_db()
        self.assertIsNotNone(old.cancelled_at)
        self.quantity(2)
        self.assertEqual(scan_low_stock(), 1)
        self.assertEqual(scan_low_stock(), 0)
        self.assertEqual(self.notices().count(), 2)
        self.assertEqual(StockWatch.objects.get(variant=self.variant).episode, 2)
        self.assertEqual(deliver_pending(), 1)

    def test_products_without_variants_are_reported_without_changing_variant_counts(self):
        missing = Product.objects.create(name='Veste sans stock configuré', price=Decimal('70'), is_published=True)
        Product.objects.create(name='Brouillon sans stock', price=Decimal('70'), is_published=False)
        Product.objects.create(name='Ancien doublon sans stock', price=Decimal('70'),
            is_published=True, duplicate_of=missing)
        unknown = Product.objects.create(name='Quantité à compter', price=Decimal('70'), is_published=True)
        Variant.objects.create(product=unknown, size='M', color='Bleu', quantity=None)
        result = stock_snapshot()
        self.assertEqual([product.pk for product in result['missing_products']], [missing.pk])
        self.assertEqual(result['missing_products_count'], 1)
        self.assertEqual(result['counts'], {'low': 0, 'out': 0, 'unknown': 1, 'disabled': 0, 'normal': 1, 'total': 2})
        self.assertEqual(result['unknown_count'], 1)
        self.assertFalse(StockWatch.objects.exists())
        self.assertFalse(AlertNotice.objects.exists())
        self.assertFalse(AlertEvent.objects.exists())
        self.assertFalse(missing.stock_variants.exists())

    def test_missing_product_search_ignores_accents_and_preserves_global_total(self):
        matching = Product.objects.create(name='Veste de l’Écureuil', brand='Alpina', price=Decimal('70'))
        Product.objects.create(name='Pantalon de randonnée', brand='Montagne', price=Decimal('90'))
        result = stock_snapshot('ecureuil alpina')
        self.assertEqual([product.pk for product in result['missing_products']], [matching.pk])
        self.assertEqual(result['missing_products_count'], 2)
        self.assertFalse(result['rows'])
        self.assertEqual(result['counts']['total'], 1)
        self.assertFalse(stock_snapshot('introuvable')['missing_products'])
        self.assertEqual(stock_snapshot('introuvable')['missing_products_count'], 2)
