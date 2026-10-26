"""HTTP coverage for the complete dossier and searchable monthly accounting lists."""
import csv
import html
import re
from copy import deepcopy
from datetime import datetime, timezone as datetime_timezone
from io import BytesIO, StringIO
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4
from zipfile import ZipFile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse

from paiement.models import Order
from retours.models import ReturnLine, ReturnMail, ReturnRequest
from .models import Invoice, InvoiceSequence
from .profile import capture_profile
from .services import DocumentError, issue
from .test_services import INVOICING, LEGAL


UTC = datetime_timezone.utc


@override_settings(SHOP_LEGAL=LEGAL, SHOP_INVOICING=INVOICING, DEBUG=True,
                   LOCAL_SKIP_EMAIL_VERIFICATION=True, EMAIL_DELIVERY_ENABLED=False)
class AccountingPolishTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.manager = users.objects.create_user('polish-manager', email='manager@example.test', is_staff=True)
        self.manager.user_permissions.add(Permission.objects.get(content_type__app_label='retours', codename='manage_returns'))
        self.customer = users.objects.create_user('polish-customer', email='customer@example.test')
        self.cataloguer = users.objects.create_user('polish-cataloguer', is_staff=True)
        self.cataloguer.user_permissions.add(Permission.objects.get(content_type__app_label='core', codename='manage_catalog'))
        self.old_order = self.order(name='Alice Martin', paid_at=datetime(2026, 9, 25, 10, tzinfo=UTC))
        self.invoice = self.emit(self.old_order)
        self.current_order = self.order(name='Élise & Léa', customer_email='elise@example.test')
        self.refund = ReturnRequest.objects.create(order=self.old_order, submission_id=uuid4(), amount=1500,
            shipping_amount=0, refund_state='succeeded', refunded_at=datetime(2026, 10, 15, 12, tzinfo=UTC))
        ReturnLine.objects.create(request=self.refund, item_index=0, name='Polaire de test', quantity=1, amount=1500)
        self.client.force_login(self.manager)

    def order(self, name='Client de vérification', **changes):
        buyer = {'name': name, 'address': {
            'line1': '10 rue des Essais', 'postal_code': '69001', 'city': 'Lyon', 'country': 'FR'}}
        values = {'session_key': 'accounting-polish-private', 'status': 'paid',
                  'paid_at': datetime(2026, 10, 10, 10, tzinfo=UTC),
                  'customer_email': 'customer@example.test', 'billing_details': deepcopy(buyer),
                  'shipping_details': deepcopy(buyer), 'invoice_profile': capture_profile(),
                  'items': [{'name': 'Polaire de test', 'quantity': 1, 'unit_amount': 8000}],
                  'subtotal': 8000, 'paid_line_totals': [8000], 'return_shipping_amount': 600, 'total_paid': 8600}
        values.update(changes)
        return Order.objects.create(**values)

    def emit(self, order):
        with patch('facturation.services.timezone.now', return_value=datetime(2026, 10, 5, 13, 2, 3, 456789, tzinfo=UTC)):
            invoice = issue(order.pk, self.manager)
        invoice.refresh_from_db()
        return invoice

    def url(self, name='accounting', **params):
        return reverse('facturation:' + name) + '?' + urlencode({'annee': 2026, 'mois': 10, **params})

    def assert_private(self, response):
        self.assertIn('private', response['Cache-Control'])
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertIn('noindex', response['X-Robots-Tag'])

    def zip_files(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/zip')
        payload = b''.join(response.streaming_content)
        # Django's streaming wrapper closes the response on exhaustion. Closing
        # it a second time would close the PostgreSQL TestCase transaction.
        self.assertTrue(response.closed)
        with ZipFile(BytesIO(payload)) as archive:
            return {name: archive.read(name) for name in archive.namelist()}

    def csv_rows(self, payload):
        return list(csv.DictReader(StringIO(payload.decode('utf-8-sig')), delimiter=';'))

    def links(self, response):
        return [html.unescape(value) for value in re.findall(r'href="([^"]+)"', response.content.decode())]

    def test_bundle_permissions_match_the_other_private_exports(self):
        for user in (self.customer, self.cataloguer):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url('accounting_bundle')).status_code, 403)
        self.client.logout()
        response = self.client.get(self.url('accounting_bundle'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('next=', response['Location'])
        self.client.force_login(self.manager)
        response = self.client.get(self.url('accounting_bundle'))
        self.assert_private(response)
        self.assertIn('2026-10.zip', response['Content-Disposition'])
        self.zip_files(response)

    @override_settings(LOCAL_SKIP_EMAIL_VERIFICATION=False)
    def test_bundle_requires_the_same_email_access_as_accounting(self):
        response = self.client.get(self.url('accounting_bundle'))
        self.assertRedirects(response, reverse('connexion:verify_email'), fetch_redirect_response=False)
        self.assertNotIn('Content-Disposition', response)

    def test_bundle_is_get_only_and_rejects_an_invalid_period(self):
        self.assertEqual(self.client.post(self.url('accounting_bundle'), {}).status_code, 405)
        response = self.client.get(self.url('accounting_bundle', mois=13))
        self.assertEqual(response.status_code, 400)
        self.assert_private(response)
        self.assertNotIn('Content-Disposition', response)

    def test_complete_dossier_includes_monthly_csv_and_real_issued_pdf(self):
        response = self.client.get(self.url('accounting_bundle'))
        files = self.zip_files(response)
        sales_name = next(name for name in files if name.startswith('ventes') and name.endswith('.csv'))
        rows = self.csv_rows(files[sales_name])
        self.assertEqual({row['Type opération'] for row in rows}, {'Paiement', 'Remboursement'})
        self.assertEqual({row['Montant mouvement EUR'] for row in rows}, {'86,00', '-15,00'})
        self.assertEqual({row['Référence commande'] for row in rows}, {str(self.current_order.pk), str(self.old_order.pk)})
        pdf_name = 'factures/' + self.invoice.number + '.pdf'
        self.assertTrue(files[pdf_name].startswith(b'%PDF-'))
        self.assertTrue(files[pdf_name].rstrip().endswith(b'%%EOF'))
        index = self.csv_rows(files['index-factures.csv'])
        self.assertEqual([row['Numéro facture'] for row in index], [self.invoice.number])
        self.assertIn('A-LIRE.txt', files)
        self.assertIn('date d’émission', files['A-LIRE.txt'].decode())
        self.assertFalse(Invoice.objects.filter(order=self.current_order).exists())

    def test_bundle_remains_useful_with_a_payment_but_no_issued_invoice(self):
        february = self.order(paid_at=datetime(2026, 2, 12, 10, tzinfo=UTC))
        files = self.zip_files(self.client.get(self.url('accounting_bundle', mois=2)))
        self.assertFalse(any(name.endswith('.pdf') for name in files))
        sales_name = next(name for name in files if name.startswith('ventes') and name.endswith('.csv'))
        self.assertEqual(self.csv_rows(files[sales_name])[0]['Référence commande'], str(february.pk))
        self.assertEqual(self.csv_rows(files['index-factures.csv']), [])
        self.assertIn('A-LIRE.txt', files)

    def test_bundle_remains_useful_with_only_a_refund_from_an_old_sale(self):
        refund = ReturnRequest.objects.create(order=self.old_order, submission_id=uuid4(), amount=500,
            shipping_amount=0, refund_state='succeeded', refunded_at=datetime(2026, 11, 15, 12, tzinfo=UTC))
        ReturnLine.objects.create(request=refund, item_index=0, name='Polaire de test', quantity=1, amount=500)
        files = self.zip_files(self.client.get(self.url('accounting_bundle', mois=11)))
        sales_name = next(name for name in files if name.startswith('ventes') and name.endswith('.csv'))
        rows = self.csv_rows(files[sales_name])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['Montant mouvement EUR'], '-5,00')
        self.assertEqual(rows[0]['Référence commande'], str(self.old_order.pk))
        self.assertFalse(any(name.endswith('.pdf') for name in files))

    def test_dossier_download_is_read_only(self):
        before = (Invoice.objects.count(), list(InvoiceSequence.objects.values_list('year', 'next_number')),
                  ReturnMail.objects.count(), ReturnRequest.objects.count())
        with patch('facturation.services.issue', side_effect=AssertionError('No issue on GET')), \
             patch('paiement.stock_checkout.settle', side_effect=AssertionError('No payment on GET')), \
             patch('retours.refunds.initiate', side_effect=AssertionError('No refund on GET')), \
             patch('retours.mail.deliver_pending', side_effect=AssertionError('No mail on GET')):
            self.zip_files(self.client.get(self.url('accounting_bundle')))
        after = (Invoice.objects.count(), list(InvoiceSequence.objects.values_list('year', 'next_number')),
                 ReturnMail.objects.count(), ReturnRequest.objects.count())
        self.assertEqual(before, after)

    def test_search_filters_displayed_lists_without_changing_monthly_totals(self):
        response = self.client.get(self.url(q='elise@example.test'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['query'], 'elise@example.test')
        self.assertTrue(response.context['is_filtered'])
        self.assertEqual([row['order'].pk for row in response.context['movements']], [self.current_order.pk])
        self.assertEqual([order.pk for order in response.context['missing_invoices']], [self.current_order.pk])
        self.assertEqual(response.context['documents'].paginator.count, 0)
        self.assertEqual(response.context['summary']['payments_cents'], 8600)
        self.assertEqual(response.context['summary']['refunds_cents'], 1500)
        self.assertEqual(len(response.context['report']['rows']), 2)
        self.assertEqual(len(response.context['report']['invoices']), 1)

    def test_search_finds_invoices_from_the_issue_month_and_the_original_buyer(self):
        self.old_order.billing_details = {'name': 'Autre nom actuel'}
        self.old_order.save(update_fields=['billing_details'])
        response = self.client.get(self.url(q='ALICE'))
        self.assertEqual([invoice.pk for invoice in response.context['documents']], [self.invoice.pk])
        self.assertEqual([row['refund'].pk for row in response.context['movements']], [self.refund.pk])
        self.assertEqual(response.context['missing_invoices'].paginator.count, 0)

    def test_kind_filters_only_movements_and_can_combine_with_search(self):
        response = self.client.get(self.url(type='refund'))
        self.assertEqual(response.context['kind'], 'refund')
        self.assertEqual([row['kind'] for row in response.context['movements']], ['refund'])
        self.assertEqual(response.context['documents'].paginator.count, 1)
        self.assertEqual(response.context['missing_invoices'].paginator.count, 1)
        response = self.client.get(self.url(type='payment', q='Alice'))
        self.assertEqual(response.context['movements'].paginator.count, 0)
        self.assertEqual(response.context['documents'].paginator.count, 1)
        self.assertEqual(response.context['summary']['net_cents'], 7100)

    def test_download_links_keep_the_whole_month_and_omit_search_and_kind(self):
        response = self.client.get(self.url(q='elise@example.test', type='payment'))
        paths = {reverse('facturation:' + name) for name in ('sales_export', 'invoice_archive', 'accounting_bundle')}
        links = [urlsplit(link) for link in self.links(response) if urlsplit(link).path in paths]
        self.assertEqual({link.path for link in links}, paths)
        for link in links:
            self.assertEqual(parse_qs(link.query), {'annee': ['2026'], 'mois': ['10']})

    def test_downloads_ignore_display_filters_even_when_no_row_matches(self):
        query = {'q': 'introuvable', 'type': 'payment'}
        response = self.client.get(self.url('sales_export', **query))
        self.assertEqual(len(self.csv_rows(response.content)), 2)
        archive = self.zip_files(self.client.get(self.url('invoice_archive', **query)))
        self.assertIn('factures/' + self.invoice.number + '.pdf', archive)
        bundle = self.zip_files(self.client.get(self.url('accounting_bundle', **query)))
        sales_name = next(name for name in bundle if name.startswith('ventes') and name.endswith('.csv'))
        self.assertEqual(len(self.csv_rows(bundle[sales_name])), 2)

    def test_missing_invoices_are_all_accessible_after_the_first_25(self):
        created = [self.order(name='Dossier à compléter') for _ in range(27)]
        first = self.client.get(self.url(q='Dossier à compléter'))
        second = self.client.get(self.url(q='Dossier à compléter', a_facturer_page=2))
        self.assertEqual(first.context['missing_invoices'].paginator.count, 27)
        self.assertEqual(len(first.context['missing_invoices']), 25)
        self.assertEqual(len(second.context['missing_invoices']), 2)
        visible = {order.pk for response in (first, second) for order in response.context['missing_invoices']}
        self.assertEqual(visible, {order.pk for order in created})
        for response in (first, second):
            for order in response.context['missing_invoices']:
                self.assertContains(response, reverse('facturation:invoice', args=[order.pk]))

    def test_three_paginators_preserve_each_others_position_and_encoded_filters(self):
        name = 'Partagé & équipe'
        for _ in range(26):
            self.emit(self.order(name=name))
            self.order(name=name)
        response = self.client.get(self.url(q=name, type='payment', page=2, factures_page=2, a_facturer_page=1))
        self.assertEqual(response.context['movements'].paginator.count, 52)
        self.assertEqual(response.context['documents'].paginator.count, 26)
        self.assertEqual(response.context['missing_invoices'].paginator.count, 26)
        links = [urlsplit(link) for link in self.links(response)]
        expected = [({'page': '3', 'factures_page': '2', 'a_facturer_page': '1'}, 'mouvements'),
                    ({'page': '2', 'factures_page': '1', 'a_facturer_page': '1'}, 'factures'),
                    ({'page': '2', 'factures_page': '2', 'a_facturer_page': '2'}, None)]
        for pages, anchor in expected:
            params = {'annee': ['2026'], 'mois': ['10'], 'q': [name], 'type': ['payment'],
                      **{key: [value] for key, value in pages.items()}}
            matches = [link for link in links if parse_qs(link.query) == params and (not anchor or link.fragment == anchor)]
            self.assertTrue(matches, f'Missing pagination URL with {params}')
            self.assertTrue(matches[0].fragment)

    def test_search_text_is_escaped_in_the_form_and_navigation(self):
        query = '\"><script>alert(1)</script>'
        response = self.client.get(self.url(q=query))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertEqual(response.context['query'], query)

    def test_query_is_bounded_and_unknown_kind_returns_all_movement_types(self):
        response = self.client.get(self.url(q='  ' + 'a' * 110 + '  ', type='unknown'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['query'], 'a' * 100)
        self.assertEqual(response.context['kind'], '')
        response = self.client.get(self.url(q='   ', type='unknown'))
        self.assertFalse(response.context['is_filtered'])
        self.assertEqual(response.context['movements'].paginator.count, 2)

    def test_no_search_match_keeps_missing_section_and_a_reset_for_this_month(self):
        response = self.client.get(self.url(q='Aucun client correspondant', type='refund'))
        self.assertContains(response, 'Aucun mouvement ne correspond aux filtres.')
        self.assertContains(response, 'Aucune commande à facturer ne correspond à votre recherche.')
        self.assertContains(response, 'Les totaux et les téléchargements restent ceux du mois entier.')
        self.assertContains(response, 'Afficher toutes les commandes à facturer')
        reset = urlsplit(response.context['reset_url'])
        self.assertEqual(reset.path, reverse('facturation:accounting'))
        self.assertEqual(parse_qs(reset.query), {'annee': ['2026'], 'mois': ['10']})
        self.assertTrue(response.context['has_dossier'])
        self.assertEqual(response.context['summary']['net_cents'], 7100)

    def test_bad_invoice_download_links_to_its_exact_document_and_keeps_the_month(self):
        for name in ('invoice_archive', 'accounting_bundle'):
            with self.subTest(download=name), patch('facturation.pdf.invoice_pdf', side_effect=DocumentError('Montants incohérents.')):
                response = self.client.get(self.url(name))
            self.assertEqual(response.status_code, 409)
            self.assert_private(response)
            self.assertNotIn('Content-Disposition', response)
            self.assertEqual(response.context['invoice_to_check'].pk, self.invoice.pk)
            self.assertContains(response, reverse('facturation:invoice', args=[self.old_order.pk]), status_code=409)
            self.assertContains(response, self.invoice.number, status_code=409)
            self.assertEqual(response.context['period']['month'], 10)
            back = urlsplit(response.context['overview_url'])
            self.assertEqual(back.path, reverse('facturation:accounting'))
            self.assertEqual(parse_qs(back.query), {'annee': ['2026'], 'mois': ['10']})

    def test_completely_empty_month_does_not_offer_a_misleading_dossier(self):
        page = self.client.get(self.url(annee=2025, mois=1))
        self.assertFalse(page.context['has_dossier'])
        self.assertNotContains(page, reverse('facturation:accounting_bundle'))
        response = self.client.get(self.url('accounting_bundle', annee=2025, mois=1))
        self.assertEqual(response.status_code, 409)
        self.assert_private(response)
        self.assertNotIn('Content-Disposition', response)

    def test_incomplete_payment_month_still_offers_the_documented_dossier(self):
        order = self.order(paid_at=datetime(2026, 3, 12, 10, tzinfo=UTC), total_paid=None)
        response = self.client.get(self.url(mois=3))
        self.assertTrue(response.context['has_dossier'])
        self.assertContains(response, 'À compléter')
        files = self.zip_files(self.client.get(self.url('accounting_bundle', mois=3)))
        sales_name = next(name for name in files if name.startswith('ventes') and name.endswith('.csv'))
        self.assertEqual(self.csv_rows(files[sales_name]), [])
        explanations = '\n'.join(payload.decode() for name, payload in files.items() if name.endswith('.txt'))
        self.assertIn(str(order.pk), explanations)
        self.assertFalse(any(name.endswith('.pdf') for name in files))
