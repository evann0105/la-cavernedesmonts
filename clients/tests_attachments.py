"""Private SAV document lifecycle and access boundaries, using synthetic records only."""
import base64
from io import BytesIO
import uuid
from unittest.mock import patch

from PIL import Image
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, transaction
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from connexion.test_helpers import verify_test_user
from .attachments import (MAX_FILE_SIZE, SupportAttachmentForm, add_attachment,
                          attachment_context, prepare_attachment, remove_attachment)
from .models import SupportAttachment, SupportCase
from .services import SupportConflict, SupportError, update_case


def photo(name='defaut.png', image_format='PNG', size=(30, 20), color='red', exif=None):
    buffer = BytesIO()
    image = Image.new('RGB', size, color)
    image.save(buffer, format=image_format, **({'exif': exif} if exif else {}))
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/' + image_format.lower())


def pdf_bytes():
    # Small, structurally complete PDF; no renderer or real customer document needed.
    parts = [b'%PDF-1.4\n']
    offsets = [0]
    for index, value in enumerate((b'<< /Type /Catalog /Pages 2 0 R >>',
                                    b'<< /Type /Pages /Count 0 /Kids [] >>'), 1):
        offsets.append(sum(map(len, parts)))
        parts.append(str(index).encode() + b' 0 obj\n' + value + b'\nendobj\n')
    startxref = sum(map(len, parts))
    parts.append(b'xref\n0 3\n0000000000 65535 f \n')
    parts.extend(f'{offset:010d} 00000 n \n'.encode() for offset in offsets[1:])
    parts.append(b'trailer\n<< /Size 3 /Root 1 0 R >>\nstartxref\n' + str(startxref).encode() + b'\n%%EOF\n')
    return b''.join(parts)


