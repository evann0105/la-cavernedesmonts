from datetime import date, datetime, timedelta, timezone as dt_timezone
from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase, override_settings

from paiement.models import Order
from retours.models import ReturnRequest, ReturnLine
from .accounting import period, report
from .accounting_exports import dossier_archive
from .accounting_periods import PARIS, select_period
from .models import Invoice
from .test_accounting_bundle import sample_report
from .test_pdf import sample_invoice


UTC = dt_timezone.utc


def utc(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


class AccountingPeriodSelectionTests(SimpleTestCase):
    def test_month_keeps_original_values_and_urls(self):
        selected = period(2026, 10)
        self.assertEqual(selected['kind'], 'month')
        self.assertEqual(selected['params'], {'annee': 2026, 'mois': 10})
        self.assertEqual(selected['start_date'], date(2026, 10, 1))
        self.assertEqual(selected['end_date'], date(2026, 10, 31))
        self.assertEqual(selected['label'], 'Octobre 2026')
        self.assertEqual(selected['slug'], '2026-10')

    def test_missing_kind_defaults_to_current_month_and_quarter_defaults_correctly(self):
        today = date(2026, 11, 12)
        self.assertEqual(select_period({}, today=today), period(2026, 11))
        quarter = select_period({'periode': 'quarter'}, today=today)
        self.assertEqual(quarter['quarter'], 4)
        self.assertEqual(quarter['params'], {'periode': 'quarter', 'annee': 2026, 'trimestre': 4})

    def test_quarter_has_calendar_dates_and_handles_year_end(self):
        for quarter, month, last_month, last_day in ((1, 1, 3, 31), (2, 4, 6, 30), (3, 7, 9, 30), (4, 10, 12, 31)):
            with self.subTest(quarter=quarter):
                selected = select_period({'periode': 'quarter', 'annee': '2026', 'trimestre': str(quarter)})
                self.assertEqual(selected['start_date'], date(2026, month, 1))
                self.assertEqual(selected['end_date'], date(2026, last_month, last_day))
                self.assertEqual(selected['end'].date(), selected['end_date'] + timedelta(days=1))
                self.assertEqual(selected['slug'], f'2026-T{quarter}')
        last = select_period({'periode': 'quarter', 'annee': 2100, 'trimestre': 4})
        self.assertEqual(last['end'].date(), date(2101, 1, 1))

    def test_year_includes_leap_day_and_retains_only_relevant_inputs(self):
        selected = select_period({'periode': 'year', 'annee': 2024, 'mois': 'bad', 'debut': 'bad', 'q': 'private'})
        self.assertEqual((selected['end_date'] - selected['start_date']).days + 1, 366)
        self.assertEqual(selected['label'], 'Année 2024')
        self.assertEqual(selected['params'], {'periode': 'year', 'annee': 2024})
        self.assertEqual(selected['slug'], '2024')

    def test_custom_range_is_inclusive_can_cross_years_and_preserves_only_dates(self):
        selected = select_period({'periode': 'custom', 'debut': '2025-12-15', 'fin': '2026-01-15',
                                  'annee': 'bad', 'mois': 'bad', 'q': 'private'})
        self.assertEqual(selected['start'].date(), date(2025, 12, 15))
        self.assertEqual(selected['end'].date(), date(2026, 1, 16))
        self.assertEqual(selected['slug'], '2025-12-15_au_2026-01-15')
        self.assertEqual(selected['label'], 'Du 15/12/2025 au 15/01/2026')
        self.assertEqual(selected['params'], {'periode': 'custom', 'debut': '2025-12-15', 'fin': '2026-01-15'})
        self.assertEqual((selected['year'], selected['month'], selected['quarter']), (2025, 12, 4))

    @override_settings(TIME_ZONE='UTC')
    def test_single_day_at_dst_switch_has_the_correct_elapsed_hours(self):
        for day, hours in (('2026-03-29', 23), ('2026-10-25', 25)):
            with self.subTest(day=day):
                selected = select_period({'periode': 'custom', 'debut': day, 'fin': day})
                elapsed = selected['end'].astimezone(UTC) - selected['start'].astimezone(UTC)
                self.assertEqual(elapsed, timedelta(hours=hours))
                self.assertEqual(selected['start'].tzinfo, PARIS)

    def test_custom_accepts_366_days_but_not_367(self):
        selected = select_period({'periode': 'custom', 'debut': '2024-01-01', 'fin': '2024-12-31'})
        self.assertEqual(selected['end_date'], date(2024, 12, 31))
        with self.assertRaisesMessage(ValidationError, '366 jours maximum'):
            select_period({'periode': 'custom', 'debut': '2024-01-01', 'fin': '2025-01-01'})

    def test_invalid_or_empty_supplied_inputs_never_fall_back_to_current_period(self):
        invalid = [None, {}, {'periode': ''}, {'periode': 'weekly'},
            {'periode': 'month', 'annee': ''}, {'periode': 'month', 'mois': ''},
            {'periode': 'year', 'annee': True}, {'periode': 'year', 'annee': 2026.0},
            {'periode': 'year', 'annee': 1999}, {'periode': 'year', 'annee': 2101},
            {'periode': 'quarter', 'trimestre': 0}, {'periode': 'quarter', 'trimestre': 5},
            {'periode': 'quarter', 'trimestre': None}, {'periode': 'quarter', 'annee': '2026;DROP'},
            {'periode': 'custom'}, {'periode': 'custom', 'debut': '', 'fin': ''}]
        # Empty mapping is the one intentional default; all malformed supplied choices fail.
        for params in [item for item in invalid if item != {}]:
            with self.subTest(params=params), self.assertRaises(ValidationError):
                select_period(params)
        for start, end in (('2026-02-29', '2026-03-01'), ('2026-04-31', '2026-05-01'),
                           ('20261001', '20261002'), ('2026-01-01T00:00:00', '2026-01-02'),
                           ('2026-01-02', '2026-01-01'), ('1999-12-31', '2000-01-01'),
                           ('2100-12-31', '2101-01-01')):
            with self.subTest(start=start, end=end), self.assertRaises(ValidationError):
                select_period({'periode': 'custom', 'debut': start, 'fin': end})

    def test_archives_use_period_slug_and_period_wording(self):
        selections = [
            {'periode': 'quarter', 'annee': 2026, 'trimestre': 4},
            {'periode': 'year', 'annee': 2026},
            {'periode': 'custom', 'debut': '2026-10-01', 'fin': '2026-10-31'},
        ]
        for params in selections:
            with self.subTest(params=params):
                selected = select_period(params)
                data = sample_report(invoices=[sample_invoice()])
                data['period'] = selected
                stream = dossier_archive(data)
                try:
                    with ZipFile(BytesIO(stream.read())) as archive:
                        self.assertIn(f'ventes-{selected["slug"]}.csv', archive.namelist())
                        recap = archive.read('RECAPITULATIF.txt').decode()
                        readme = archive.read('A-LIRE.txt').decode()
                        self.assertIn(selected['label'], recap)
                        self.assertIn('totalité de la période choisie', recap)
                        self.assertIn('FACTURES ÉMISES PENDANT LA PÉRIODE', recap)
                        self.assertIn('totalité de la période choisie', readme)
                finally:
                    stream.close()


@override_settings(DEBUG=True, EMAIL_TEST_MODE=True)
class AccountingPeriodReportTests(TestCase):
    def order(self, **changes):
        fields = {'status': 'paid', 'session_key': 'accounting-range-test', 'paid_at': utc(2026, 10, 15),
                  'customer_email': 'range@example.test', 'billing_details': {'name': 'Client période'},
                  'items': [{'name': 'Polaire', 'quantity': 1, 'unit_amount': 6000}],
                  'subtotal': 6000, 'total_paid': 6000, 'paid_line_totals': [6000], 'return_shipping_amount': 0}
        fields.update(changes)
        return Order.objects.create(**fields)

    def invoice(self, order, issued_at):
        invoice = sample_invoice()
        snapshot = dict(invoice.snapshot, order_reference=str(order.pk))
        return Invoice.objects.create(order=order, number=invoice.number, issued_at=issued_at, snapshot=snapshot)

    def refund(self, order, refunded_at):
        refund = ReturnRequest.objects.create(order=order, submission_id=uuid4(), amount=1200,
            shipping_amount=0, refund_state='succeeded', refunded_at=refunded_at)
        ReturnLine.objects.create(request=refund, item_index=0, name='Polaire', size='M', color='Vert',
                                  quantity=1, amount=1200)
        return refund

    def test_quarter_counts_cash_and_issued_documents_once_with_independent_dates(self):
        selected = select_period({'periode': 'quarter', 'annee': 2026, 'trimestre': 4})
        self.order(paid_at=utc(2026, 9, 30, 21, 59))
        first = self.order(paid_at=utc(2026, 9, 30, 22))
        last = self.order(paid_at=utc(2026, 12, 31, 22, 59))
        self.order(paid_at=utc(2026, 12, 31, 23))
        old = self.order(paid_at=utc(2026, 8, 1))
        refund = self.refund(old, utc(2026, 11, 15))
        invoice = self.invoice(old, utc(2026, 10, 2))
        self.order(paid_at=None)
        data = report(selection=selected)
        self.assertEqual({row['order'].pk for row in data['rows'] if row['kind'] == 'payment'}, {first.pk, last.pk})
        self.assertEqual([row['refund'].pk for row in data['rows'] if row['kind'] == 'refund'], [refund.pk])
        self.assertEqual([document.pk for document in data['invoices']], [invoice.pk])
        self.assertEqual(data['summary']['payments_cents'], 12000)
        self.assertEqual(data['summary']['refunds_cents'], 1200)
        self.assertEqual(data['undated_orders_count'], 1)
        self.assertEqual(len(data['warnings']), 1)

    def test_custom_last_day_is_wholly_included_during_spring_and_autumn_dst(self):
        for day, before, first, last, after in (
            ('2026-03-29', utc(2026, 3, 28, 22, 59), utc(2026, 3, 28, 23), utc(2026, 3, 29, 21, 59), utc(2026, 3, 29, 22)),
            ('2026-10-25', utc(2026, 10, 24, 21, 59), utc(2026, 10, 24, 22), utc(2026, 10, 25, 22, 59), utc(2026, 10, 25, 23)),
        ):
            with self.subTest(day=day):
                self.order(paid_at=before)
                included = [self.order(paid_at=first), self.order(paid_at=last)]
                self.order(paid_at=after)
                selected = select_period({'periode': 'custom', 'debut': day, 'fin': day})
                data = report(selection=selected)
                self.assertEqual([row['order'].pk for row in data['rows']], [order.pk for order in included])

    def test_year_includes_leap_day_and_excludes_following_paris_year(self):
        included = [self.order(paid_at=utc(2023, 12, 31, 23)), self.order(paid_at=utc(2024, 2, 29, 12))]
        self.order(paid_at=utc(2024, 12, 31, 23))
        data = report(selection=select_period({'periode': 'year', 'annee': 2024}))
        self.assertEqual([row['order'].pk for row in data['rows']], [order.pk for order in included])
        self.assertEqual(data['summary']['payments_cents'], 12000)

    def test_report_rebuilds_bounds_and_label_from_canonical_period_params(self):
        self.order(paid_at=utc(2026, 9, 1))
        wanted = self.order(paid_at=utc(2026, 10, 1))
        selected = period(2026, 10)
        selected.update(start=utc(1900, 1, 1), end=utc(2200, 1, 1), label='Untrusted', slug='../../file')
        data = report(selection=selected)
        self.assertEqual([row['order'].pk for row in data['rows']], [wanted.pk])
        self.assertEqual(data['period'], period(2026, 10))
        for invalid in ({'start': selected['start'], 'end': selected['end']}, {'params': {'periode': 'year', 'annee': 0}}):
            with self.assertRaises(ValidationError):
                report(selection=invalid)

    def test_incomplete_payment_warns_about_period_without_inventing_an_amount(self):
        order = self.order(total_paid=None)
        data = report(selection=select_period({'periode': 'year', 'annee': 2026}))
        self.assertEqual(data['incomplete_orders'], [order])
        self.assertEqual(data['summary']['payments_cents'], 0)
        self.assertIn('de la période sans montant confirmé', data['warnings'][0])
