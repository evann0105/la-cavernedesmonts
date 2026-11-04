from datetime import datetime, timedelta, timezone as datetime_timezone
import hashlib
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.core import mail
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from . import password_reset as recovery
from .models import EmailVerification, PasswordResetMail, PasswordResetState, SignupQuota
from .test_helpers import verify_test_user

CONFIG = dict(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=False, EMAIL_TEST_MODE=False,
    EMAIL_DELIVERY_ENABLED=True, DEFAULT_FROM_EMAIL='boutique@example.test',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', SITE_URL='http://localhost:8000',
    PASSWORD_RESET_TIMEOUT=3600, PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
OLD_PASSWORD = 'Fictif-only-old-7Mqa!'
NEW_PASSWORD = 'Fictif-only-new-9Dpk!'


class ResetFixtures:
    def setUp(self):
        self.user = get_user_model().objects.create_user('reset-client', email='client-reset@example.test', password=OLD_PASSWORD)
        self.url = reverse('connexion:password_reset')

    def enqueue(self, address=None, *, ip='203.0.113.10'):
        return recovery.queue_password_reset(address or self.user.email, remote_addr=ip)

    def issue(self, address=None):
        self.assertEqual(self.enqueue(address), ('accepted', 0))
        self.assertEqual(recovery.deliver_pending_password_resets(), 1)
        return re.search(r'https?://\S+/accounts/nouveau-mot-de-passe/[^\s]+', mail.outbox[-1].body).group(0)

    def clean_link(self, client, link):
        response = client.get(urlsplit(link).path)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.endswith('/nouveau-mot-de-passe/'))
        return response.url

    def new_password(self, client, url, **extra):
        return client.post(url, {'new_password1': NEW_PASSWORD, 'new_password2': NEW_PASSWORD}, **extra)


