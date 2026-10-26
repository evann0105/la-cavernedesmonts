"""Export metadata and manual declarations do not rewrite financial records."""
import hashlib
from datetime import date, datetime, timedelta, timezone as datetime_timezone
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import TestCase, override_settings

from .accounting import period
from .accounting_history import export_history_queryset, record_export, transmission_service
from .models import AccountingExport, AccountingExportTransmission, Invoice, InvoiceSequence
from .test_accounting_bundle import movement, sample_order, sample_report


UTC = datetime_timezone.utc
NOW = datetime(2026, 10, 26, 12, 34, 56, tzinfo=UTC)
TODAY = date(2026, 10, 26)


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True, EMAIL_DELIVERY_ENABLED=False)
class AccountingHistoryTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user('export-history-manager',
            email='private-manager@example.test', first_name='Élodie', last_name='Responsable', is_staff=True)
        self.manager.user_permissions.add(Permission.objects.get(content_type__app_label='retours', codename='manage_returns'))
        self.customer = get_user_model().objects.create_user('export-history-customer')
        self.data = sample_report(rows=[movement(sample_order(), notes='Facture non émise.')],
                                  missing=[sample_order()], warnings=['Une information reste à vérifier.'])
        self.filename = 'dossier-comptable-2026-10.zip'
        self.content = b'finished-private-test-archive'
        self.clock = patch('facturation.accounting_history.timezone.now', return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def record(self, **changes):
        args = dict(data=self.data, kind='bundle', filename=self.filename, content=self.content, user=self.manager)
        args.update(changes)
        return record_export(**args)

    def send(self, record, **changes):
        args = dict(record_id=record.pk, user=self.manager, action='sent', sent_on=TODAY,
                    recipient='Comptable de test', note='Dossier remis par le canal habituel.',
                    expected_revision=record.revision)
        args.update(changes)
        return transmission_service(**args)

    def test_record_saves_digest_period_and_immutable_summary_without_customer_rows(self):
        record = self.record()
        record.refresh_from_db()
        self.assertEqual(record.filename, self.filename)
        self.assertEqual(record.file_size, len(self.content))
        self.assertEqual(record.file_sha256, hashlib.sha256(self.content).hexdigest())
        self.assertEqual(record.generated_at, NOW)
        self.assertEqual(record.start_date, date(2026, 10, 1))
        self.assertEqual(record.end_date, date(2026, 10, 31))
        self.assertEqual(record.period_params, {'annee': 2026, 'mois': 10})
        self.assertEqual(record.actor_identifier, str(self.manager.pk))
        self.assertEqual(record.actor_label, 'Élodie Responsable')
        self.assertEqual(record.summary['payments_cents'], 6000)
        self.assertEqual(record.summary['payments'], '60.00')
        self.assertEqual(record.summary['missing_invoice_count'], 1)
        self.assertEqual(record.summary['warning_count'], 1)
        self.assertNotIn('customer_email', str(record.summary))
        self.assertNotIn('client@example.test', str(record.summary))
        self.assertFalse(record.is_transmitted)
        self.assertEqual(record.revision, 0)
        self.assertIsNone(record.latest_event)
        self.assertFalse(Invoice.objects.exists())
        self.assertFalse(InvoiceSequence.objects.exists())

    def test_fresh_generation_produces_distinct_history_even_for_identical_bytes(self):
        first, second = self.record(), self.record()
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(first.file_sha256, second.file_sha256)
        self.assertEqual(AccountingExport.objects.count(), 2)

    def test_file_content_is_read_in_chunks_rewound_and_not_closed(self):
        class Chunked(BytesIO):
            def read(self, size=-1):
                if size < 0 or size > 1024 * 1024:
                    raise AssertionError('Unbounded read')
                return super().read(size)
        content = Chunked(b'A' * (2 * 1024 * 1024 + 51))
        content.seek(42)
        record = self.record(content=content)
        self.assertEqual(content.tell(), 0)
        self.assertFalse(content.closed)
        self.assertEqual(record.file_size, 2 * 1024 * 1024 + 51)
        self.assertEqual(record.file_sha256, hashlib.sha256(content.getvalue()).hexdigest())
        content.close()

    def test_file_failure_does_not_record_an_export_and_rewinds(self):
        class Broken(BytesIO):
            def read(self, size=-1):
                super().read(2)
                raise OSError('Fichier incomplet')
        content = Broken(b'private')
        with self.assertRaises(OSError):
            self.record(content=content)
        self.assertEqual(content.tell(), 0)
        self.assertFalse(content.closed)
        self.assertFalse(AccountingExport.objects.exists())
        content.close()

    def test_disallows_invalid_type_and_unsafe_filenames(self):
        for changes in ({'kind': 'unknown'}, {'filename': '../secret.zip'}, {'filename': 'name\r\n.zip'}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.record(**changes)
        self.assertFalse(AccountingExport.objects.exists())

    def test_only_canonical_period_parameters_enter_history(self):
        self.data['period']['params'] = {'annee': 2026, 'mois': 10, 'q': 'Private Name',
                                         'type': 'refund', 'page': 2, 'unrelated': 'secret'}
        record = self.record()
        self.assertEqual(record.period_params, {'annee': 2026, 'mois': 10})

    def test_sensitive_metadata_is_encrypted_at_rest(self):
        record = self.send(self.record(), recipient='private-comptable@example.test', note='Private audit note')
        with connection.cursor() as cursor:
            cursor.execute('SELECT actor_label, summary FROM facturation_accountingexport')
            values = cursor.fetchone()
            cursor.execute('SELECT actor_label, recipient, note FROM facturation_accountingexporttransmission')
            values += cursor.fetchone()
        self.assertTrue(all(value.startswith('enc:v1:') for value in values))
        self.assertNotIn('Private audit note', str(values))
        self.assertEqual(record.recipient, 'private-comptable@example.test')
        self.assertEqual(record.latest_event.note, 'Private audit note')

    def test_permission_required_for_generation_and_transmission(self):
        with self.assertRaises(PermissionDenied):
            self.record(user=self.customer)
        record = self.record()
        with self.assertRaises(PermissionDenied):
            self.send(record, user=self.customer)
        self.assertFalse(AccountingExportTransmission.objects.exists())

    def test_mark_transmitted_records_a_declaration_without_any_send(self):
        original = self.record()
        with patch('django.core.mail.send_mail') as mail:
            record = self.send(original)
        self.assertTrue(record.is_transmitted)
        self.assertEqual(record.revision, 1)
        self.assertEqual(record.transmitted_on, TODAY)
        self.assertEqual(record.recipient, 'Comptable de test')
        self.assertEqual(record.latest_event.recorded_at, NOW)
        self.assertEqual(record.latest_event.actor_identifier, str(self.manager.pk))
        self.assertEqual(record.summary, original.summary)
        self.assertEqual(AccountingExport.objects.count(), 1)
        mail.assert_not_called()

    def test_reopen_keeps_previous_declaration_and_can_be_sent_again(self):
        record = self.send(self.record())
        reopened = transmission_service(record.pk, self.manager, action='reopen', expected_revision=1,
                                        note='Date à revérifier')
        self.assertFalse(reopened.is_transmitted)
        self.assertEqual(reopened.revision, 2)
        self.assertIsNone(reopened.transmitted_on)
        self.assertEqual(reopened.recipient, '')
        self.assertEqual(reopened.latest_event.note, 'Date à revérifier')
        self.assertEqual(reopened.transmissions.count(), 2)
        self.assertEqual(reopened.transmissions.get(revision=1).recipient, 'Comptable de test')
        sent_again = self.send(reopened, recipient='Autre comptable')
        self.assertEqual(sent_again.revision, 3)
        self.assertEqual(sent_again.recipient, 'Autre comptable')
        self.assertEqual(sent_again.transmissions.count(), 3)

    def test_date_must_be_calendar_date_between_generation_and_today(self):
        record = self.record()
        for invalid in (None, '', '2026-10-26', NOW, TODAY - timedelta(days=1), TODAY + timedelta(days=1)):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                self.send(record, sent_on=invalid)
        self.assertFalse(AccountingExportTransmission.objects.exists())

    def test_date_validation_uses_paris_day_not_utc_day(self):
        with patch('facturation.accounting_history.timezone.now', return_value=datetime(2026, 10, 25, 23, 30, tzinfo=UTC)):
            record = self.record()
            self.send(record, sent_on=TODAY)

    def test_stale_revision_and_double_submission_do_not_duplicate_declarations(self):
        original = self.record()
        self.send(original)
        with self.assertRaises(ValidationError) as caught:
            self.send(original, expected_revision=0)
        self.assertEqual(caught.exception.code, 'stale')
        current = export_history_queryset().get(pk=original.pk)
        with self.assertRaises(ValidationError):
            self.send(current)
        self.assertEqual(AccountingExportTransmission.objects.count(), 1)

    def test_required_revision_and_state_transitions_are_strict(self):
        record = self.record()
        for revision in (None, '', True, -1, '1.0', 'not-number'):
            with self.subTest(revision=revision), self.assertRaises(ValidationError):
                self.send(record, expected_revision=revision)
        with self.assertRaises(ValidationError):
            transmission_service(record.pk, self.manager, action='reopen', expected_revision=0)
        with self.assertRaises(ValidationError):
            transmission_service(record.pk, self.manager, action='unknown', expected_revision=0)
        self.assertFalse(AccountingExportTransmission.objects.exists())

    def test_recipient_and_note_limits_are_enforced_in_service(self):
        record = self.record()
        for changes in ({'recipient': 'a' * 201}, {'note': 'a' * 1001}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.send(record, **changes)
        self.assertFalse(AccountingExportTransmission.objects.exists())

    def test_history_filter_uses_latest_event_not_any_past_transmission(self):
        pending = self.record()
        sent = self.send(self.record())
        reopened = self.send(self.record())
        transmission_service(reopened.pk, self.manager, action='reopen', expected_revision=1)
        self.assertEqual(set(export_history_queryset('pending').values_list('pk', flat=True)), {pending.pk, reopened.pk})
        self.assertEqual(list(export_history_queryset('sent').values_list('pk', flat=True)), [sent.pk])
        with self.assertNumQueries(2):
            records = list(export_history_queryset())
            states = [(entry.revision, entry.is_transmitted, entry.transmitted_on, entry.recipient) for entry in records]
        self.assertEqual(len(states), 3)

    def test_records_and_events_cannot_be_rewritten_or_deleted(self):
        record = self.send(self.record())
        event = record.latest_event
        for obj in (record, event):
            with self.subTest(model=type(obj).__name__):
                with self.assertRaises(ValidationError):
                    obj.save()
                with self.assertRaises(ValidationError):
                    obj.delete()
                with self.assertRaises(ValidationError):
                    type(obj).objects.filter(pk=obj.pk).delete()
                with self.assertRaises(ValidationError):
                    type(obj).objects.filter(pk=obj.pk).update(actor_identifier='changed')
                with self.assertRaises(ValidationError):
                    type(obj).objects.bulk_update([obj], ['actor_identifier'])
        record.refresh_from_db()
        self.assertEqual(record.actor_identifier, str(self.manager.pk))
        self.assertEqual(AccountingExportTransmission.objects.count(), 1)
