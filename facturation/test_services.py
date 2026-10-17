from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from paiement.models import Order
from .models import Invoice, InvoiceSequence
from .profile import blockers, capture_profile
from .services import DocumentError, build_preview, issue


LEGAL = {
    'business_name': 'Élodie FOLOPPE', 'legal_form': 'Entrepreneur individuel (EI)',
    'siren': '525 114 914', 'siret': '525 114 914 00019',
    'registration': '525 114 914 RCS Bourg-en-Bresse',
    'address': '225 rue des Monts-Jura, 01410 Lélex, France',
    'email': 'contact@example.test', 'phone': '04 00 00 00 00',
    'vat_notice': 'TVA applicable au taux validé de 20 %.',
    'vat_number': 'FR55 525114914',
}
INVOICING = {
    'vat_mode': 'standard', 'vat_rate': '20.00', 'numbering_confirmed': True,
    'issue_policy_confirmed': True, 'ready': True,
}


@override_settings(SHOP_LEGAL=LEGAL, SHOP_INVOICING=INVOICING, DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True)
class InvoiceServiceTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser('invoice-manager', email='manager@example.test', password='test-password')
        self.buyer = {'name': 'Client Test', 'address': {'line1': '10 rue du Test', 'line2': '', 'city': 'Lyon', 'postal_code': '69001', 'country': 'FR'}}
        self.order = self.make_order()

    def make_order(self, **changes):
        data = {
            'session_key': 'invoice-test', 'status': 'paid', 'paid_at': timezone.now(),
            'customer_email': 'client@example.test', 'shipping_details': deepcopy(self.buyer),
            'billing_details': deepcopy(self.buyer), 'invoice_profile': capture_profile(),
            'items': [
                {'name': 'Polaire', 'quantity': 2, 'unit_amount': 3600, 'size': 'M', 'color': 'Beige'},
                {'name': 'Écharpe', 'quantity': 1, 'unit_amount': 2000, 'size': 'Unique', 'color': 'Rouge'},
            ],
            'subtotal': 9200, 'paid_line_totals': [6001, 2000],
            'return_shipping_amount': 600, 'total_paid': 8601,
        }
        data.update(changes)
        return Order.objects.create(**data)

    def test_profile_requires_explicit_confirmations_and_no_default_tax_rate(self):
        self.assertEqual(blockers(), [])
        self.assertEqual(capture_profile()['vat_rate'], '20.00')
        for key in ('ready', 'numbering_confirmed', 'issue_policy_confirmed', 'vat_rate'):
            config = {k: v for k, v in INVOICING.items() if k != key}
            with self.subTest(key=key), override_settings(SHOP_INVOICING=config):
                self.assertTrue(blockers())
                self.assertEqual(capture_profile(), {})
        with override_settings(SHOP_LEGAL={**LEGAL, 'vat_number': ''}):
            self.assertEqual(capture_profile(), {})

    def test_preview_keeps_single_item_discount_and_balances_tax(self):
        data = build_preview(self.order)
        self.assertTrue(data['final_ready'], data['blockers'])
        self.assertTrue(data['amounts_verified'])
        self.assertEqual(data['lines'][0]['net'], Decimal('60.01'))
        self.assertEqual(data['lines'][1]['net'], Decimal('20.00'))
        self.assertEqual(data['lines'][0]['discount'], Decimal('11.99'))
        self.assertEqual(data['lines'][1]['discount'], Decimal('0'))
        self.assertEqual(data['discount'], Decimal('11.99'))
        self.assertEqual(data['tax_base'] + data['tax_amount'], data['total'])
        self.assertEqual(sum(row['vat'] for row in data['lines']) + data['shipping_vat'], data['tax_amount'])
        self.assertFalse(Invoice.objects.exists())
        self.assertFalse(InvoiceSequence.objects.exists())

    def test_missing_historical_profile_has_no_invented_tax_or_fiscal_number(self):
        self.order.invoice_profile = {}
        self.order.save(update_fields=['invoice_profile'])
        data = build_preview(self.order)
        self.assertEqual(data['total'], Decimal('86.01'))
        self.assertIsNone(data['tax_base'])
        self.assertIsNone(data['tax_amount'])
        self.assertFalse(data['final_ready'])
        with self.assertRaises(DocumentError):
            issue(self.order.pk, self.admin)
        self.assertFalse(Invoice.objects.exists())
        self.assertFalse(InvoiceSequence.objects.exists())

    def test_incomplete_buyer_and_non_paid_or_demo_orders_cannot_be_issued(self):
        for changes in ({'billing_details': {}}, {'status': 'pending'}, {'status': 'expired'}, {'status': 'demo'}, {'paid_at': None}):
            with self.subTest(changes=changes):
                order = self.make_order(**changes)
                with self.assertRaises(DocumentError):
                    issue(order.pk, self.admin)
        self.assertFalse(Invoice.objects.exists())

    def test_unknown_or_incoherent_amounts_never_use_a_global_discount(self):
        cases = (
            {'paid_line_totals': None}, {'paid_line_totals': [6100, 1900]},
            {'paid_line_totals': [6001]}, {'paid_line_totals': [6001, True]},
            {'return_shipping_amount': None}, {'total_paid': 8600},
            {'paid_line_totals': [700, 7301]},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                order = self.make_order(**changes)
                self.assertFalse(build_preview(order)['amounts_verified'])
                with self.assertRaises(DocumentError):
                    issue(order.pk, self.admin)

    def test_exact_undiscounted_legacy_amounts_can_be_proved_without_stripe(self):
        order = self.make_order(paid_line_totals=None, total_paid=9800)
        self.assertTrue(build_preview(order)['final_ready'])
        invoice = issue(order.pk, self.admin)
        self.assertEqual(invoice.snapshot['discount'], '0')
        self.assertEqual(invoice.snapshot['total'], '98')

    def test_franchise_must_be_confirmed_and_has_no_deductible_vat(self):
        with override_settings(SHOP_INVOICING={**INVOICING, 'vat_mode': 'franchise', 'vat_rate': ''}, SHOP_LEGAL={**LEGAL, 'vat_number': '', 'vat_notice': 'TVA non applicable, art. 293 B du CGI.'}):
            order = self.make_order()
            data = build_preview(order)
            self.assertTrue(data['final_ready'], data['blockers'])
            self.assertEqual(data['vat_mode'], 'franchise')
            self.assertEqual(data['vat_rate'], '')
            self.assertEqual(data['tax_amount'], Decimal('0'))
            self.assertEqual(data['tax_base'], data['total'])

    def test_issue_is_idempotent_and_snapshot_is_encrypted(self):
        first = issue(self.order.pk, self.admin)
        again = issue(self.order.pk, self.admin)
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(first.number, f'WEB-{timezone.localdate().year}-000001')
        self.assertEqual(InvoiceSequence.objects.get().next_number, 2)
        self.assertEqual(first.snapshot['buyer']['name'], 'Client Test')
        with connection.cursor() as cursor:
            cursor.execute('SELECT snapshot FROM facturation_invoice WHERE id=%s', [first.pk])
            ciphertext = cursor.fetchone()[0]
        self.assertTrue(ciphertext.startswith('enc:v1:'))
        self.assertNotIn('Client Test', ciphertext)

    def test_existing_invoice_does_not_recalculate_after_order_or_config_changes(self):
        invoice = issue(self.order.pk, self.admin)
        original = deepcopy(invoice.snapshot)
        self.order.billing_details['name'] = 'Nom modifié'
        self.order.items[0]['name'] = 'Produit modifié'
        self.order.save(update_fields=['billing_details', 'items'])
        with override_settings(SHOP_LEGAL={}, SHOP_INVOICING={}):
            again = issue(self.order.pk, self.admin)
        self.assertEqual(again.snapshot, original)

    def test_historical_profile_is_used_even_if_current_configuration_changes(self):
        with override_settings(SHOP_INVOICING={}, SHOP_LEGAL={}):
            data = build_preview(self.order)
            self.assertTrue(data['final_ready'], data['blockers'])
            self.assertEqual(data['vat_rate'], '20.00')
            self.assertEqual(data['seller']['business_name'], 'Élodie FOLOPPE')

    def test_invalid_existing_sequence_is_reported_without_issuing_again(self):
        issue(self.order.pk, self.admin)
        InvoiceSequence.objects.update(next_number=1)
        with self.assertRaises(DocumentError):
            issue(self.make_order().pk, self.admin)
        self.assertEqual(Invoice.objects.count(), 1)

    def test_numbers_are_continuous_and_invoices_cannot_be_edited_or_deleted(self):
        first = issue(self.order.pk, self.admin)
        second = issue(self.make_order().pk, self.admin)
        self.assertTrue(second.number.endswith('-000002'))
        first.number = 'REWRITTEN'
        with self.assertRaises(ValidationError):
            first.save()
        with self.assertRaises(ValidationError):
            Invoice.objects.filter(pk=first.pk).update(number='REWRITTEN')
        with self.assertRaises(ValidationError):
            first.delete()
        with self.assertRaises(ValidationError):
            Invoice.objects.filter(pk=first.pk).delete()
        self.assertEqual(Invoice.objects.count(), 2)

    def test_issuance_requires_staff_permission_and_verified_email(self):
        user = get_user_model().objects.create_user('invoice-customer', email='customer@example.test')
        for actor in (None, user):
            with self.subTest(actor=actor), self.assertRaises(PermissionDenied):
                issue(self.order.pk, actor)
        with override_settings(DEBUG=False, LOCAL_SKIP_EMAIL_VERIFICATION=False):
            with self.assertRaises(PermissionDenied):
                issue(self.order.pk, self.admin)
        self.assertFalse(Invoice.objects.exists())


@override_settings(SHOP_LEGAL=LEGAL, SHOP_INVOICING=INVOICING, DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True)
class InvoiceConcurrencyTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Ce contrôle nécessite les verrous PostgreSQL.')
        self.admin = get_user_model().objects.create_superuser('concurrent-invoices', email='manager@example.test', password='test-password')
        buyer = {'name': 'Client Test', 'address': {'line1': '10 rue du Test', 'postal_code': '69001', 'city': 'Lyon', 'country': 'FR'}}
        self.orders = [Order.objects.create(
            session_key='concurrent-invoice', status='paid', paid_at=timezone.now(),
            customer_email='client@example.test', shipping_details=buyer, billing_details=buyer,
            invoice_profile=capture_profile(), items=[{'name': 'Polaire', 'quantity': 1, 'unit_amount': 3600}],
            subtotal=3600, total_paid=4200, return_shipping_amount=600, paid_line_totals=[3600],
        ) for _ in range(2)]

    def run_issue(self, order_id):
        close_old_connections()
        try:
            actor = get_user_model().objects.get(pk=self.admin.pk)
            invoice = issue(order_id, actor)
            return invoice.pk, invoice.number
        finally:
            close_old_connections()

    def test_concurrent_requests_for_same_order_issue_one_invoice(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(self.run_issue, [self.orders[0].pk, self.orders[0].pk]))
        self.assertEqual(result[0], result[1])
        self.assertEqual(Invoice.objects.count(), 1)
        self.assertEqual(InvoiceSequence.objects.get().next_number, 2)

    def test_concurrent_different_orders_receive_distinct_continuous_numbers(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(self.run_issue, [order.pk for order in self.orders]))
        numbers = sorted(number for _, number in result)
        year = timezone.localdate().year
        self.assertEqual(numbers, [f'WEB-{year}-000001', f'WEB-{year}-000002'])
        dated_numbers = list(Invoice.objects.order_by('issued_at').values_list('number', flat=True))
        self.assertEqual(dated_numbers, numbers)
