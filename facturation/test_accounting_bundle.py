"""A monthly hand-off keeps documents, cash movements and omissions distinct."""
import csv
from copy import deepcopy
from datetime import datetime, timezone as datetime_timezone
from decimal import Decimal
from io import BytesIO, StringIO
from tempfile import SpooledTemporaryFile
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID
from zipfile import ZipFile

from django.test import SimpleTestCase

from .accounting import invoice_index_csv, period, sales_csv
from .accounting_exports import InvoiceArchiveError, dossier_archive, invoice_archive
from .services import DocumentError
from .test_pdf import sample_invoice


UTC = datetime_timezone.utc


def sample_order(reference='00000000-0000-4000-8000-000000000001'):
    return SimpleNamespace(pk=UUID(reference), paid_at=datetime(2026, 10, 1, 10, 1, tzinfo=UTC),
                           billing_details={'name': 'Client de démonstration'}, total_paid=6000)


def movement(order, *, invoice=None, kind='payment', cents=6000, notes=''):
    return {'kind': kind, 'date': datetime(2026, 10, 15, 12, tzinfo=UTC),
            'order': order, 'refund': None,
            'invoice': invoice, 'reference': 'refund-01' if kind == 'refund' else str(order.pk),
            'invoice_number': invoice.number if invoice else '', 'customer_name': 'Client de démonstration',
            'customer_email': 'client@example.test', 'amount_cents': cents, 'amount': Decimal(cents) / 100,
            'gross_cents': 6000 if kind == 'payment' else None,
            'discount_cents': 600 if kind == 'payment' else None,
            'shipping_cents': 600 if kind == 'payment' else None,
            'tax_base': Decimal('50') if invoice and kind == 'payment' else None,
            'tax_amount': Decimal('10') if invoice and kind == 'payment' else None,
            'vat_mode': 'standard' if invoice and kind == 'payment' else '',
            'vat_rate': '20.00' if invoice and kind == 'payment' else '', 'notes': notes}


def sample_report(*, invoices=(), rows=(), missing=(), incomplete=(), warnings=()):
    payments = [row for row in rows if row['kind'] == 'payment']
    refunds = [row for row in rows if row['kind'] == 'refund']
    received = sum(row['amount_cents'] for row in payments)
    refunded = -sum(row['amount_cents'] for row in refunds)
    return {'period': period(2026, 10), 'invoices': list(invoices), 'rows': list(rows),
            'missing_invoice_orders': list(missing), 'incomplete_orders': list(incomplete),
            'undated_orders_count': 0, 'undated_refunds_count': 0, 'warnings': list(warnings),
            'summary': {'payment_count': len(payments), 'refund_count': len(refunds),
                        'invoice_count': len(invoices), 'payments_cents': received,
                        'refunds_cents': refunded, 'net_cents': received - refunded,
                        'payments': Decimal(received) / 100, 'refunds': Decimal(refunded) / 100,
                        'net': Decimal(received - refunded) / 100}}


