from copy import deepcopy
from datetime import datetime, timezone as dt_timezone
from types import SimpleNamespace
import uuid

from django.test import SimpleTestCase, TestCase

from paiement.models import Order
from retours.models import ReturnRequest
from .accounting import report, sales_csv
from .accounting_search import filter_report, normalize_query
from .models import Invoice


class AccountingSearchTests(SimpleTestCase):
    def setUp(self):
        self.order = SimpleNamespace(pk=uuid.UUID('67b71d16-1857-4415-9d08-11e0dbf439ec'),
            billing_details={'name': 'Contact changé'}, customer_email='nouveau@example.test')
        self.other_order = SimpleNamespace(pk=uuid.UUID('1ef881a6-6d5a-4ca0-ac80-3ca1e23bcfee'),
            billing_details={'name': 'Chloé À compter'}, customer_email='chloe@example.test')
        self.refund = SimpleNamespace(pk=uuid.UUID('a4beaf8b-bf6d-41de-9e70-a177e0932492'))
        self.invoice = SimpleNamespace(pk=1, number='WEB-2026-000017', order_id=self.order.pk,
            snapshot={'buyer': {'name': 'Élodie Noël', 'email': 'elodie.noel@example.test'}})
        self.payment = {'kind': 'payment', 'order': self.order, 'refund': None,
            'customer_name': 'Élodie Noël', 'customer_email': 'elodie.noel@example.test',
            'reference': str(self.order.pk), 'invoice_number': self.invoice.number,
            'notes': 'PRIVATE-NOTE not a search field', 'amount_cents': 8000}
        self.refund_row = {**self.payment, 'kind': 'refund', 'reference': str(self.refund.pk),
            'refund': self.refund, 'amount_cents': -2000}
        self.other_row = {'kind': 'payment', 'order': self.other_order, 'refund': None,
            'customer_name': 'Chloé À compter', 'customer_email': 'chloe@example.test',
            'reference': str(self.other_order.pk), 'invoice_number': '', 'amount_cents': 4000}
        self.data = {'rows': [self.payment, self.refund_row, self.other_row], 'invoices': [self.invoice],
            'missing_invoice_orders': [self.other_order], 'summary': {'net_cents': 10000},
            'period': {'slug': '2026-10'}, 'warnings': ['Situation à vérifier']}

    def test_no_filter_returns_new_lists_in_original_order(self):
        result = filter_report(self.data)
        self.assertFalse(result['is_filtered'])
        for key in ('rows', 'invoices', 'missing_invoice_orders'):
            self.assertEqual(result[key], self.data[key])
            self.assertIsNot(result[key], self.data[key])
        self.assertNotIn('summary', result)

    def test_name_matching_ignores_case_accents_and_supports_multiple_words(self):
        result = filter_report(self.data, '  ELODIE NOEL  ')
        self.assertEqual(result['query'], 'ELODIE NOEL')
        self.assertTrue(result['is_filtered'])
        self.assertEqual(result['rows'], [self.payment, self.refund_row])
        self.assertEqual(result['invoices'], [self.invoice])
        self.assertEqual(result['missing_invoice_orders'], [])
        self.assertEqual(filter_report(self.data, 'NOEL élodie')['rows'], [self.payment, self.refund_row])

    def test_email_and_invoice_number_match_their_frozen_fields(self):
        self.assertEqual(filter_report(self.data, 'ELODIE.NOEL@EXAMPLE.TEST')['rows'], [self.payment, self.refund_row])
        result = filter_report(self.data, 'web-2026-000017')
        self.assertEqual(result['rows'], [self.payment, self.refund_row])
        self.assertEqual(result['invoices'], [self.invoice])
        self.assertEqual(result['missing_invoice_orders'], [])

    def test_full_short_and_unhyphenated_order_reference_match(self):
        for reference in (str(self.order.pk), str(self.order.pk)[:8].upper(), self.order.pk.hex):
            with self.subTest(reference=reference):
                result = filter_report(self.data, reference)
                self.assertEqual(result['rows'], [self.payment, self.refund_row])
                self.assertEqual(result['invoices'], [self.invoice])

    def test_full_and_short_refund_reference_match_only_that_movement(self):
        for reference in (str(self.refund.pk), str(self.refund.pk)[:8].upper(), self.refund.pk.hex):
            with self.subTest(reference=reference):
                result = filter_report(self.data, reference)
                self.assertEqual(result['rows'], [self.refund_row])
                self.assertEqual(result['invoices'], [])

    def test_kind_restricts_only_movements_and_composes_with_query(self):
        result = filter_report(self.data, kind='refund')
        self.assertEqual(result['rows'], [self.refund_row])
        self.assertEqual(result['invoices'], [self.invoice])
        self.assertEqual(result['missing_invoice_orders'], [self.other_order])
        self.assertEqual(result['kind'], 'refund')
        self.assertEqual(filter_report(self.data, 'elodie', 'payment')['rows'], [self.payment])
        self.assertEqual(filter_report(self.data, 'chloe', 'refund')['rows'], [])

    def test_invalid_kind_is_ignored_without_excluding_everything(self):
        for kind in ('all', 'other', None, '<script>'):
            with self.subTest(kind=kind):
                result = filter_report(self.data, kind=kind)
                self.assertEqual(result['kind'], '')
                self.assertEqual(result['rows'], self.data['rows'])

    def test_invoice_identity_never_matches_current_order_contacts(self):
        for value in ('Contact changé', 'nouveau@example.test'):
            with self.subTest(value=value):
                result = filter_report(self.data, value)
                self.assertEqual(result['rows'], [])
                self.assertEqual(result['invoices'], [])
        self.assertEqual(filter_report(self.data, 'elodie')['invoices'], [self.invoice])

    def test_missing_invoice_search_uses_order_billing_identity_and_email(self):
        for value in ('chloe', 'A COMPTER', 'CHLOE@EXAMPLE.TEST', str(self.other_order.pk)[:8]):
            with self.subTest(value=value):
                self.assertEqual(filter_report(self.data, value)['missing_invoice_orders'], [self.other_order])

    def test_symbols_are_literal_never_sql_or_regular_expression_patterns(self):
        for value in ('%', '_', '.*', "' OR 1=1 --", '<script>', 'PRIVATE-NOTE'):
            with self.subTest(value=value):
                result = filter_report(self.data, value)
                self.assertEqual(result['rows'], [])
                self.assertEqual(result['invoices'], [])
                self.assertEqual(result['missing_invoice_orders'], [])
        self.other_row['customer_name'] = 'Client 100%_.*'
        self.assertEqual(filter_report(self.data, '100%_.*')['rows'], [self.other_row])

    def test_query_is_trimmed_and_bounded_before_matching(self):
        self.assertEqual(normalize_query(None), '')
        self.assertEqual(normalize_query(' \nélodie\t '), 'élodie')
        self.assertEqual(len(normalize_query(' ' + 'x' * 250 + ' ')), 100)
        self.assertEqual(filter_report(self.data, 'x' * 250)['query'], 'x' * 100)

    def test_malformed_snapshot_does_not_read_current_buyer_as_fallback(self):
        for snapshot in (None, [], {'buyer': None}, {'buyer': []}):
            with self.subTest(snapshot=snapshot):
                self.invoice.snapshot = snapshot
                self.assertEqual(filter_report(self.data, 'nouveau@example.test')['invoices'], [])
                self.assertEqual(filter_report(self.data, self.invoice.number)['invoices'], [self.invoice])

    def test_filtering_never_mutates_report_or_recalculates_global_totals(self):
        before = deepcopy(self.data)
        result = filter_report(self.data, 'elodie', 'refund')
        result['rows'].clear()
        result['invoices'].clear()
        result['missing_invoice_orders'].clear()
        self.assertEqual(self.data, before)
        self.assertEqual(self.data['summary']['net_cents'], 10000)