@override_settings(**CONFIG)
class PasswordResetServiceTests(ResetFixtures, TestCase):
    def test_request_only_queues_and_does_not_look_up_accounts_or_send(self):
        with patch('connexion.password_reset.get_user_model', side_effect=AssertionError('No account lookup in HTTP')):
            response = self.client.post(self.url, {'email': ' Client-reset@EXAMPLE.test '})
        self.assertRedirects(response, reverse('connexion:password_reset_done'))
        self.assertEqual(len(mail.outbox), 0)
        envelope = PasswordResetMail.objects.get()
        self.assertEqual(envelope.recipient, self.user.email)
        self.assertEqual(envelope.payload, {})
        self.assertEqual(envelope.state, 'pending')
        self.assertFalse(PasswordResetState.objects.exists())

    def test_unknown_inactive_and_unusable_addresses_have_same_queue_and_public_response(self):
        self.user.is_active = False; self.user.save(update_fields=['is_active'])
        unusable = get_user_model().objects.create_user('reset-unusable', email='unusable@example.test')
        unusable.set_unusable_password(); unusable.save(update_fields=['password'])
        bodies = []
        for address in [self.user.email, unusable.email, 'absent@example.test']:
            response = self.client.post(self.url, {'email': address}, follow=True)
            body = re.sub(rb'name="csrfmiddlewaretoken" value="[^"]+"', b'csrf-token', response.content)
            bodies.append(re.sub(rb'"checked_at": "[^"]+"', b'"checked_at":"current-time"', body))
        self.assertEqual(len(set(bodies)), 1)
        self.assertEqual(PasswordResetMail.objects.count(), 3)
        self.assertEqual(recovery.deliver_pending_password_resets(), 0)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(set(PasswordResetMail.objects.values_list('state', flat=True)), {'skipped'})

    def test_worker_uses_captured_site_origin_and_not_a_second_workers_port(self):
        self.enqueue()
        with override_settings(SITE_URL='http://localhost:8001'):
            self.assertEqual(recovery.deliver_pending_password_resets(), 1)
        self.assertIn('http://localhost:8000/accounts/nouveau-mot-de-passe/', mail.outbox[-1].body)
        self.assertNotIn('localhost:8001', mail.outbox[-1].body)

    def test_token_is_one_hour_and_state_and_session_never_store_it_raw(self):
        link = self.issue()
        token = urlsplit(link).path.rstrip('/').split('/')[-1]
        state = PasswordResetState.objects.get()
        self.assertEqual(state.token_digest, hashlib.sha256(token.encode()).hexdigest())
        self.assertTrue(recovery.token_valid(self.user, token))
        self.clean_link(self.client, link)
        self.assertNotIn(token, json.dumps(dict(self.client.session)))
        with connection.cursor() as cursor:
            cursor.execute('SELECT recipient, payload FROM connexion_passwordresetmail WHERE id = %s', [state.mail_id])
            recipient, payload = cursor.fetchone()
        self.assertTrue(recipient.startswith('enc:v1:'))
        self.assertTrue(payload.startswith('enc:v1:'))
        self.assertNotIn(self.user.email, recipient)
        self.assertNotIn(token, payload)
        self.assertEqual(PasswordResetMail.objects.get().payload, {})
        PasswordResetState.objects.update(issued_at=timezone.now() - timedelta(hours=1, seconds=1))
        self.assertFalse(recovery.token_valid(self.user, token))

    def test_new_request_invalidates_old_token_and_browser_grant_before_worker_runs(self):
        link = self.issue()
        clean = self.clean_link(self.client, link)
        envelope = PasswordResetMail.objects.get()
        PasswordResetMail.objects.filter(pk=envelope.pk).update(created_at=timezone.now() - timedelta(seconds=61))
        self.enqueue(ip='203.0.113.11')
        token = urlsplit(link).path.rstrip('/').split('/')[-1]
        self.assertFalse(recovery.token_valid(self.user, token))
        self.assertFalse(self.client.get(clean).context['validlink'])
        self.assertEqual(recovery.deliver_pending_password_resets(), 1)
        self.assertEqual(len(mail.outbox), 2)

    def test_changed_password_email_last_login_or_deactivated_account_invalidates_link(self):
        link = self.issue()
        token = urlsplit(link).path.rstrip('/').split('/')[-1]
        for field, value in [('email', 'changed@example.test'), ('password', 'invalid-hash'),
                             ('last_login', timezone.now()), ('is_active', False)]:
            with self.subTest(field=field):
                old = getattr(self.user, field)
                setattr(self.user, field, value); self.user.save(update_fields=[field])
                self.assertFalse(recovery.token_valid(self.user, token))
                setattr(self.user, field, old); self.user.save(update_fields=[field])

    def test_provider_failure_does_not_claim_sent_or_keep_a_valid_token(self):
        self.enqueue()
        with patch('django.core.mail.EmailMultiAlternatives.send', side_effect=RuntimeError('PRIVATE_TOKEN_AND_PASSWORD')):
            self.assertEqual(recovery.deliver_pending_password_resets(), 0)
        job = PasswordResetMail.objects.get()
        self.assertEqual((job.state, job.payload), ('failed', {}))
        self.assertEqual(PasswordResetState.objects.get().token_digest, '')
        self.assertEqual(len(mail.outbox), 0)

    def test_pending_job_does_not_authorize_a_reset_and_gets_no_token(self):
        self.enqueue()
        self.assertEqual(PasswordResetMail.objects.get().payload, {})
        self.assertFalse(PasswordResetState.objects.exists())

    def test_signing_key_rotation_does_not_reissue_old_queued_authorizations(self):
        self.enqueue()
        with override_settings(SECRET_KEY='rotated-test-signing-key-9kLm!' * 3):
            self.assertEqual(recovery.deliver_pending_password_resets(), 0)
        self.assertEqual(PasswordResetMail.objects.get().state, 'skipped')
        self.assertFalse(PasswordResetState.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_elapsed_hour_expiry_is_stable_across_both_french_dst_changes(self):
        for start in [datetime(2026, 3, 29, 0, 30, tzinfo=datetime_timezone.utc),
                      datetime(2026, 10, 25, 0, 30, tzinfo=datetime_timezone.utc)]:
            with self.subTest(start=start):
                with patch('connexion.password_reset.timezone.now', return_value=start), \
                     patch.object(recovery.token_generator, '_now', return_value=start.replace(tzinfo=None)):
                    link = self.issue()
                token = urlsplit(link).path.rstrip('/').split('/')[-1]
                before = start + timedelta(minutes=59, seconds=59)
                with patch('connexion.password_reset.timezone.now', return_value=before), \
                     patch.object(recovery.token_generator, '_now', return_value=before.replace(tzinfo=None)):
                    self.assertTrue(recovery.token_valid(self.user, token))
                after = start + timedelta(hours=1)
                with patch('connexion.password_reset.timezone.now', return_value=after), \
                     patch.object(recovery.token_generator, '_now', return_value=after.replace(tzinfo=None)):
                    self.assertFalse(recovery.token_valid(self.user, token))

    def test_expired_worker_claim_reuses_prepared_encrypted_payload_after_crash(self):
        self.enqueue()
        pk, claim = recovery._claim()
        payload = recovery._prepare(pk, claim)
        original_digest = PasswordResetState.objects.get().token_digest
        self.assertIn('/accounts/nouveau-mot-de-passe/', payload['url'])
        self.assertFalse(recovery.token_valid(self.user, payload['url'].rstrip('/').split('/')[-1]))
        self.assertEqual(recovery.deliver_pending_password_resets(), 0)
        PasswordResetMail.objects.filter(pk=pk).update(claimed_at=timezone.now() - timedelta(minutes=6))
        self.assertEqual(recovery.deliver_pending_password_resets(), 1)
        self.assertEqual(PasswordResetState.objects.get().token_digest, original_digest)
        self.assertEqual(PasswordResetMail.objects.get().payload, {})
        self.assertIn(payload['url'], mail.outbox[-1].body)

    def test_late_acknowledgement_cannot_finish_a_new_workers_claim(self):
        self.enqueue()
        pk, first_claim = recovery._claim()
        payload = recovery._prepare(pk, first_claim)
        PasswordResetMail.objects.filter(pk=pk).update(claimed_at=timezone.now() - timedelta(minutes=6))
        same_pk, next_claim = recovery._claim()
        self.assertEqual(same_pk, pk)
        self.assertNotEqual(first_claim, next_claim)
        recovery._finish(pk, first_claim, 'failed')
        job = PasswordResetMail.objects.get()
        self.assertEqual((job.state, job.claim_id, job.payload), ('sending', next_claim, payload))
        self.assertNotEqual(PasswordResetState.objects.get().token_digest, '')

    def test_old_jobs_do_not_starve_fresh_requests_and_cleanup_is_bounded(self):
        for number in range(3):
            PasswordResetMail.objects.create(recipient=f'old{number}@example.test', recipient_hash='old',
                origin='http://localhost:8000', created_at=timezone.now() - timedelta(days=2))
        self.enqueue()
        self.assertEqual(recovery.deliver_pending_password_resets(limit=1), 1)
        self.assertEqual(len(mail.outbox), 1)
        recovery.purge_password_reset_data(limit=2)
        self.assertEqual(PasswordResetMail.objects.filter(recipient_hash='old').count(), 1)

    def test_worker_isolates_a_corrupt_encrypted_envelope_and_continues(self):
        self.enqueue('corrupt@example.test')
        broken = PasswordResetMail.objects.get()
        with connection.cursor() as cursor:
            cursor.execute('UPDATE connexion_passwordresetmail SET recipient = %s WHERE id = %s', ['CORRUPT_PRIVATE_VALUE', broken.pk])
        self.enqueue(ip='203.0.113.11')
        self.assertEqual(recovery.deliver_pending_password_resets(), 1)
        self.assertEqual(PasswordResetMail.objects.filter(pk=broken.pk).values_list('state', flat=True).get(), 'failed')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])

    def test_cleanup_expires_pending_and_stale_sending_even_without_smtp(self):
        past = timezone.now() - timedelta(hours=2)
        for state, claimed_at in [('pending', None), ('sending', timezone.now() - timedelta(minutes=6))]:
            PasswordResetMail.objects.create(recipient='old@example.test', recipient_hash=state,
                origin='http://localhost:8000', state=state, claimed_at=claimed_at, created_at=past,
                payload={'url': 'PRIVATE_EXPIRED_LINK'})
        active = PasswordResetMail.objects.create(recipient='active@example.test', recipient_hash='active',
            origin='http://localhost:8000', state='sending', claimed_at=timezone.now(), created_at=past,
            payload={'url': 'PRIVATE_ACTIVE_LINK'})
        with override_settings(EMAIL_DELIVERY_ENABLED=False):
            recovery.purge_password_reset_data()
        self.assertEqual(PasswordResetMail.objects.filter(state='skipped').count(), 2)
        self.assertTrue(all(job.payload == {} for job in PasswordResetMail.objects.filter(state='skipped')))
        active.refresh_from_db(); self.assertEqual(active.state, 'sending')
        self.assertEqual(active.payload, {'url': 'PRIVATE_ACTIVE_LINK'})

    @override_settings(EMAIL_DELIVERY_ENABLED=False)
    def test_unconfigured_mail_is_honest_and_creates_no_false_delivery(self):
        self.assertEqual(self.enqueue(), ('unavailable', 0))
        self.assertFalse(PasswordResetMail.objects.exists())
        self.assertContains(self.client.get(self.url), 'indisponible')

    def test_unsafe_site_origins_are_rejected_even_when_browser_host_is_valid(self):
        for origin in ['https://attacker@example.test', 'https://example.test/path', '//example.test',
                       'javascript:alert(1)', 'https://example.test/?next=bad']:
            with self.subTest(origin=origin), override_settings(SITE_URL=origin):
                self.assertFalse(recovery.delivery_available())
        with override_settings(DEBUG=False, SITE_URL='http://example.test'):
            self.assertFalse(recovery.delivery_available())

    def test_email_cooldown_and_hourly_budget_apply_to_unknown_addresses_too(self):
        address = 'absent@example.test'
        now = timezone.now()
        for n in range(5):
            with patch('connexion.password_reset.timezone.now', return_value=now + timedelta(seconds=61 * n)):
                self.enqueue(address, ip=f'203.0.113.{n+1}')
        with patch('connexion.password_reset.timezone.now', return_value=now + timedelta(seconds=400)):
            self.enqueue(address, ip='203.0.113.99')
        self.assertEqual(PasswordResetMail.objects.count(), 5)
        self.assertEqual(len(mail.outbox), 0)

    def test_cooldown_does_not_invalidate_a_previously_sent_link(self):
        link = self.issue()
        self.enqueue(ip='203.0.113.88')
        self.assertEqual(PasswordResetMail.objects.count(), 1)
        self.assertTrue(recovery.token_valid(self.user, urlsplit(link).path.rstrip('/').split('/')[-1]))

    def test_ip_limit_survives_new_browsers_and_forwarded_for(self):
        for n in range(20):
            response = Client().post(self.url, {'email': f'reset{n}@example.test'}, HTTP_X_FORWARDED_FOR=f'203.0.113.{n}')
            self.assertEqual(response.status_code, 302)
        response = Client().post(self.url, {'email': self.user.email}, HTTP_X_FORWARDED_FOR='198.51.100.99')
        self.assertEqual(response.status_code, 429)
        self.assertGreater(int(response['Retry-After']), 0)
        self.assertEqual(PasswordResetMail.objects.count(), 20)
        self.assertNotIn('127.0.0.1', SignupQuota.objects.get().key)

    def test_ipv6_rotation_and_mapped_ipv4_share_quotas(self):
        now = timezone.now()
        for n in range(20):
            self.enqueue(f'v6{n}@example.test', ip=f'2001:db8:1234:5678::{n+1}')
        self.assertEqual(self.enqueue('too-many@example.test', ip='2001:db8:1234:5678::99')[0], 'limited_ip')
        self.enqueue('v4@example.test', ip='203.0.113.20')
        self.enqueue('mapped@example.test', ip='::ffff:203.0.113.20')
        self.assertEqual(SignupQuota.objects.count(), 2)


