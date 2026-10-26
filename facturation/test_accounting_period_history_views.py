"""HTTP regressions for selectable accounting periods and manual hand-off history."""
import csv
import html
import hashlib
import re
from copy import deepcopy
from datetime import datetime, timedelta, timezone as datetime_timezone
from io import BytesIO, StringIO
from tempfile import SpooledTemporaryFile
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit
from zipfile import ZipFile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase, override_settings
from django.db import DatabaseError
from django.urls import reverse
from django.utils import timezone

from .accounting import PARIS
from .models import AccountingExport, AccountingExportTransmission, Invoice, InvoiceSequence

from paiement.models import Order
from retours.models import ReturnMail
from .profile import capture_profile
from .services import DocumentError, issue
from .test_services import INVOICING, LEGAL

UTC = datetime_timezone.utc


@override_settings(SHOP_LEGAL=LEGAL, SHOP_INVOICING=INVOICING, DEBUG=True,
                   LOCAL_SKIP_EMAIL_VERIFICATION=True, EMAIL_DELIVERY_ENABLED=False)
class AccountingPeriodHistoryHTTPTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.manager = users.objects.create_user('period-history-manager', email='manager@example.test', is_staff=True)
        self.manager.user_permissions.add(Permission.objects.get(content_type__app_label='retours', codename='manage_returns'))
        self.customer = users.objects.create_user('period-history-customer', email='client@example.test')
        self.cataloguer = users.objects.create_user('period-history-cataloguer', is_staff=True)
        self.cataloguer.user_permissions.add(Permission.objects.get(content_type__app_label='core', codename='manage_catalog'))
        self.october = self.order('Alice Exemple', datetime(2026, 10, 10, 10, tzinfo=UTC))
        self.november = self.order('Béatrice Exemple', datetime(2026, 11, 12, 10, tzinfo=UTC))
        self.outside = self.order('Client hors période', datetime(2027, 1, 2, 10, tzinfo=UTC))
        with patch('facturation.services.timezone.now', return_value=datetime(2026, 10, 15, 10, tzinfo=UTC)):
            self.invoice = issue(self.october.pk, self.manager)
        self.invoice.refresh_from_db()
        self.client.force_login(self.manager)

    def order(self, name, paid_at):
        buyer = {'name': name, 'address': {'line1': '10 rue des Essais', 'postal_code': '69001',
                                         'city': 'Lyon', 'country': 'FR'}}
        return Order.objects.create(session_key='period-history-tests', status='paid', paid_at=paid_at,
            customer_email='client@example.test', billing_details=deepcopy(buyer), shipping_details=deepcopy(buyer),
            invoice_profile=capture_profile(), items=[{'name': 'Polaire de test', 'quantity': 1, 'unit_amount': 8000}],
            subtotal=8000, paid_line_totals=[8000], return_shipping_amount=600, total_paid=8600)

    def url(self, name='accounting', **params):
        return reverse('facturation:' + name) + ('?' + urlencode(params) if params else '')

    def csv_rows(self, data):
        return list(csv.DictReader(StringIO(data.decode('utf-8-sig')), delimiter=';'))

    def archive(self, response):
        self.assertEqual(response.status_code, 200)
        payload = b''.join(response.streaming_content)
        self.assertTrue(response.closed)
        with ZipFile(BytesIO(payload)) as archive:
            return {name: archive.read(name) for name in archive.namelist()}

    def links(self, response):
        return [urlsplit(html.unescape(value)) for value in re.findall(r'href="([^"]+)"', response.content.decode())]

    def assert_private(self, response, form_page=False):
        self.assertIn('private', response['Cache-Control'])
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response['Referrer-Policy'], 'same-origin' if form_page else 'no-referrer')
        self.assertIn('noindex', response['X-Robots-Tag'])

    def test_quarter_search_keeps_the_period_in_downloads_and_totals(self):
        params = {'periode': 'quarter', 'annee': 2026, 'trimestre': 4}
        response = self.client.get(self.url(**params, q='Béatrice', type='payment'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['period']['kind'], 'quarter')
        self.assertEqual(response.context['summary']['payments_cents'], 17200)
        self.assertEqual([row['order'].pk for row in response.context['movements']], [self.november.pk])
        paths = {reverse('facturation:' + name) for name in ('sales_export', 'invoice_archive', 'accounting_bundle')}
        downloads = [link for link in self.links(response) if link.path in paths]
        self.assertEqual({link.path for link in downloads}, paths)
        for link in downloads:
            self.assertEqual(parse_qs(link.query), {key: [str(value)] for key, value in params.items()})
        files = self.archive(self.client.get(self.url('accounting_bundle', **params, q='introuvable', type='refund')))
        sales = self.csv_rows(files['ventes-2026-T4.csv'])
        self.assertEqual({row['Référence commande'] for row in sales}, {str(self.october.pk), str(self.november.pk)})
        self.assertIn('factures/' + self.invoice.number + '.pdf', files)

    def test_year_export_covers_the_year_without_following_display_filters(self):
        response = self.client.get(self.url('sales_export', periode='year', annee=2026, q='introuvable', type='refund'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('ventes-2026.csv', response['Content-Disposition'])
        rows = self.csv_rows(response.content)
        self.assertEqual({row['Référence commande'] for row in rows}, {str(self.october.pk), str(self.november.pk)})
        self.assert_private(response)

    def test_custom_period_survives_search_pagination_and_reset(self):
        for index in range(26):
            self.order(f'Lot partagé {index}', datetime(2026, 11, 12, 10, tzinfo=UTC))
        params = {'periode': 'custom', 'debut': '2026-10-09', 'fin': '2026-11-13'}
        response = self.client.get(self.url(**params, q='Lot partagé', type='payment'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['movements'].paginator.count, 26)
        next_link = urlsplit(response.context['movements_pagination']['next_url'])
        query = parse_qs(next_link.query)
        for key, value in params.items():
            self.assertEqual(query[key], [value])
        self.assertEqual(query['q'], ['Lot partagé'])
        self.assertEqual(query['page'], ['2'])
        reset = parse_qs(urlsplit(response.context['reset_url']).query)
        self.assertEqual(reset, {key: [value] for key, value in params.items()})
        content = response.content.decode()
        for name, value in params.items():
            self.assertRegex(content, rf'<input[^>]+name="{name}"[^>]+value="{value}"')
        second = self.client.get(reverse('facturation:accounting') + '?' + next_link.query)
        self.assertEqual(len(second.context['movements']), 1)
        self.assertEqual(second.context['summary']['payments_cents'], 28 * 8600)

    def test_custom_single_day_includes_the_entire_last_paris_day(self):
        start = self.order('À minuit', datetime(2026, 10, 24, 22, 0, tzinfo=UTC))
        end = self.order('Dernière seconde', datetime(2026, 10, 25, 22, 59, 59, tzinfo=UTC))
        self.order('Jour suivant', datetime(2026, 10, 25, 23, 0, tzinfo=UTC))
        response = self.client.get(self.url('sales_export', periode='custom', debut='2026-10-25', fin='2026-10-25'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('ventes-2026-10-25_au_2026-10-25.csv', response['Content-Disposition'])
        self.assertEqual({row['Référence commande'] for row in self.csv_rows(response.content)}, {str(start.pk), str(end.pk)})

    def test_invalid_custom_period_cannot_download_or_render_a_report(self):
        invalid = ({'debut': '2026-12-01', 'fin': '2026-10-01'},
                   {'debut': '2026-02-30', 'fin': '2026-03-01'},
                   {'debut': '2025-01-01', 'fin': '2026-01-02'})
        for dates in invalid:
            for route in ('accounting', 'sales_export', 'invoice_archive', 'accounting_bundle'):
                with self.subTest(dates=dates, route=route):
                    response = self.client.get(self.url(route, periode='custom', **dates))
                    self.assertEqual(response.status_code, 400)
                    self.assert_private(response)
                    self.assertNotIn('Content-Disposition', response)

    def create_export(self):
        response = self.client.get(self.url('sales_export', periode='quarter', annee=2026, trimestre=4))
        self.assertEqual(response.status_code, 200)
        return AccountingExport.objects.latest('generated_at')

    def history_url(self, record=None, *, transmission=False):
        name = 'accounting_history' if record is None else ('accounting_export_transmission' if transmission else 'accounting_export_detail')
        return reverse('facturation:' + name, args=[record.pk] if record else [])

    def today(self):
        return timezone.localtime(timezone.now(), PARIS).date()

    def test_successful_exports_record_exact_bytes_period_and_unfiltered_totals(self):
        self.assertEqual(AccountingExport.objects.count(), 0)
        response = self.client.get(self.url('sales_export', periode='quarter', annee=2026, trimestre=4, q='introuvable'))
        record = AccountingExport.objects.get()
        self.assertEqual(record.kind, 'sales')
        self.assertEqual(record.filename, 'ventes-2026-T4.csv')
        self.assertEqual(record.period_params, {'periode': 'quarter', 'annee': 2026, 'trimestre': 4})
        self.assertEqual(record.file_sha256, hashlib.sha256(response.content).hexdigest())
        self.assertEqual(record.file_size, len(response.content))
        self.assertEqual(record.summary['payments_cents'], 17200)
        self.assertEqual(record.summary['payment_count'], 2)
        self.assertFalse(record.is_transmitted)
        for name, kind in (('invoice_archive', 'invoices'), ('accounting_bundle', 'bundle')):
            self.archive(self.client.get(self.url(name, annee=2026, mois=10)))
            self.assertTrue(AccountingExport.objects.filter(kind=kind).exists())
        self.assertEqual(AccountingExport.objects.count(), 3)

    def test_pages_head_requests_and_failed_exports_do_not_create_history(self):
        self.client.get(self.url(annee=2026, mois=10))
        self.client.get(self.history_url())
        for name in ('sales_export', 'invoice_archive', 'accounting_bundle'):
            self.assertEqual(self.client.head(self.url(name, annee=2026, mois=10)).status_code, 405)
            self.assertEqual(self.client.get(self.url(name, annee=2026, mois=99)).status_code, 400)
        self.assertEqual(self.client.get(self.url('accounting_bundle', annee=2025, mois=1)).status_code, 409)
        with patch('facturation.pdf.invoice_pdf', side_effect=DocumentError('Document à vérifier.')):
            self.assertEqual(self.client.get(self.url('invoice_archive', annee=2026, mois=10)).status_code, 409)
            self.assertEqual(self.client.get(self.url('accounting_bundle', annee=2026, mois=10)).status_code, 409)
        self.assertEqual(AccountingExport.objects.count(), 0)

    def test_history_and_details_share_manager_permissions_and_are_private(self):
        record = self.create_export()
        for path in (self.history_url(), self.history_url(record)):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assert_private(response, form_page=path == self.history_url(record))
        for user in (self.customer, self.cataloguer):
            self.client.force_login(user)
            for path in (self.history_url(), self.history_url(record)):
                self.assertEqual(self.client.get(path).status_code, 403)
            self.assertEqual(self.client.post(self.history_url(record, transmission=True),
                {'action': 'sent', 'expected_revision': 0, 'transmitted_on': self.today().isoformat()}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.history_url()).status_code, 302)
        self.assertEqual(self.client.get(self.history_url(record)).status_code, 302)
        self.assertEqual(AccountingExportTransmission.objects.count(), 0)

    @override_settings(LOCAL_SKIP_EMAIL_VERIFICATION=False)
    def test_history_requires_email_access_consistent_with_accounting(self):
        response = self.client.get(self.history_url())
        self.assertRedirects(response, reverse('connexion:verify_email'), fetch_redirect_response=False)

    def test_transmission_is_post_only_and_csrf_protected(self):
        record = self.create_export()
        path = self.history_url(record, transmission=True)
        self.assertEqual(self.client.get(path).status_code, 405)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.manager)
        self.assertEqual(strict.post(path, {'action': 'sent', 'expected_revision': 0,
            'transmitted_on': self.today().isoformat()}).status_code, 403)
        self.assertEqual(AccountingExportTransmission.objects.count(), 0)

    def test_manual_transmission_and_reopen_keep_events_without_sending_mail(self):
        record = self.create_export()
        path = self.history_url(record, transmission=True)
        before = ReturnMail.objects.count()
        note = '<script>justification</script>'
        recipient = 'Cabinet Exemple <comptable@example.test>'
        with patch('retours.mail.deliver_pending', side_effect=AssertionError('No email for a manual note')):
            sent = self.client.post(path, {'action': 'sent', 'expected_revision': 0,
                'transmitted_on': self.today().isoformat(), 'recipient': recipient, 'note': note})
        self.assertEqual(sent.status_code, 302)
        record = AccountingExport.objects.get(pk=record.pk)
        self.assertTrue(record.is_transmitted)
        self.assertEqual(record.transmitted_on, self.today())
        self.assertEqual(record.recipient, recipient)
        self.assertEqual(record.revision, 1)
        detail = self.client.get(self.history_url(record))
        self.assertNotContains(detail, note)
        self.assertContains(detail, html.escape(note))
        self.assertContains(detail, html.escape(recipient))
        reopened = self.client.post(path, {'action': 'reopen', 'expected_revision': 1, 'note': 'Envoi à refaire'})
        self.assertEqual(reopened.status_code, 302)
        record = AccountingExport.objects.get(pk=record.pk)
        self.assertFalse(record.is_transmitted)
        self.assertEqual(record.revision, 2)
        self.assertEqual(list(record.transmissions.order_by('revision').values_list('action', flat=True)), ['sent', 'reopen'])
        self.assertEqual(ReturnMail.objects.count(), before)
        self.assertEqual(AccountingExport.objects.count(), 1)

    def test_stale_transmission_form_cannot_overwrite_a_newer_status(self):
        record = self.create_export()
        path = self.history_url(record, transmission=True)
        data = {'action': 'sent', 'expected_revision': 0, 'transmitted_on': self.today().isoformat()}
        self.assertEqual(self.client.post(path, data).status_code, 302)
        stale = self.client.post(path, {'action': 'reopen', 'expected_revision': 0})
        self.assertEqual(stale.status_code, 409)
        self.assert_private(stale, form_page=True)
        self.assertEqual(AccountingExportTransmission.objects.count(), 1)
        self.assertTrue(AccountingExport.objects.get(pk=record.pk).is_transmitted)

    def test_transmission_rejects_impossible_dates_invalid_actions_and_long_notes(self):
        record = self.create_export()
        path = self.history_url(record, transmission=True)
        base = {'action': 'sent', 'expected_revision': 0, 'transmitted_on': self.today().isoformat()}
        invalid = ({'transmitted_on': (self.today() + timedelta(days=1)).isoformat()},
                   {'transmitted_on': '2000-01-01'}, {'transmitted_on': 'date invalide'},
                   {'action': 'delete'}, {'expected_revision': '-1'}, {'note': 'a' * 1001})
        for values in invalid:
            with self.subTest(values=values):
                response = self.client.post(path, {**base, **values})
                self.assertEqual(response.status_code, 400)
                self.assertNotIn('Content-Disposition', response)
        self.assertEqual(AccountingExportTransmission.objects.count(), 0)

    def test_history_filters_pending_and_sent_without_erasing_entries(self):
        first = self.create_export()
        second = self.create_export()
        self.client.post(self.history_url(first, transmission=True), {'action': 'sent',
            'expected_revision': 0, 'transmitted_on': self.today().isoformat()})
        pending = self.client.get(self.history_url() + '?state=pending')
        sent = self.client.get(self.history_url() + '?state=sent')
        self.assertEqual([row.pk for row in pending.context['exports']], [second.pk])
        self.assertEqual([row.pk for row in sent.context['exports']], [first.pk])
        self.assertEqual(AccountingExport.objects.count(), 2)

    def test_recreating_an_export_is_explicit_and_keeps_the_old_record(self):
        record = self.create_export()
        old_digest = record.file_sha256
        detail = self.client.get(self.history_url(record))
        self.assertContains(detail, 'Recréer')
        matches = [link for link in self.links(detail) if link.path == reverse('facturation:sales_export')]
        self.assertEqual(len(matches), 1)
        self.assertEqual(parse_qs(matches[0].query), {'periode': ['quarter'], 'annee': ['2026'], 'trimestre': ['4']})
        self.order('Nouvelle donnée', datetime(2026, 12, 15, 12, tzinfo=UTC))
        response = self.client.get(matches[0].path + '?' + matches[0].query)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(AccountingExport.objects.count(), 2)
        record.refresh_from_db()
        self.assertEqual(record.file_sha256, old_digest)
        newest = AccountingExport.objects.latest('generated_at')
        self.assertNotEqual(newest.pk, record.pk)
        self.assertEqual(newest.summary['payment_count'], 3)
        self.assertNotEqual(newest.file_sha256, old_digest)
        self.assertFalse(newest.is_transmitted)

    def test_export_history_never_issues_a_document_or_changes_financial_records(self):
        before = (Invoice.objects.count(), list(InvoiceSequence.objects.values_list('year', 'next_number')),
                  Order.objects.count(), ReturnMail.objects.count())
        with patch('facturation.services.issue', side_effect=AssertionError('No invoice issuance')):
            record = self.create_export()
            self.client.get(self.history_url())
            self.client.get(self.history_url(record))
            self.client.post(self.history_url(record, transmission=True), {'action': 'sent',
                'expected_revision': 0, 'transmitted_on': self.today().isoformat()})
        after = (Invoice.objects.count(), list(InvoiceSequence.objects.values_list('year', 'next_number')),
                 Order.objects.count(), ReturnMail.objects.count())
        self.assertEqual(before, after)


    def test_unavailable_history_keeps_download_atomic_and_closes_prepared_archives(self):
        streams = []

        def spool(*args, **kwargs):
            stream = SpooledTemporaryFile(*args, **kwargs)
            streams.append(stream)
            return stream

        with patch('facturation.accounting_views.record_export', side_effect=DatabaseError('History unavailable')), \
             patch('facturation.accounting_exports.SpooledTemporaryFile', side_effect=spool):
            for name in ('sales_export', 'invoice_archive', 'accounting_bundle'):
                response = self.client.get(self.url(name, annee=2026, mois=10))
                self.assertEqual(response.status_code, 503)
                self.assertNotIn('Content-Disposition', response)
                self.assertFalse(response.streaming)
                self.assert_private(response)
        self.assertEqual(len(streams), 2)
        self.assertTrue(all(stream.closed for stream in streams))
        self.assertEqual(AccountingExport.objects.count(), 0)