class AccountingSearchReportTests(TestCase):
    def order(self, name, paid_at):
        return Order.objects.create(session_key='accounting-search', status='paid', paid_at=paid_at,
            customer_email=name.lower() + '@example.test', billing_details={'name': name},
            items=[{'name':'Polaire', 'quantity':1, 'unit_amount':8000}], subtotal=8000,
            paid_line_totals=[8000], total_paid=8000, return_shipping_amount=0)

    def test_search_only_uses_loaded_month_and_does_not_change_complete_export(self):
        current = self.order('Élodie', datetime(2026,10,12,tzinfo=dt_timezone.utc))
        other = self.order('Chloé', datetime(2026,10,13,tzinfo=dt_timezone.utc))
        outside = self.order('Septembre', datetime(2026,9,13,tzinfo=dt_timezone.utc))
        ReturnRequest.objects.create(order=current, submission_id=uuid.uuid4(), amount=2000,
            refund_state='succeeded', refunded_at=datetime(2026,10,15,tzinfo=dt_timezone.utc))
        data = report(2026, 10)
        original_export = sales_csv(data)
        with self.assertNumQueries(0):
            filtered = filter_report(data, 'elodie', 'refund')
            not_in_month = filter_report(data, str(outside.pk))
        self.assertEqual(len(filtered['rows']), 1)
        self.assertEqual(filtered['rows'][0]['kind'], 'refund')
        self.assertEqual(filtered['rows'][0]['order'].pk, current.pk)
        self.assertEqual(not_in_month['rows'], [])
        self.assertEqual(not_in_month['missing_invoice_orders'], [])
        self.assertEqual(sales_csv(data), original_export)
        self.assertIn(str(other.pk).encode(), original_export)
        self.assertNotIn(str(outside.pk).encode(), original_export)
        self.assertEqual(data['summary']['net_cents'], 14000)

    def test_issued_invoice_search_uses_recorded_identity_without_extra_queries(self):
        order = self.order('Ancienne', datetime(2026,9,12,tzinfo=dt_timezone.utc))
        invoice = Invoice.objects.create(order=order, number='WEB-2026-000099',
            issued_at=datetime(2026,10,12,tzinfo=dt_timezone.utc),
            snapshot={'buyer':{'name':'Élodie Noël','email':'ancien@example.test'}})
        Order.objects.filter(pk=order.pk).update(billing_details={'name':'Aujourd’hui'}, customer_email='nouveau@example.test')
        data = report(2026,10)
        with self.assertNumQueries(0):
            self.assertEqual(filter_report(data, 'elodie noel')['invoices'], [invoice])
            self.assertEqual(filter_report(data, 'ANCIEN@EXAMPLE.TEST')['invoices'], [invoice])
            self.assertEqual(filter_report(data, 'nouveau@example.test')['invoices'], [])
            self.assertEqual(filter_report(data, 'aujourdhui')['invoices'], [])
