"""Accounting downloads stay private, read-only, complete and tied to the chosen month."""
import csv
from copy import deepcopy
from datetime import datetime, timezone as datetime_timezone
from io import BytesIO, StringIO
from tempfile import SpooledTemporaryFile
from unittest.mock import patch
from uuid import uuid4
from zipfile import ZipFile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import signals
from django.db import close_old_connections
from django.test import TestCase, override_settings
from django.urls import reverse

from paiement.models import Order
from retours.models import ReturnLine, ReturnMail, ReturnRequest
from .accounting import period
from .accounting_views import invoice_archive
from .models import Invoice, InvoiceSequence
from .profile import capture_profile
from .services import DocumentError, issue
from .test_pdf import sample_invoice
from .test_services import INVOICING, LEGAL


UTC = datetime_timezone.utc


@override_settings(SHOP_LEGAL=LEGAL, SHOP_INVOICING=INVOICING, DEBUG=True,
                   LOCAL_SKIP_EMAIL_VERIFICATION=True, EMAIL_DELIVERY_ENABLED=False)
class AccountingDownloadTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.manager = users.objects.create_user('accounting-manager', email='manager@example.test', is_staff=True)
        self.manager.user_permissions.add(Permission.objects.get(content_type__app_label='retours', codename='manage_returns'))
        self.customer = users.objects.create_user('accounting-customer', email='customer@example.test')
        self.cataloguer = users.objects.create_user('accounting-cataloguer', is_staff=True, email='catalogue@example.test')
        self.cataloguer.user_permissions.add(Permission.objects.get(content_type__app_label='core', codename='manage_catalog'))
        self.buyer = {'name': 'Client de vérification', 'address': {
            'line1': '10 rue des Essais', 'postal_code': '69001', 'city': 'Lyon', 'country': 'FR'}}
        self.old_order = self.order(paid_at=datetime(2026, 9, 25, 10, tzinfo=UTC))
        with patch('facturation.services.timezone.now', return_value=datetime(2026, 10, 5, 13, 2, 3, 456789, tzinfo=UTC)):
            self.invoice = issue(self.old_order.pk, self.manager)
        self.invoice.refresh_from_db()
        self.client.force_login(self.manager)

    def order(self, **changes):
        values = {'session_key': 'accounting-private-test', 'status': 'paid',
                  'paid_at': datetime(2026, 10, 10, 10, tzinfo=UTC),
                  'customer_email': 'customer@example.test', 'billing_details': deepcopy(self.buyer),
                  'shipping_details': deepcopy(self.buyer), 'invoice_profile': capture_profile(),
                  'items': [{'name': 'Polaire de test', 'quantity': 1, 'unit_amount': 8000, 'size': 'M', 'color': 'Vert'}],
                  'subtotal': 8000, 'paid_line_totals': [8000], 'return_shipping_amount': 600, 'total_paid': 8600}
        values.update(changes)
        return Order.objects.create(**values)

    def url(self, name, query='annee=2026&mois=10'):
        return reverse('facturation:' + name) + ('?' + query if query else '')

    def assert_private(self, response):
        self.assertIn('private', response['Cache-Control'])
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertIn('noindex', response['X-Robots-Tag'])

    def test_only_returns_managers_can_access_accounting_and_downloads(self):
        for user in (self.customer, self.cataloguer):
            self.client.force_login(user)
            for name in ('accounting', 'sales_export', 'invoice_archive'):
                with self.subTest(user=user.username, page=name):
                    self.assertEqual(self.client.get(self.url(name)).status_code, 403)
        self.client.force_login(self.manager)
        for name in ('accounting', 'sales_export', 'invoice_archive'):
            response = self.client.get(self.url(name))
            self.assertEqual(response.status_code, 200)
            self.assert_private(response)
            if response.streaming:
                b''.join(response.streaming_content)
            self.assertTrue(response.closed)

    def test_anonymous_is_redirected_and_inactive_staff_cannot_enter(self):
        self.client.logout()
        for name in ('accounting', 'sales_export', 'invoice_archive'):
            response = self.client.get(self.url(name))
            self.assertEqual(response.status_code, 302)
            self.assertIn('next=', response['Location'])
        self.manager.is_active = False
        self.manager.save(update_fields=['is_active'])
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(self.url('accounting')).status_code, 302)

    @override_settings(LOCAL_SKIP_EMAIL_VERIFICATION=False)
    def test_unverified_manager_is_redirected_without_exporting_private_data(self):
        for name in ('accounting', 'sales_export', 'invoice_archive'):
            response = self.client.get(self.url(name))
            self.assertRedirects(response, reverse('connexion:verify_email'), fetch_redirect_response=False)
            self.assertNotIn('Content-Disposition', response)

    def test_post_methods_do_not_change_state_or_download(self):
        for name in ('accounting', 'sales_export', 'invoice_archive'):
            self.assertEqual(self.client.post(self.url(name), {}).status_code, 405)

    def test_invalid_period_is_an_explicit_private_error_for_every_route(self):
        for query in ('annee=1999&mois=10', 'annee=2101&mois=10', 'annee=2026&mois=0',
                      'annee=2026&mois=13', 'annee=2026&mois=abc', 'annee=&mois=1'):
            for name in ('accounting', 'sales_export', 'invoice_archive'):
                with self.subTest(query=query, page=name):
                    response = self.client.get(self.url(name, query))
                    self.assertContains(response, 'Choisissez un mois valide', status_code=400)
                    self.assert_private(response)
                    self.assertNotIn('Content-Disposition', response)

    def test_default_month_uses_paris_even_when_utc_is_previous_month(self):
        with patch('facturation.accounting_views.timezone.now', return_value=datetime(2026, 9, 30, 22, 30, tzinfo=UTC)):
            response = self.client.get(self.url('accounting', ''))
        self.assertContains(response, 'Octobre 2026')
        self.assertEqual(response.context['period']['month'], 10)

    def test_page_and_downloads_do_not_issue_invoices_or_send_payments_or_mail(self):
        missing = self.order()
        before = (Invoice.objects.count(), list(InvoiceSequence.objects.values_list('year', 'next_number')),
                  ReturnMail.objects.count(), ReturnRequest.objects.count())
        with patch('facturation.services.issue', side_effect=AssertionError('No issue on GET')), \
             patch('paiement.stock_checkout.settle', side_effect=AssertionError('No payment on GET')), \
             patch('retours.refunds.initiate', side_effect=AssertionError('No refund on GET')), \
             patch('retours.mail.deliver_pending', side_effect=AssertionError('No mail on GET')):
            for name in ('accounting', 'sales_export', 'invoice_archive'):
                response = self.client.get(self.url(name))
                self.assertEqual(response.status_code, 200)
                if response.streaming:
                    b''.join(response.streaming_content)
                self.assertTrue(response.closed)
        after = (Invoice.objects.count(), list(InvoiceSequence.objects.values_list('year', 'next_number')),
                 ReturnMail.objects.count(), ReturnRequest.objects.count())
        self.assertEqual(before, after)
        self.assertFalse(Invoice.objects.filter(order=missing).exists())

    def test_overview_preserves_month_in_links_and_marks_missing_invoice(self):
        order = self.order()
        response = self.client.get(self.url('accounting'))
        self.assertContains(response, 'Comptabilité et exports.')
        self.assertContains(response, '1 commande sans facture émise')
        self.assertContains(response, reverse('facturation:invoice', args=[order.pk]))
        self.assertContains(response, 'ventes.csv?annee=2026&amp;mois=10')
        self.assertContains(response, 'factures.zip?annee=2026&amp;mois=10')
        self.assertContains(response, self.invoice.number)

    def test_csv_uses_confirmed_movement_month_and_signed_refunds_for_old_orders(self):
        current = self.order(subtotal=10000, paid_line_totals=[9000], total_paid=10000,
                             return_shipping_amount=1000, items=[{'name': 'Veste', 'quantity': 1, 'unit_amount': 10000}])
        refund = ReturnRequest.objects.create(order=self.old_order, submission_id=uuid4(), amount=2000,
            shipping_amount=500, refund_state='succeeded', refunded_at=datetime(2026, 10, 15, 12, tzinfo=UTC))
        ReturnLine.objects.create(request=refund, item_index=0, name='Polaire de test', quantity=1, amount=1500)
        self.order(status='demo')
        self.order(status='pending')
        response = self.client.get(self.url('sales_export'))
        self.assertEqual(response['Content-Disposition'], 'attachment; filename="ventes-2026-10.csv"')
        self.assertTrue(response.content.startswith(b'\xef\xbb\xbf'))
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        rows = list(csv.DictReader(StringIO(response.content.decode('utf-8-sig')), delimiter=';'))
        self.assertEqual(len(rows), 2)
        sale = next(row for row in rows if row['Type opération'] == 'Paiement')
        reimbursement = next(row for row in rows if row['Type opération'] == 'Remboursement')
        self.assertEqual(sale['Référence commande'], str(current.pk))
        self.assertEqual(sale['Montant mouvement EUR'], '100,00')
        self.assertEqual(sale['Remise articles EUR'], '10,00')
        self.assertEqual(sale['Livraison EUR'], '10,00')
        self.assertEqual(sale['TVA facture EUR'], '')
        self.assertEqual(reimbursement['Référence commande'], str(self.old_order.pk))
        self.assertEqual(reimbursement['Montant mouvement EUR'], '-20,00')
        self.assertEqual(reimbursement['Livraison EUR'], '-5,00')
        self.assertEqual(reimbursement['Numéro facture'], self.invoice.number)
        self.assertNotIn('accounting-private-test', response.content.decode('utf-8-sig'))

    def test_empty_sales_export_remains_a_valid_header_only_csv(self):
        response = self.client.get(self.url('sales_export', 'annee=2025&mois=1'))
        rows = list(csv.reader(StringIO(response.content.decode('utf-8-sig')), delimiter=';'))
        self.assertEqual(len(rows), 1)
        self.assertIn('Montant mouvement EUR', rows[0])

    def test_archive_contains_real_pdf_index_and_explanation_for_issue_month(self):
        response = self.client.get(self.url('invoice_archive'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/zip')
        self.assertIn('factures-2026-10.zip', response['Content-Disposition'])
        payload = b''.join(response.streaming_content)
        self.assertTrue(response.closed)
        with ZipFile(BytesIO(payload)) as archive:
            self.assertEqual(set(archive.namelist()), {
                'factures/' + self.invoice.number + '.pdf', 'index-factures.csv', 'A-LIRE.txt'})
            pdf = archive.read('factures/' + self.invoice.number + '.pdf')
            self.assertTrue(pdf.startswith(b'%PDF-'))
            self.assertTrue(pdf.rstrip().endswith(b'%%EOF'))
            self.assertIn(b'/FontFile2', pdf)
            index = list(csv.DictReader(StringIO(archive.read('index-factures.csv').decode('utf-8-sig')), delimiter=';'))
            self.assertEqual(index[0]['Numéro facture'], self.invoice.number)
            self.assertEqual(index[0]['Total facture EUR'], '86,00')
            self.assertIn('date d’émission', archive.read('A-LIRE.txt').decode())
        september = self.client.get(self.url('invoice_archive', 'annee=2026&mois=9'))
        self.assertContains(september, 'Aucune facture', status_code=409)

    def test_archive_keeps_issued_snapshot_after_live_order_is_modified(self):
        self.old_order.billing_details = {'name': 'Nom actuel sans rapport'}
        self.old_order.items = [{'name': 'Prix actuel à ignorer'}]
        self.old_order.save(update_fields=['billing_details', 'items'])
        response = self.client.get(self.url('invoice_archive'))
        self.assertEqual(response.status_code, 200)
        with ZipFile(BytesIO(b''.join(response.streaming_content))) as archive:
            index = archive.read('index-factures.csv').decode('utf-8-sig')
            self.assertIn('Client de vérification', index)
            self.assertNotIn('Nom actuel sans rapport', index)
        self.assertTrue(response.closed)

    def test_empty_archive_is_an_explicit_private_409_not_a_misleading_empty_zip(self):
        response = self.client.get(self.url('invoice_archive', 'annee=2025&mois=1'))
        self.assertContains(response, 'Aucune facture', status_code=409)
        self.assert_private(response)
        self.assertNotIn('Content-Disposition', response)

    def test_document_error_is_a_private_409_and_closes_the_partial_archive(self):
        files = []
        def spool(*args, **kwargs):
            stream = SpooledTemporaryFile(*args, **kwargs)
            files.append(stream)
            return stream
        with patch('facturation.accounting_exports.SpooledTemporaryFile', side_effect=spool), \
             patch('facturation.pdf.invoice_pdf', side_effect=DocumentError('Snapshot incohérent.')):
            response = self.client.get(self.url('invoice_archive'))
        self.assertContains(response, 'Aucun dossier partiel', status_code=409)
        self.assertContains(response, self.invoice.number, status_code=409)
        self.assert_private(response)
        self.assertNotIn('Content-Disposition', response)
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].closed)

    def test_response_close_releases_the_successful_private_archive(self):
        files = []
        def spool(*args, **kwargs):
            stream = SpooledTemporaryFile(*args, **kwargs)
            files.append(stream)
            return stream
        with patch('facturation.accounting_exports.SpooledTemporaryFile', side_effect=spool):
            response = self.client.get(self.url('invoice_archive'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(files), 1)
        self.assertFalse(files[0].closed)
        # Match Django's test-client closing wrapper: protect the enclosing
        # TestCase transaction while exercising FileResponse.close itself.
        connected = signals.request_finished.disconnect(close_old_connections)
        try:
            response.close()
        finally:
            if connected:
                signals.request_finished.connect(close_old_connections)
        self.assertTrue(response.closed)
        self.assertTrue(files[0].closed)

    def test_document_limit_rejects_before_creating_a_temporary_file(self):
        with patch('facturation.accounting_exports.MAX_ARCHIVE_INVOICES', 0), \
             patch('facturation.accounting_exports.SpooledTemporaryFile') as temporary:
            response = self.client.get(self.url('invoice_archive'))
        self.assertEqual(response.status_code, 409)
        temporary.assert_not_called()

    def test_size_limit_closes_file_and_returns_no_partial_zip(self):
        files = []
        def spool(*args, **kwargs):
            stream = SpooledTemporaryFile(*args, **kwargs)
            files.append(stream)
            return stream
        with patch('facturation.accounting_exports.MAX_ARCHIVE_BYTES', 1), \
             patch('facturation.accounting_exports.SpooledTemporaryFile', side_effect=spool):
            response = self.client.get(self.url('invoice_archive'))
        self.assertContains(response, 'dépassent 50 Mo', status_code=409)
        self.assertTrue(files[0].closed)
        self.assertNotIn('Content-Disposition', response)

    def test_unexpected_generation_error_also_closes_partial_file(self):
        files = []
        def spool(*args, **kwargs):
            stream = SpooledTemporaryFile(*args, **kwargs)
            files.append(stream)
            return stream
        with patch('facturation.accounting_exports.SpooledTemporaryFile', side_effect=spool), \
             patch('facturation.pdf.invoice_pdf', side_effect=RuntimeError('generation failure')):
            with self.assertRaises(RuntimeError):
                invoice_archive({'period': period(2026, 10), 'invoices': [self.invoice]})
        self.assertTrue(files[0].closed)

    def test_archive_names_cannot_escape_the_invoice_directory(self):
        invoice = sample_invoice()
        invoice.number = '../../outside\\private'
        invoice.snapshot['number'] = invoice.number
        invoice.order_id = self.old_order.pk
        with patch('facturation.pdf.invoice_pdf', return_value=b'%PDF-test'):
            stream = invoice_archive({'period': period(2026, 10), 'invoices': [invoice]})
        try:
            with ZipFile(stream) as archive:
                name = next(name for name in archive.namelist() if name.endswith('.pdf'))
                self.assertTrue(name.startswith('factures/'))
                self.assertNotIn('..', name)
                self.assertNotIn('\\', name)
                self.assertEqual(name.count('/'), 1)
        finally:
            stream.close()

    def test_sanitized_filename_collision_fails_before_serving_partial_content(self):
        invoices = [sample_invoice(), sample_invoice()]
        for invoice, number in zip(invoices, ('A/B', 'A?B')):
            invoice.number = number
            invoice.snapshot['number'] = number
            invoice.order_id = self.old_order.pk
        with patch('facturation.pdf.invoice_pdf', return_value=b'%PDF-test'):
            with self.assertRaisesMessage(DocumentError, 'noms de deux factures'):
                invoice_archive({'period': period(2026, 10), 'invoices': invoices})
