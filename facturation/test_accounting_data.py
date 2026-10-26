import csv
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from io import StringIO
import uuid
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from paiement.models import Order
from retours.models import ReturnRequest, ReturnLine, ReturnMail
from .accounting import period, report, sales_csv, invoice_index_csv, csv_text
from .models import Invoice, InvoiceSequence


def utc(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=dt_timezone.utc)


@override_settings(DEBUG=True, EMAIL_TEST_MODE=True)
class AccountingDataTests(TestCase):
    def order(self, **changes):
        values = dict(session_key='accounting-test', status='paid', paid_at=utc(2026, 10, 12),
            customer_email='client@example.test', billing_details={'name': 'Client enregistré'},
            items=[{'name': 'Polaire', 'quantity': 2, 'unit_amount': 4000}], subtotal=8000,
            total_paid=7700, paid_line_totals=[7200], return_shipping_amount=500)
        values.update(changes)
        return Order.objects.create(**values)

    def invoice(self, order, **changes):
        values = dict(order=order, number=f'WEB-2026-{Invoice.objects.count()+1:06d}', issued_at=utc(2026, 10, 20),
            snapshot={'number': f'WEB-2026-{Invoice.objects.count()+1:06d}', 'order_reference': str(order.pk),
                'buyer': {'name': 'Client facturé', 'email': 'facture@example.test'},
                'gross': '80.00', 'discount': '8.00', 'shipping_amount': '5.00', 'total': '77.00',
                'tax_base': '64.17', 'tax_amount': '12.83', 'vat_mode': 'standard', 'vat_rate': '20.00'})
        values.update(changes)
        return Invoice.objects.create(**values)

    def refund(self, order, amount=2300, shipping=300, **changes):
        values = dict(order=order, submission_id=uuid.uuid4(), amount=amount, shipping_amount=shipping,
            refunded_at=utc(2026, 10, 18), refund_state='succeeded')
        values.update(changes)
        refund = ReturnRequest.objects.create(**values)
        if amount >= shipping:
            ReturnLine.objects.create(request=refund, item_index=0, name='Polaire achetée', size='M', color='Vert',
                quantity=1, amount=amount-shipping)
        return refund

    def csv(self, content):
        self.assertTrue(content.startswith(b'\xef\xbb\xbf'))
        return list(csv.DictReader(StringIO(content.decode('utf-8-sig')), delimiter=';'))

    def test_period_is_french_calendar_month_even_across_dst_and_year_end(self):
        data = period('2026', '10')
        self.assertEqual(data['label'], 'Octobre 2026')
        self.assertEqual(data['slug'], '2026-10')
        self.assertEqual(data['start'].utcoffset(), timedelta(hours=2))
        self.assertEqual(data['end'].utcoffset(), timedelta(hours=1))
        self.assertEqual(period(2026, 12)['end'].year, 2027)
        self.assertEqual(period(2100, 12)['end'].year, 2101)
        for year, month in ((True, 1), (2026.0, 1), (1999, 1), (2101, 1), (2026, 0), (2026, 13), (None, 1), ('2026;DROP', 1)):
            with self.subTest(year=year, month=month), self.assertRaises(ValidationError):
                period(year, month)

    @override_settings(TIME_ZONE='UTC')
    def test_half_open_local_month_boundaries_do_not_depend_on_active_timezone(self):
        before = self.order(paid_at=utc(2026, 9, 30, 21, 59))
        first = self.order(paid_at=utc(2026, 9, 30, 22))
        last = self.order(paid_at=utc(2026, 10, 31, 22, 59))
        after = self.order(paid_at=utc(2026, 10, 31, 23))
        rows = report(2026, 10)['rows']
        self.assertEqual([row['order'].pk for row in rows], [first.pk, last.pk])
        self.assertNotIn(before.pk, [row['order'].pk for row in rows])
        self.assertNotIn(after.pk, [row['order'].pk for row in rows])
        self.assertEqual(self.csv(sales_csv(report(2026, 10)))[0]['Date opération (Europe/Paris)'], '01/10/2026 00:00:00')

    def test_cash_movements_separate_old_order_refund_and_invoice_issuance_month(self):
        old = self.order(paid_at=utc(2026, 9, 12))
        old_invoice = self.invoice(old)
        current = self.order()
        later_invoice = self.invoice(current, issued_at=utc(2026, 11, 2))
        old_refund = self.refund(old)
        data = report(2026, 10)
        self.assertEqual({row['reference'] for row in data['rows']}, {str(current.pk), str(old_refund.pk)})
        self.assertEqual([inv.pk for inv in data['invoices']], [old_invoice.pk])
        self.assertEqual(data['summary']['payments_cents'], 7700)
        self.assertEqual(data['summary']['refunds_cents'], 2300)
        self.assertEqual(data['summary']['net_cents'], 5400)
        self.assertEqual(data['summary']['net'], Decimal('54.00'))
        self.assertEqual(data['summary']['invoice_count'], 1)
        self.assertEqual(next(row for row in data['rows'] if row['kind']=='payment')['invoice_number'], later_invoice.number)

    def test_multiple_partial_refunds_include_shipping_once_and_balance(self):
        order = self.order()
        first = self.refund(order, amount=2300, shipping=300)
        second = self.refund(order, amount=1500, shipping=0)
        data = report(2026, 10)
        self.assertEqual(data['summary']['net_cents'], 7700 - 2300 - 1500)
        rows = {row['reference']: row for row in data['rows']}
        self.assertEqual(rows[str(first.pk)]['amount_cents'], -2300)
        self.assertEqual(rows[str(first.pk)]['shipping_cents'], -300)
        self.assertEqual(rows[str(second.pk)]['amount_cents'], -1500)
        self.assertEqual(rows[str(second.pk)]['shipping_cents'], 0)
        self.assertIsNone(rows[str(first.pk)]['tax_base'])
        self.assertIsNone(rows[str(first.pk)]['tax_amount'])

    def test_unconfirmed_refunds_and_demo_orders_never_become_cash_movements(self):
        paid = self.order()
        for state in ('none', 'submitting', 'pending', 'failed', 'review'):
            self.refund(paid, refund_state=state)
        for status in ('demo', 'pending', 'expired'):
            other = self.order(status=status)
            self.refund(other)
        data = report(2026, 10)
        self.assertEqual(data['summary']['payment_count'], 1)
        self.assertEqual(data['summary']['refund_count'], 0)
        self.assertEqual(data['summary']['payments_cents'], 7700)

    def test_zero_amount_paid_order_and_free_refund_are_valid(self):
        order = self.order(subtotal=8000, total_paid=0, paid_line_totals=[0], return_shipping_amount=0)
        refund = self.refund(order, amount=0, shipping=0)
        data = report(2026, 10)
        self.assertEqual(len(data['rows']), 2)
        self.assertEqual(data['summary']['payment_count'], 1)
        self.assertEqual(data['summary']['refund_count'], 1)
        self.assertEqual(data['summary']['net_cents'], 0)
        row = next(row for row in data['rows'] if row['kind']=='payment')
        self.assertEqual(row['discount_cents'], 8000)
        self.assertEqual(row['amount'], Decimal(0))
        self.assertEqual(next(row for row in data['rows'] if row['reference']==str(refund.pk))['amount_cents'], 0)

    def test_missing_payment_amount_or_date_is_not_replaced_by_subtotal_or_creation(self):
        missing_amount = self.order(total_paid=None)
        self.order(paid_at=None)
        self.order(status='pending', paid_at=None, total_paid=None)
        old_incomplete = self.order(paid_at=utc(2026, 8, 1), total_paid=None)
        self.refund(old_incomplete, refunded_at=None)
        data = report(2026, 10)
        self.assertEqual(data['rows'], [])
        self.assertEqual([order.pk for order in data['incomplete_orders']], [missing_amount.pk])
        self.assertEqual(data['undated_orders_count'], 1)
        self.assertEqual(data['undated_refunds_count'], 1)
        self.assertEqual(len(data['warnings']), 3)
        self.assertEqual(data['summary']['net_cents'], 0)

    def test_confirmed_refund_is_kept_if_original_payment_information_is_incomplete(self):
        order = self.order(paid_at=None, total_paid=None)
        self.refund(order)
        data = report(2026, 10)
        self.assertEqual(data['summary']['payment_count'], 0)
        self.assertEqual(data['summary']['refunds_cents'], 2300)
        self.assertIn('paiement d’origine incomplètes', data['rows'][0]['notes'])

    def test_undiscounted_legacy_breakdown_is_exact_but_tax_stays_unknown(self):
        order = self.order(total_paid=8500, paid_line_totals=None)
        row = report(2026, 10)['rows'][0]
        self.assertEqual(row['gross_cents'], 8000)
        self.assertEqual(row['discount_cents'], 0)
        self.assertEqual(row['shipping_cents'], 500)
        self.assertIsNone(row['tax_base'])
        self.assertIsNone(row['tax_amount'])
        self.assertIn('Facture non émise', row['notes'])

    def test_discount_shipping_unknown_allocation_never_invents_components(self):
        for changes in ({'paid_line_totals': None}, {'paid_line_totals': [7700]}, {'paid_line_totals': []},
                        {'return_shipping_amount': None}, {'items': []}, {'subtotal': 8100}):
            with self.subTest(changes=changes):
                order = self.order(**changes)
                row = next(row for row in report(2026, 10)['rows'] if row['order'].pk==order.pk)
                self.assertEqual(row['amount_cents'], 7700)
                self.assertIsNone(row['gross_cents'])
                self.assertIsNone(row['shipping_cents'])
                self.assertIsNone(row['discount_cents'])
                self.assertTrue(row['notes'])

    def test_refund_incoherent_allocation_keeps_total_without_guessing_shipping(self):
        order = self.order()
        refund = self.refund(order)
        ReturnLine.objects.filter(request=refund).update(amount=999)
        row = next(row for row in report(2026, 10)['rows'] if row['kind']=='refund')
        self.assertEqual(row['amount_cents'], -2300)
        self.assertIsNone(row['shipping_cents'])
        self.assertIn('Répartition du remboursement', row['notes'])

    def test_issued_snapshot_identity_tax_and_prices_survive_later_order_edits(self):
        order = self.order()
        invoice = self.invoice(order)
        Order.objects.filter(pk=order.pk).update(billing_details={'name':'Nom actuel'}, customer_email='changed@example.test',
            items=[{'name': 'Produit actuel', 'quantity': 1, 'unit_amount': 7700}], subtotal=7700, paid_line_totals=[7700], return_shipping_amount=0)
        with override_settings(SHOP_LEGAL={}, SHOP_INVOICING={}):
            row = report(2026, 10)['rows'][0]
        self.assertEqual(row['customer_name'], 'Client facturé')
        self.assertEqual(row['customer_email'], 'facture@example.test')
        self.assertEqual(row['gross_cents'], 8000)
        self.assertEqual(row['shipping_cents'], 500)
        self.assertEqual(row['discount_cents'], 800)
        self.assertEqual(row['tax_base'], Decimal('64.17'))
        self.assertEqual(row['tax_amount'], Decimal('12.83'))
        self.assertEqual(row['invoice'].pk, invoice.pk)

    def test_unknown_or_incoherent_invoice_tax_never_becomes_zero(self):
        for changes in ({'tax_base':None}, {'tax_amount':None}, {'tax_amount':'0'}, {'tax_amount':'NaN'},
                        {'tax_base':'Infinity'}, {'total':'99'}, {'vat_mode':''}, {'vat_rate':'-1'}, {'vat_rate':'0'}):
            with self.subTest(changes=changes):
                order = self.order()
                snapshot = {'number':f'WEB-2026-{Invoice.objects.count()+1:06d}','order_reference':str(order.pk), 'buyer':{}, 'gross':'80','discount':'8',
                    'shipping_amount':'5', 'total':'77', 'tax_base':'64.17', 'tax_amount':'12.83', 'vat_mode':'standard', 'vat_rate':'20.00', **changes}
                self.invoice(order, snapshot=snapshot)
                row = next(row for row in report(2026, 10)['rows'] if row['order'].pk==order.pk)
                self.assertIsNone(row['tax_base'])
                self.assertIsNone(row['tax_amount'])
                self.assertTrue(row['notes'])

    def test_confirmed_franchise_snapshot_has_exact_zero_tax(self):
        order = self.order()
        self.invoice(order, snapshot={'number':f'WEB-2026-{Invoice.objects.count()+1:06d}', 'order_reference':str(order.pk),
            'buyer':{}, 'total':'77', 'tax_base':'77', 'tax_amount':'0', 'vat_mode':'franchise', 'vat_rate':''})
        row = report(2026, 10)['rows'][0]
        self.assertEqual(row['tax_base'], Decimal('77'))
        self.assertEqual(row['tax_amount'], Decimal(0))
        self.assertEqual(row['vat_mode'], 'franchise')

    def test_missing_invoices_include_only_paid_month_and_no_issuance_occurs(self):
        without = self.order()
        issued = self.order()
        self.invoice(issued)
        self.order(paid_at=utc(2026, 9, 15))
        self.order(status='demo')
        self.assertFalse(InvoiceSequence.objects.exists())
        with patch('facturation.services.issue', side_effect=AssertionError('No issue')), patch('stripe.Refund.create', side_effect=AssertionError('No refund')):
            data = report(2026, 10)
            sales_csv(data)
            invoice_index_csv(data['invoices'])
        self.assertEqual([order.pk for order in data['missing_invoice_orders']], [without.pk])
        self.assertEqual(Invoice.objects.count(), 1)
        self.assertFalse(InvoiceSequence.objects.exists())
        self.assertFalse(ReturnMail.objects.exists())

    def test_sales_csv_has_exact_french_amounts_dates_no_private_address_or_provider_secret(self):
        order = self.order(billing_details={'name':'Élodie Dupont','address':{'line1':'Private address'}}, stripe_session_id='cs_secret_value')
        self.refund(order)
        rows = self.csv(sales_csv(report(2026, 10)))
        payment, refund = next(row for row in rows if row['Type opération']=='Paiement'), next(row for row in rows if row['Type opération']=='Remboursement')
        self.assertEqual(payment['Articles avant remise EUR'], '80,00')
        self.assertEqual(payment['Remise articles EUR'], '8,00')
        self.assertEqual(payment['Livraison EUR'], '5,00')
        self.assertEqual(payment['Montant mouvement EUR'], '77,00')
        self.assertEqual(refund['Montant mouvement EUR'], '-23,00')
        self.assertEqual(refund['Livraison EUR'], '-3,00')
        self.assertEqual(payment['TVA facture EUR'], '')
        self.assertEqual(payment['Client'], 'Élodie Dupont')
        self.assertEqual(payment['Devise'], 'EUR')
        exported = sales_csv(report(2026, 10)).decode('utf-8-sig')
        self.assertNotIn('Private address', exported)
        self.assertNotIn('cs_secret_value', exported)

    def test_csv_formula_like_text_is_neutralized_but_generated_negative_money_stays_numeric(self):
        order = self.order(customer_email='=private@example.test', billing_details={'name':'\n\t=HYPERLINK("bad")'})
        self.refund(order)
        rows = self.csv(sales_csv(report(2026, 10)))
        self.assertTrue(rows[0]['Client'].startswith("'"))
        self.assertTrue(rows[0]['E-mail client'].startswith("'"))
        self.assertEqual(rows[1]['Montant mouvement EUR'], '-23,00')
        for unsafe in ('=1+1', '+SUM(A1)', '-1', '@cmd', '\t=1', '\r\n+2', '\ufeff=2', '\x00@x',
                       '\ufeff \ufeff\t=HYPERLINK("bad")', '\u2003\ufeff\n\ufeff+1'):
            with self.subTest(unsafe=unsafe):
                self.assertTrue(csv_text(unsafe).startswith("'"))
        self.assertEqual(csv_text('Texte; avec "guillemets"'), 'Texte; avec "guillemets"')

    def test_incoherent_invoice_identity_keeps_cash_but_never_uses_its_breakdown(self):
        for reference in ('number', 'order_reference'):
            with self.subTest(reference=reference):
                order = self.order()
                number = f'WEB-2026-{Invoice.objects.count()+1:06d}'
                snapshot = {'number': number, 'order_reference':str(order.pk), 'buyer':{},
                    'gross':'80','discount':'8','shipping_amount':'5', 'total':'77',
                    'tax_base':'64.17','tax_amount':'12.83','vat_mode':'standard','vat_rate':'20.00'}
                snapshot[reference] = 'autre-document'
                self.invoice(order, snapshot=snapshot)
                row = next(row for row in report(2026, 10)['rows'] if row['order'].pk == order.pk)
                self.assertEqual(row['amount_cents'], 7700)
                for key in ('gross_cents', 'discount_cents', 'shipping_cents', 'tax_base', 'tax_amount'):
                    self.assertIsNone(row[key])
                self.assertIn('Références du document enregistré à vérifier', row['notes'])

    def test_invoice_index_is_issue_dated_and_signals_absent_or_incoherent_snapshots(self):
        order = self.order()
        invoice = self.invoice(order, issued_at=utc(2026, 10, 31, 22, 59))
        broken = self.invoice(self.order(), snapshot={'buyer': {'name': '+FORMULE'}, 'total': None})
        rows = self.csv(invoice_index_csv([invoice, broken]))
        self.assertEqual(rows[0]['Date émission (Europe/Paris)'], '31/10/2026 23:59:00')
        self.assertEqual(rows[0]['Total facture EUR'], '77,00')
        self.assertEqual(rows[0]['TVA EUR'], '12,83')
        self.assertEqual(rows[0]['Observations'], '')
        self.assertEqual(rows[1]['Total facture EUR'], '')
        self.assertEqual(rows[1]['TVA EUR'], '')
        self.assertTrue(rows[1]['Client'].startswith("'"))
        self.assertIn('vérifier', rows[1]['Observations'])

    def test_empty_month_produces_header_only_without_database_mutation(self):
        data = report(2026, 10)
        self.assertEqual(data['summary']['net'], Decimal(0))
        self.assertEqual(data['rows'], [])
        self.assertEqual(self.csv(sales_csv(data)), [])
        self.assertEqual(self.csv(invoice_index_csv(data['invoices'])), [])
        self.assertFalse(InvoiceSequence.objects.exists())
