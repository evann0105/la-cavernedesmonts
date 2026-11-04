"""Real row-lock checks; SQLite cannot validate production serialization."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase, override_settings

from connexion.test_helpers import verify_test_user
from core.models import Product
from stocks.models import Variant
from historique.exceptions import HistoryConflict
from historique.models import HistoryVersion, RestorationReceipt
from historique.products import record_product, snapshot_product, restore_product, product_change
from historique.services import fingerprint, prepare_restore, apply_restore


@skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL row locks')
@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ProductHistoryConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser('history-race', 'history-race@example.test', 'unused')
        verify_test_user(self.actor)
        self.product = Product.objects.create(name='Ancien nom', price='40', is_published=False, include_imported_gallery=False)
        self.variant = Variant.objects.create(product=self.product, size='M', color='Vert', quantity=10, reserved=2)
        self.version = record_product(self.product, actor=self.actor, source='baseline')
        self.product.name = 'Nom actuel'; self.product.save(update_fields=['name'])
        self.digest = fingerprint(snapshot_product(self.product))

    def threaded(self, callback):
        def worker():
            close_old_connections()
            try:
                return callback(get_user_model().objects.get(pk=self.actor.pk))
            finally:
                close_old_connections()
        return worker

    def test_double_confirmation_creates_one_restoration_and_receipt(self):
        token = prepare_restore(self.actor, self.version)['token']
        barrier = Barrier(2)
        def apply(actor):
            barrier.wait(timeout=5)
            return apply_restore(actor, token).pk
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result(timeout=15) for future in [pool.submit(self.threaded(apply)), pool.submit(self.threaded(apply))]]
        self.assertEqual(results[0], results[1])
        self.assertEqual(RestorationReceipt.objects.count(), 1)
        self.assertEqual(HistoryVersion.objects.filter(source='restore').count(), 1)
        self.variant.refresh_from_db(); self.assertEqual((self.variant.quantity, self.variant.reserved), (10, 2))

    def test_sale_finishing_while_restore_waits_preserves_sold_quantity(self):
        locked, finish = Event(), Event()
        def sale(actor):
            with transaction.atomic():
                variant = Variant.objects.select_for_update().get(pk=self.variant.pk)
                locked.set()
                if not finish.wait(5): raise RuntimeError('restore did not start')
                variant.quantity -= 2; variant.reserved -= 2; variant.revision += 1
                variant.save(update_fields=['quantity', 'reserved', 'revision', 'updated_at'])
        def restore(actor):
            if not locked.wait(5): raise RuntimeError('sale did not lock')
            finish.set()
            return restore_product(self.version, actor=actor, expected_fingerprint=self.digest).pk
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(self.threaded(sale)); second=pool.submit(self.threaded(restore))
            first.result(timeout=15); second.result(timeout=15)
        self.variant.refresh_from_db(); self.product.refresh_from_db()
        self.assertEqual((self.variant.quantity, self.variant.reserved), (8, 0))
        self.assertEqual(self.product.name, 'Ancien nom')

    def test_metadata_writer_wins_before_restore_rechecks_expected_state(self):
        locked, ready = Event(), Event()
        def edit(actor):
            with product_change(self.product.pk, actor=actor) as product:
                product.name = 'Saisie récente'; product.save(update_fields=['name'])
                locked.set()
                if not ready.wait(5): raise RuntimeError('restore did not start')
        def restore(actor):
            if not locked.wait(5): raise RuntimeError('edit did not lock')
            ready.set()
            try:
                restore_product(self.version, actor=actor, expected_fingerprint=self.digest)
            except HistoryConflict:
                return 'conflict'
            return 'unexpected'
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(self.threaded(edit)); second=pool.submit(self.threaded(restore))
            first.result(timeout=15); result=second.result(timeout=15)
        self.assertEqual(result, 'conflict')
        self.product.refresh_from_db(); self.assertEqual(self.product.name, 'Saisie récente')
        self.assertFalse(HistoryVersion.objects.filter(source='restore').exists())