@override_settings(**CONFIG)
class PasswordResetHttpTests(ResetFixtures, TestCase):
    def test_success_revokes_other_sessions_preserves_rights_and_verification_and_notifies(self):
        self.user.is_staff = True; self.user.is_superuser = True; self.user.save()
        verify_test_user(self.user)
        other = Client(); other.force_login(self.user, backend='connexion.backends.EmailOrUsernameBackend')
        before = list(EmailVerification.objects.values())
        link = self.issue()
        clean = self.clean_link(self.client, link)
        self.assertTrue(self.client.get(clean).context['validlink'])
        response = self.new_password(self.client, clean)
        self.assertRedirects(response, reverse('connexion:password_reset_complete'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW_PASSWORD))
        self.assertTrue(self.user.is_staff and self.user.is_superuser)
        self.assertEqual(before, list(EmailVerification.objects.values()))
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(other.get(reverse('tableau:home')).status_code, 302)
        self.assertFalse(self.client.get(urlsplit(link).path).context['validlink'])
        self.assertFalse(self.new_password(self.client, clean).context['validlink'])
        self.assertEqual(recovery.deliver_pending_password_resets(), 1)
        self.assertIn('mot de passe', mail.outbox[-1].subject.lower())
        self.assertNotIn(NEW_PASSWORD, mail.outbox[-1].body)
        self.assertEqual(PasswordResetMail.objects.get(kind='changed').state, 'sent')

    def test_unverified_email_is_not_automatically_verified_or_signed_in(self):
        link = self.issue(); clean = self.clean_link(self.client, link)
        self.new_password(self.client, clean)
        self.assertFalse(EmailVerification.objects.filter(user=self.user).exists())
        response = self.client.post(reverse('connexion:login'), {'username': self.user.username, 'password': NEW_PASSWORD})
        self.assertRedirects(response, reverse('connexion:verify_email'))

    def test_get_email_scanner_does_not_consume_and_grant_cannot_be_copied_between_sessions(self):
        link = self.issue()
        scanner = Client(); scanner_clean = self.clean_link(scanner, link)
        clean = self.clean_link(self.client, link)
        self.assertTrue(scanner.get(scanner_clean).context['validlink'])
        self.user.refresh_from_db(); self.assertTrue(self.user.check_password(OLD_PASSWORD))
        other = Client(); session = other.session
        session[recovery.SESSION_KEY] = self.client.session[recovery.SESSION_KEY]; session.save()
        self.assertFalse(other.get(clean).context['validlink'])
        self.assertTrue(self.client.get(clean).context['validlink'])

    def test_post_requires_csrf_and_raw_token_url_cannot_change_password(self):
        strict = Client(enforce_csrf_checks=True)
        self.assertEqual(strict.post(self.url, {'email': self.user.email}).status_code, 403)
        link = self.issue(); path = urlsplit(link).path
        self.assertFalse(self.new_password(self.client, path).context['validlink'])
        clean = self.clean_link(strict, link)
        self.assertEqual(self.new_password(strict, clean).status_code, 403)
        strict.get(clean)
        self.assertEqual(self.new_password(strict, clean, HTTP_X_CSRFTOKEN=strict.cookies['csrftoken'].value).status_code, 302)

    def test_browser_origin_is_preserved_on_clean_forms_and_null_origin_stays_forbidden(self):
        strict = Client(enforce_csrf_checks=True)
        page = strict.get(self.url, HTTP_HOST='testserver')
        self.assertEqual(page['Referrer-Policy'], 'same-origin')
        token = strict.cookies['csrftoken'].value
        payload = {'email': self.user.email, 'csrfmiddlewaretoken': token}
        refused = strict.post(self.url, payload, HTTP_HOST='testserver', HTTP_ORIGIN='null')
        self.assertEqual(refused.status_code, 403)
        self.assertFalse(PasswordResetMail.objects.exists())
        accepted = strict.post(self.url, payload, HTTP_HOST='testserver', HTTP_ORIGIN='http://testserver')
        self.assertEqual(accepted.status_code, 302)
        self.assertEqual(accepted['Referrer-Policy'], 'same-origin')
        self.assertEqual(recovery.deliver_pending_password_resets(), 1)
        link = re.search(r'https?://\S+/accounts/nouveau-mot-de-passe/[^\s]+', mail.outbox[-1].body).group(0)
        landing = strict.get(urlsplit(link).path, HTTP_HOST='testserver')
        self.assertEqual(landing.status_code, 302)
        self.assertEqual(landing['Referrer-Policy'], 'no-referrer')
        self.assertNotIn(urlsplit(link).path.rstrip('/').split('/')[-1], landing.url)
        clean = landing.url
        confirmation = strict.get(clean, HTTP_HOST='testserver')
        self.assertEqual(confirmation['Referrer-Policy'], 'same-origin')
        self.assertTrue(confirmation.context['validlink'])
        password_payload = {'new_password1': NEW_PASSWORD, 'new_password2': NEW_PASSWORD,
                            'csrfmiddlewaretoken': strict.cookies['csrftoken'].value}
        refused = strict.post(clean, password_payload, HTTP_HOST='testserver', HTTP_ORIGIN='null')
        self.assertEqual(refused.status_code, 403)
        self.user.refresh_from_db(); self.assertTrue(self.user.check_password(OLD_PASSWORD))
        accepted = strict.post(clean, password_payload, HTTP_HOST='testserver', HTTP_ORIGIN='http://testserver')
        self.assertEqual(accepted.status_code, 302)
        self.assertEqual(accepted.url, reverse('connexion:password_reset_complete'))
        self.assertEqual(accepted['Referrer-Policy'], 'same-origin')
        invalid = strict.get('/accounts/nouveau-mot-de-passe/bad/bad/', HTTP_HOST='testserver')
        self.assertEqual(invalid['Referrer-Policy'], 'no-referrer')

    def test_weak_mismatch_and_oversized_passwords_are_refused_without_consuming_link(self):
        clean = self.clean_link(self.client, self.issue())
        for first, second in [('123', '123'), (NEW_PASSWORD, 'different'), ('a' * 257, 'a' * 257)]:
            response = self.client.post(clean, {'new_password1': first, 'new_password2': second})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context['validlink'])
            self.assertTrue(response.context['form'].errors)
            self.assertNotContains(response, 'value="' + first + '"')
        self.user.refresh_from_db(); self.assertTrue(self.user.check_password(OLD_PASSWORD))
        self.assertEqual(self.new_password(self.client, clean).status_code, 302)

    def test_reset_remains_accessible_despite_axes_lock_and_does_not_clear_it(self):
        for _ in range(5):
            self.client.post(reverse('connexion:login'), {'username': self.user.username, 'password': 'incorrect'})
        clean = self.clean_link(self.client, self.issue())
        self.assertEqual(self.new_password(self.client, clean).status_code, 302)
        response = self.client.post(reverse('connexion:login'), {'username': self.user.username, 'password': NEW_PASSWORD})
        self.assertEqual(response.status_code, 429)

    def test_private_headers_untrusted_next_and_host_do_not_change_destinations(self):
        response = self.client.post(self.url, {'email': self.user.email, 'next': 'https://attacker.test/'}, HTTP_HOST='localhost')
        self.assertEqual(response.url, reverse('connexion:password_reset_done'))
        recovery.deliver_pending_password_resets()
        link = re.search(r'https?://\S+/accounts/nouveau-mot-de-passe/[^\s]+', mail.outbox[-1].body).group(0)
        clean = self.clean_link(self.client, link)
        for path in [self.url, reverse('connexion:password_reset_done'), clean,
                     reverse('connexion:password_reset_complete'), '/accounts/nouveau-mot-de-passe/bad/bad/']:
            response = self.client.get(path)
            self.assertIn('no-store', response['Cache-Control'])
            self.assertEqual(response['Referrer-Policy'], 'no-referrer' if path == '/accounts/nouveau-mot-de-passe/bad/bad/' else 'same-origin')
            self.assertIn('noindex', response['X-Robots-Tag'])

    def test_invalid_identifiers_and_tokens_never_raise_or_change_an_account(self):
        for uid, token in [('bad', 'bad'), (urlsafe_base64_encode(force_bytes('9' * 30)), 'bad'),
                           (urlsafe_base64_encode(force_bytes(self.user.pk)), 'x' * 129)]:
            response = self.client.get(reverse('connexion:password_reset_confirm', kwargs={'uidb64': uid, 'token': token}))
            self.assertFalse(response.context['validlink'])
        self.user.refresh_from_db(); self.assertTrue(self.user.check_password(OLD_PASSWORD))

    def test_local_mailbox_capture_uses_real_private_test_backend_without_external_delivery(self):
        with TemporaryDirectory() as folder, override_settings(EMAIL_TEST_MODE=True,
                EMAIL_BACKEND='courriels.backend.LocalMailboxBackend', EMAIL_TEST_DIRECTORY=Path(folder)):
            self.client.post(self.url, {'email': self.user.email})
            self.assertEqual(list(Path(folder).glob('*.json')), [])
            self.assertEqual(recovery.deliver_pending_password_resets(), 1)
            stored = list(Path(folder).glob('*.json'))
            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0].stat().st_mode & 0o777, 0o600)
            message = json.loads(stored[0].read_text())
            self.assertEqual(message['to'], [self.user.email])
            self.assertIn('/accounts/nouveau-mot-de-passe/', message['body'])