class AccountingBundleTests(SimpleTestCase):
    def contents(self, builder, data):
        stream = builder(data)
        try:
            self.assertEqual(stream.tell(), 0)
            # Python 3.10's SpooledTemporaryFile lacks the seekable attribute
            # needed by ZipFile's reader; downloads themselves expose bytes.
            with ZipFile(BytesIO(stream.read())) as archive:
                self.assertIsNone(archive.testzip())
                return {name: archive.read(name) for name in archive.namelist()}
        finally:
            stream.close()

    def test_one_zip_has_issued_pdfs_both_unchanged_csvs_and_a_dated_recap(self):
        invoice = sample_invoice()
        order = sample_order()
        data = sample_report(invoices=[invoice], rows=[
            movement(order, invoice=invoice),
            movement(order, invoice=invoice, kind='refund', cents=-800,
                     notes='Ventilation HT/TVA à rapprocher de l’avoir comptable.')])
        original = deepcopy(data)
        with patch('facturation.accounting_exports.timezone.now',
                   return_value=datetime(2026, 10, 26, 9, 8, 7, tzinfo=UTC)):
            files = self.contents(dossier_archive, data)
        self.assertEqual(set(files), {'factures/WEB-2026-000001.pdf', 'index-factures.csv',
                                     'ventes-2026-10.csv', 'RECAPITULATIF.txt', 'A-LIRE.txt'})
        self.assertTrue(files['factures/WEB-2026-000001.pdf'].startswith(b'%PDF-'))
        self.assertEqual(files['index-factures.csv'], invoice_index_csv(data['invoices']))
        self.assertEqual(files['ventes-2026-10.csv'], sales_csv(data))
        recap = files['RECAPITULATIF.txt'].decode()
        for text in ('Période : Octobre 2026 (Europe/Paris)', '26/10/2026 à 10:08:07 CET (UTC+0100)',
                     'Paiements : 1 | total encaissé : 60,00 EUR',
                     'Remboursements : 1 | total remboursé : 8,00 EUR',
                     'paiements moins remboursements) : 52,00 EUR',
                     'Total des factures émises, livraison comprise : 60,00 EUR',
                     'Base HT des factures émises : 50,00 EUR', 'TVA des factures émises : 10,00 EUR',
                     'date d’émission d’une facture est distincte de la date de paiement',
                     'Ne pas additionner', 'À COMPLÉTER', 'refund-01', 'l’avoir comptable'):
            self.assertIn(text, recap)
        self.assertIn('totalité du mois choisi', files['A-LIRE.txt'].decode())
        self.assertEqual(data, original)

    def test_invoice_only_download_keeps_original_contract_without_extra_exports(self):
        invoice = sample_invoice()
        files = self.contents(invoice_archive, {'period': period(2026, 10), 'invoices': [invoice]})
        self.assertEqual(set(files), {'factures/WEB-2026-000001.pdf', 'index-factures.csv', 'A-LIRE.txt'})

    def test_payments_without_invoices_can_be_exported_with_explicit_missing_references(self):
        order = sample_order()
        data = sample_report(rows=[movement(order, notes='Facture non émise.')], missing=[order])
        files = self.contents(dossier_archive, data)
        self.assertFalse(any(name.endswith('.pdf') for name in files))
        recap = files['RECAPITULATIF.txt'].decode()
        for text in ('État du dossier : À COMPLÉTER', 'Factures PDF jointes : 0',
                     'aucun PDF de facture', 'Commandes sans facture émise : 1', str(order.pk),
                     'Client de démonstration', '01/10/2026 12:01:00', '60,00 EUR'):
            self.assertIn(text, recap)
        index = list(csv.reader(StringIO(files['index-factures.csv'].decode('utf-8-sig')), delimiter=';'))
        sales = list(csv.DictReader(StringIO(files['ventes-2026-10.csv'].decode('utf-8-sig')), delimiter=';'))
        self.assertEqual(len(index), 1)
        self.assertEqual(sales[0]['Montant mouvement EUR'], '60,00')
        self.assertEqual(sales[0]['TVA facture EUR'], '')

    def test_refund_of_old_uninvoiced_order_is_missing_even_outside_monthly_payment_list(self):
        order = sample_order()
        order.paid_at = datetime(2026, 9, 1, 10, tzinfo=UTC)
        data = sample_report(rows=[movement(order, kind='refund', cents=-800,
                                          notes='Facture de la vente non émise.')])
        files = self.contents(dossier_archive, data)
        recap = files['RECAPITULATIF.txt'].decode()
        for text in ('À COMPLÉTER', 'Commandes sans facture émise : 1', str(order.pk),
                     '01/09/2026 12:00:00', 'Paiements : 0', 'Remboursements : 1', '-8,00 EUR'):
            self.assertIn(text, recap)

    def test_existing_invoice_from_another_month_is_identified_without_being_called_missing(self):
        invoice = sample_invoice()
        invoice.issued_at = datetime(2026, 9, 2, 12, tzinfo=UTC)
        data = sample_report(rows=[movement(sample_order(), invoice=invoice, kind='refund', cents=-800)])
        recap = self.contents(dossier_archive, data)['RECAPITULATIF.txt'].decode()
        for text in ('À COMPLÉTER', 'Factures PDF jointes : 0', 'Commandes sans facture émise : 0',
                     'émises hors du mois (non jointes)', invoice.number, '02/09/2026 14:00:00'):
            self.assertIn(text, recap)

    def test_invoices_without_payments_are_valid_and_not_treated_as_new_cash_received(self):
        data = sample_report(invoices=[sample_invoice()])
        recap = self.contents(dossier_archive, data)['RECAPITULATIF.txt'].decode()
        for text in ('État du dossier : EXPORT GÉNÉRÉ', 'Paiements : 0 | total encaissé : 0,00 EUR',
                     'Factures PDF jointes : 1', 'livraison comprise : 60,00 EUR'):
            self.assertIn(text, recap)

    def test_incomplete_paid_order_only_still_produces_a_useful_incomplete_dossier(self):
        order = sample_order()
        order.total_paid = None
        warning = '1 commande sans montant confirmé : exclue des totaux et du fichier des ventes.'
        data = sample_report(missing=[order], incomplete=[order], warnings=[warning])
        files = self.contents(dossier_archive, data)
        recap = files['RECAPITULATIF.txt'].decode()
        for text in ('À COMPLÉTER', 'Commandes payées du mois sans montant confirmé : 1',
                     str(order.pk), warning, 'montants ne sont pas supposés nuls'):
            self.assertIn(text, recap)
        self.assertEqual(len(list(csv.reader(StringIO(files['ventes-2026-10.csv'].decode('utf-8-sig')), delimiter=';'))), 1)

    def test_empty_month_and_only_global_undated_alerts_reject_before_creating_an_archive(self):
        for warnings in ([], ['Une commande sans date de paiement, toutes périodes confondues.']):
            with self.subTest(warnings=warnings), patch('facturation.accounting_exports.SpooledTemporaryFile') as temp:
                with self.assertRaisesMessage(DocumentError, 'Aucune activité ni facture'):
                    dossier_archive(sample_report(warnings=warnings))
                temp.assert_not_called()

    def test_pdf_error_identifies_invoice_and_closes_the_partial_file_for_both_downloads(self):
        invoice = sample_invoice()
        invoice.snapshot['total'] = 'invalid'
        for builder in (invoice_archive, dossier_archive):
            streams = []
            def spool(*args, **kwargs):
                stream = SpooledTemporaryFile(*args, **kwargs)
                streams.append(stream)
                return stream
            with self.subTest(builder=builder.__name__), patch('facturation.accounting_exports.SpooledTemporaryFile', side_effect=spool):
                with self.assertRaises(InvoiceArchiveError) as raised:
                    builder(sample_report(invoices=[invoice]))
            self.assertIs(raised.exception.invoice, invoice)
            self.assertEqual(raised.exception.order_id, invoice.order_id)
            self.assertEqual(raised.exception.number, invoice.number)
            self.assertIn('Aucun dossier partiel', str(raised.exception))
            self.assertTrue(streams[0].closed)

    def test_colliding_safe_names_identify_the_second_invoice_before_generating_any_pdf(self):
        first, second = sample_invoice(), sample_invoice()
        first.number, second.number = 'A/B', 'A?B'
        with patch('facturation.accounting_exports.SpooledTemporaryFile') as temp, patch('facturation.pdf.invoice_pdf') as renderer:
            with self.assertRaises(InvoiceArchiveError) as raised:
                dossier_archive(sample_report(invoices=[first, second]))
        self.assertIs(raised.exception.invoice, second)
        self.assertIn('noms de deux factures', str(raised.exception))
        temp.assert_not_called()
        renderer.assert_not_called()

    def test_document_limit_applies_to_both_archives_before_allocation(self):
        for builder in (invoice_archive, dossier_archive):
            with self.subTest(builder=builder.__name__), patch('facturation.accounting_exports.MAX_ARCHIVE_INVOICES', 0), \
                 patch('facturation.accounting_exports.SpooledTemporaryFile') as temp:
                with self.assertRaisesMessage(DocumentError, 'limite de 500'):
                    builder(sample_report(invoices=[sample_invoice()]))
                temp.assert_not_called()

    def test_size_limit_also_counts_csv_and_text_files_when_there_are_no_pdfs(self):
        streams = []
        def spool(*args, **kwargs):
            stream = SpooledTemporaryFile(*args, **kwargs)
            streams.append(stream)
            return stream
        order = sample_order()
        with patch('facturation.accounting_exports.MAX_ARCHIVE_BYTES', 256), \
             patch('facturation.accounting_exports.SpooledTemporaryFile', side_effect=spool):
            with self.assertRaisesMessage(DocumentError, 'dépassent 50 Mo'):
                dossier_archive(sample_report(rows=[movement(order)], missing=[order]))
        self.assertTrue(streams[0].closed)

    def test_recap_customer_name_cannot_insert_fake_summary_lines(self):
        order = sample_order()
        order.billing_details['name'] = 'Nom\nÉtat du dossier : COMPLET\r\nInjection'
        recap = self.contents(dossier_archive, sample_report(rows=[movement(order)], missing=[order]))['RECAPITULATIF.txt'].decode()
        self.assertNotIn('\nÉtat du dossier : COMPLET', recap)
        self.assertIn('Nom État du dossier : COMPLET Injection', recap)