@override_settings(
    DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=False,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class SupportAttachmentTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.manager = User.objects.create_user('attachment-seller', email='seller@example.test', is_staff=True)
        self.manager.user_permissions.add(Permission.objects.get(content_type__app_label='retours', codename='manage_returns'))
        verify_test_user(self.manager)
        self.customer = User.objects.create_user('attachment-customer', email='customer@example.test')
        self.case = self.make_case()
        self.client.force_login(self.manager, backend='connexion.backends.EmailOrUsernameBackend')

    def make_case(self):
        return SupportCase.objects.create(creation_key=uuid.uuid4(), creation_fingerprint='a' * 64,
            user=self.customer, customer_name='Camille Test', customer_email=self.customer.email,
            phone='0600000000', subject='Vérification', description='Vérifier une couture.')

    def data(self, **changes):
        values = {'submission_key': uuid.uuid4(), 'revision': self.case.revision, 'title': 'Photo de la couture'}
        values.update(changes)
        return values

    def add(self, **changes):
        return add_attachment(self.manager, self.case.pk, self.data(**changes), {'file': photo()})[0]

    def url(self, name, attachment=None, case=None):
        args = [case.pk if case else self.case.pk]
        if attachment:
            args.append(attachment.pk)
        return reverse('clients:attachment_' + name, args=args)

    def test_images_are_normalized_metadata_stripped_and_filenames_safe(self):
        exif = Image.Exif()
        exif[270] = 'private-camera-location'
        prepared = prepare_attachment(photo('../couture.jpg', 'JPEG', size=(2500, 20), exif=exif))
        self.assertEqual(prepared.content_type, 'image/webp')
        self.assertEqual(prepared.filename, 'couture.webp')
        with Image.open(BytesIO(prepared.content)) as normalized:
            self.assertEqual(normalized.format, 'WEBP')
            self.assertLessEqual(normalized.width, 2400)
            self.assertFalse(normalized.getexif())
        self.assertNotIn(b'private-camera-location', prepared.content)
        for image_format in ('PNG', 'WEBP'):
            with self.subTest(image_format=image_format):
                self.assertEqual(prepare_attachment(photo(image_format=image_format)).content_type, 'image/webp')
        unusual = photo('C:\\secret\\couture\r\n.webp')
        self.assertEqual(prepare_attachment(unusual).filename, 'couture.webp')

    def test_pdf_uses_actual_content_and_rejects_truncated_or_disguised_documents(self):
        prepared = prepare_attachment(SimpleUploadedFile('justificatif.jpg', pdf_bytes(), content_type='image/jpeg'))
        self.assertEqual((prepared.content, prepared.filename, prepared.content_type),
                         (pdf_bytes(), 'justificatif.pdf', 'application/pdf'))
        for content in (b'<svg onload="alert(1)"></svg>', b'<html>not a PDF</html>',
                        b'%PDF-1.7\nnot a PDF\n%%EOF', pdf_bytes()[:-8],
                        pdf_bytes().replace(b'startxref\n', b'startxref\n99999')):
            with self.subTest(content=content[:20]), self.assertRaises(ValidationError):
                prepare_attachment(SimpleUploadedFile('preuve.pdf', content, content_type='application/pdf'))
        with self.assertRaises(ValidationError):
            prepare_attachment(photo('animation.gif', image_format='GIF'))

    def test_empty_oversized_and_false_declared_size_are_rejected_without_writes(self):
        large = SimpleUploadedFile('large.pdf', b'x' * (MAX_FILE_SIZE + 1))
        # Actual bytes are bounded even if a caller spoofs file.size.
        large.size = 1
        for upload in (large, SimpleUploadedFile('empty.pdf', b'')):
            form = SupportAttachmentForm(self.data(), {'file': upload})
            self.assertFalse(form.is_valid())
            self.assertIn('file', form.errors)
        self.assertFalse(SupportAttachment.objects.exists())
        self.assertFalse(self.case.events.exists())

    def test_upload_encrypts_payload_and_metadata_and_updates_case_once_without_mail(self):
        previous = self.case.updated_at
        attachment = self.add()
        self.case.refresh_from_db()
        self.assertEqual(self.case.revision, 2)
        self.assertGreater(self.case.updated_at, previous)
        self.assertEqual(self.case.updated_by_id, self.manager.pk)
        self.assertEqual(self.case.state, 'open')
        self.assertEqual(self.case.events.get().kind, 'update')
        self.assertIn('Photo de la couture', self.case.events.get().summary)
        with connection.cursor() as cursor:
            cursor.execute('SELECT title, filename, payload FROM clients_supportattachment WHERE id = %s',
                           [attachment.pk.hex])
            raw_values = cursor.fetchone()
        self.assertTrue(all(value.startswith('enc:v1:') for value in raw_values))
        self.assertNotIn(attachment.title, ''.join(raw_values))
        self.assertNotIn(base64.b64encode(b'RIFF').decode(), raw_values[-1])
        self.assertEqual(len(base64.b64decode(attachment.payload)), attachment.byte_size)
        self.assertFalse(mail.outbox)

    def test_double_submission_reuses_file_and_rejects_changed_content_actor_or_case(self):
        values = self.data()
        first, repeated = add_attachment(self.manager, self.case.pk, values, {'file': photo()})
        second, repeated_again = add_attachment(self.manager, self.case.pk, values, {'file': photo()})
        self.assertFalse(repeated)
        self.assertTrue(repeated_again)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(self.case.events.count(), 1)
        self.case.refresh_from_db()
        self.assertEqual(self.case.revision, 2)
        for changes, upload, case in (({'title': 'Changed'}, photo(), self.case),
                                     ({}, photo(color='green'), self.case),
                                     ({}, photo(), self.make_case())):
            with self.subTest(changes=changes, case=case.pk), self.assertRaises(SupportConflict):
                add_attachment(self.manager, case.pk, {**values, **changes}, {'file': upload})
        other_manager = get_user_model().objects.create_superuser('attachment-owner', email='other@example.test')
        verify_test_user(other_manager)
        with self.assertRaises(SupportConflict):
            add_attachment(other_manager, self.case.pk, values, {'file': photo()})
        self.assertEqual(SupportAttachment.objects.count(), 1)

    def test_stale_upload_and_stale_case_update_are_blocked(self):
        values = self.data()
        attachment = self.add()
        with self.assertRaises(SupportConflict):
            add_attachment(self.manager, self.case.pk, values, {'file': photo()})
        with self.assertRaises(SupportConflict):
            update_case(self.manager, self.case.pk, {'revision': 1, 'state': 'open', 'priority': 'normal',
                                                   'due_date': None, 'note': 'ancienne note'})
        self.assertEqual(SupportAttachment.objects.count(), 1)
        self.assertEqual(self.case.events.count(), 1)
        self.assertEqual(attachment.case_id, self.case.pk)

    def test_soft_remove_preserves_evidence_and_audit_and_retry_does_not_restore_it(self):
        values = self.data()
        attachment, _ = add_attachment(self.manager, self.case.pk, values, {'file': photo()})
        self.case.refresh_from_db()
        remove_data = {'revision': self.case.revision, 'confirm': True}
        removed, repeated = remove_attachment(self.manager, self.case.pk, attachment.pk, remove_data)
        self.assertFalse(repeated)
        self.assertIsNotNone(removed.removed_at)
        self.assertEqual(removed.removed_by_id, self.manager.pk)
        _, repeated = remove_attachment(self.manager, self.case.pk, attachment.pk, remove_data)
        self.assertTrue(repeated)
        replay, repeated = add_attachment(self.manager, self.case.pk, values, {'file': photo()})
        self.assertTrue(repeated)
        self.assertIsNotNone(replay.removed_at)
        attachment.refresh_from_db()
        self.assertTrue(attachment.payload)
        self.case.refresh_from_db()
        self.assertEqual(self.case.revision, 3)
        self.assertEqual(self.case.events.count(), 2)
        self.assertIn('Document retiré', self.case.events.first().summary)
        self.assertEqual(self.client.get(self.url('download', attachment)).status_code, 404)

    def test_remove_requires_confirmation_current_revision_and_matching_case(self):
        attachment = self.add()
        for values in ({'revision': 2, 'confirm': False}, {'revision': 1, 'confirm': True}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                remove_attachment(self.manager, self.case.pk, attachment.pk, values)
        with self.assertRaises(SupportError):
            remove_attachment(self.manager, self.make_case().pk, attachment.pk, {'revision': 1, 'confirm': True})
        attachment.refresh_from_db()
        self.assertIsNone(attachment.removed_at)

    def test_case_volume_limits_include_retired_files_and_replay_does_not_consume_quota(self):
        values = self.data()
        with patch('clients.attachments.MAX_CASE_FILES', 1):
            attachment, _ = add_attachment(self.manager, self.case.pk, values, {'file': photo()})
            self.assertTrue(add_attachment(self.manager, self.case.pk, values, {'file': photo()})[1])
            self.case.refresh_from_db()
            remove_attachment(self.manager, self.case.pk, attachment.pk, {'revision': self.case.revision, 'confirm': True})
            self.case.refresh_from_db()
            with self.assertRaises(SupportError):
                self.add()
            self.assertTrue(attachment_context(self.case)['attachment_limit_reached'])
        with patch('clients.attachments.MAX_CASE_BYTES', attachment.byte_size + 1):
            with self.assertRaises(SupportError):
                self.add()
        self.assertEqual(SupportAttachment.objects.count(), 1)

    def test_validation_failure_does_not_break_callers_transaction(self):
        with transaction.atomic():
            try:
                add_attachment(self.manager, self.case.pk, self.data(),
                               {'file': SimpleUploadedFile('bad.pdf', b'not-a-file')})
            except ValidationError:
                pass
            self.assertEqual(SupportCase.objects.count(), 1)
            attachment = self.add()
        self.assertTrue(SupportAttachment.objects.filter(pk=attachment.pk).exists())

    def test_lists_defer_payload_and_separate_retired_files(self):
        attachment = self.add()
        with CaptureQueriesContext(connection) as queries:
            context = attachment_context(self.case)
            rows = list(context['attachments'])
            retired = list(context['retired_attachments'])
        self.assertEqual([row.pk for row in rows], [attachment.pk])
        self.assertEqual(retired, [])
        self.assertIn('payload', rows[0].get_deferred_fields())
        self.assertTrue(all('"payload"' not in query['sql'] for query in queries))
        self.assertEqual(context['attachment_total_count'], 1)

    def test_service_authorization_precedes_file_read(self):
        upload = photo()
        with patch('clients.attachments.prepare_attachment', side_effect=AssertionError('unauthorized read')):
            with self.assertRaises(PermissionDenied):
                add_attachment(self.customer, self.case.pk, self.data(), {'file': upload})
        attachment = self.add()
        with self.assertRaises(PermissionDenied):
            remove_attachment(self.customer, self.case.pk, attachment.pk, {'revision': 2, 'confirm': True})

    def test_http_access_controls_run_before_any_attachment_query(self):
        attachment = self.add()
        self.client.logout()
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.client.get(self.url('download', attachment)).status_code, 302)
        self.assertTrue(all('clients_supportattachment' not in query['sql'] for query in queries))
        staff = get_user_model().objects.create_user('attachment-no-permission', email='staff@example.test', is_staff=True)
        for user in (self.customer, staff):
            self.client.force_login(user, backend='connexion.backends.EmailOrUsernameBackend')
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(self.client.get(self.url('download', attachment)).status_code, 403)
                self.assertEqual(self.client.post(self.url('add'), {}).status_code, 403)
                self.assertEqual(self.client.post(self.url('remove', attachment), {}).status_code, 403)
            self.assertTrue(all('clients_supportattachment' not in query['sql'] for query in queries))
        self.client.force_login(self.manager, backend='connexion.backends.EmailOrUsernameBackend')
        self.manager.email_verification.delete()
        self.assertEqual(self.client.get(self.url('download', attachment)).status_code, 302)

    def test_http_pdf_download_is_forced_private_and_scoped_to_the_case(self):
        attachment, _ = add_attachment(self.manager, self.case.pk, self.data(title='Justificatif'),
            {'file': SimpleUploadedFile('preuve.pdf', pdf_bytes(), content_type='application/pdf')})
        response = self.client.get(self.url('download', attachment))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, pdf_bytes())
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('private', response['Cache-Control'])
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertIn('sandbox', response['Content-Security-Policy'])
        self.assertIn('noindex', response['X-Robots-Tag'])
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.client.get(self.url('download', attachment, self.make_case())).status_code, 404)
        self.assertTrue(all('"payload"' not in query['sql'] for query in queries))

    def test_http_upload_remove_errors_methods_and_csrf(self):
        self.assertEqual(self.client.get(self.url('add')).status_code, 405)
        response = self.client.post(self.url('add'), {**self.data(), 'file': photo()})
        self.assertEqual(response.status_code, 302)
        attachment = SupportAttachment.objects.get()
        self.assertEqual(self.client.get(self.url('remove', attachment)).status_code, 405)
        response = self.client.post(self.url('add'), {**self.data(), 'file': SimpleUploadedFile('bad.pdf', b'bad')})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'La pièce jointe n’a pas été ajoutée', status_code=400)
        response = self.client.post(self.url('remove', attachment), {'revision': 2})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'Le document n’a pas été retiré', status_code=400)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.manager, backend='connexion.backends.EmailOrUsernameBackend')
        self.assertEqual(csrf_client.post(self.url('remove', attachment), {'revision': 2, 'confirm': 'on'}).status_code, 403)
        response = self.client.post(self.url('remove', attachment), {'revision': 2, 'confirm': 'on'})
        self.assertEqual(response.status_code, 302)
        attachment.refresh_from_db()
        self.assertIsNotNone(attachment.removed_at)
        self.assertFalse(mail.outbox)
