from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import timedelta
from threading import Barrier, Event
from unittest import skipUnless
from unittest.mock import patch

from django.core import mail
from django.db import close_old_connections, connection
from django.test import Client, TransactionTestCase, override_settings
from django.utils import timezone

from . import password_reset as recovery
from .models import PasswordResetMail, PasswordResetState
from .test_password_reset import CONFIG, ResetFixtures


@skipUnless(connection.vendor == 'postgresql', 'Real row/advisory locking requires PostgreSQL')
@override_settings(**CONFIG)
class PasswordResetConcurrencyTests(ResetFixtures, TransactionTestCase):
    def grant(self, client, link):
        self.clean_link(client, link)
        return client.session[recovery.SESSION_KEY], client.session.session_key

    def threaded(self, function):
        close_old_connections()
        try:
            return function()
        finally:
            close_old_connections()

    def test_two_concurrent_posts_can_change_password_only_once(self):
        link = self.issue()
        first, second = self.grant(Client(), link), self.grant(Client(), link)
        barrier = Barrier(2)
        passwords = ['Fictif-race-first-7xLm!', 'Fictif-race-second-4dKr!']

        def change(number, grant):
            def action():
                barrier.wait(timeout=10)
                return recovery.reset_password(self.user.pk, *grant, {
                    'new_password1': passwords[number], 'new_password2': passwords[number]})[1]
            return self.threaded(action)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(change, number, grant) for number, grant in enumerate([first, second])]
            results = [future.result(timeout=15) for future in futures]
        self.assertEqual(sorted(results), [False, True])
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(passwords[results.index(True)]))
        self.assertEqual(PasswordResetState.objects.get().token_digest, '')
        self.assertEqual(PasswordResetMail.objects.filter(kind='changed').count(), 1)

    def test_concurrent_workers_send_one_reset_message(self):
        self.enqueue()
        barrier = Barrier(2)

        def deliver():
            def action():
                barrier.wait(timeout=10)
                return recovery.deliver_pending_password_resets(limit=1)
            return self.threaded(action)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(deliver) for _ in range(2)]
            results = [future.result(timeout=15) for future in futures]
        self.assertEqual(sum(results), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(PasswordResetMail.objects.get().state, 'sent')

    def test_new_request_waits_until_an_already_validated_reset_is_committed(self):
        link = self.issue()
        grant = self.grant(Client(), link)
        PasswordResetMail.objects.filter(kind='reset').update(created_at=timezone.now() - timedelta(seconds=61))
        checked, release, queued = Event(), Event(), Event()
        original = recovery._current_state

        def paused_validation(user, state):
            valid = original(user, state)
            checked.set()
            if not release.wait(timeout=10):
                raise RuntimeError('Timed out while testing serialization')
            return valid

        def change():
            return self.threaded(lambda: recovery.reset_password(self.user.pk, *grant, {
                'new_password1': 'Fictif-serialized-9Lm!', 'new_password2': 'Fictif-serialized-9Lm!'})[1])

        def request_new():
            queued.set()
            return self.threaded(lambda: recovery.queue_password_reset(self.user.email, remote_addr='203.0.113.99'))

        with patch('connexion.password_reset._current_state', side_effect=paused_validation):
            with ThreadPoolExecutor(max_workers=2) as pool:
                changing = pool.submit(change)
                self.assertTrue(checked.wait(timeout=10))
                requesting = pool.submit(request_new)
                self.assertTrue(queued.wait(timeout=10))
                try:
                    with self.assertRaises(TimeoutError):
                        requesting.result(timeout=0.15)
                finally:
                    release.set()
                self.assertTrue(changing.result(timeout=15))
                self.assertEqual(requesting.result(timeout=15), ('accepted', 0))
        self.assertEqual(PasswordResetMail.objects.filter(kind='reset').count(), 2)
