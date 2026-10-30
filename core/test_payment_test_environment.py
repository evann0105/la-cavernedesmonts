"""Isolation tests: no connection to the real shop and no Stripe network calls."""
import json
import importlib.util
import os
from pathlib import Path
import secrets
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, patch

from scripts import payment_test_environment as sandbox


ROOT = Path(__file__).resolve().parents[1]


class PaymentTestEnvironmentTests(TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.private = Path(self.folder.name) / 'private'
        self.private.mkdir(mode=0o700)
        self.root = Path(self.folder.name) / 'shop'
        self.root.mkdir()
        self.write_configuration(self.root)

    def write_configuration(self, root):
        name, user = sandbox.identity(root)
        self.marker = {'purpose': sandbox.PURPOSE, 'root': str(root.resolve()),
                       'name': name, 'user': user, 'instance': 'a' * 32}
        self.config = {'name': name, 'user': user, 'password': 'fictitious-password',
                       'source_name': 'real_shop', 'instance': 'a' * 32,
                       'owner': 'cluster_owner', 'socket': '/private/pg/socket',
                       'port': 55432, 'certificate': '/private/pg/server.crt'}
        for filename, content in (
            ('environment.json', json.dumps(self.marker)),
            ('connection.json', json.dumps(self.config)),
            ('django.key', secrets.token_urlsafe(64)),
            ('data.key', 'fictitious-encryption-key'),
        ):
            sandbox.private_write(self.private / filename, content)

    def test_launch_pins_own_database_keys_mail_and_cookies_settings(self):
        inherited = {'DATABASE_URL': 'postgres://real:secret@remote/real_shop',
                     'LEGACY_SQLITE_SOURCE': '/real/db.sqlite3',
                     'DATABASE_CONFIG_FILE': '/real/connection.json',
                     'DB_MAINTENANCE': '1', 'STRIPE_SECRET_KEY': 'sk_live_do_not_use',
                     'STRIPE_SHIPPING_RATE': 'shr_real',
                     'EMAIL_BACKEND': 'django.core.mail.backends.smtp.EmailBackend',
                     'SITE_URL': 'https://real-shop.example', 'DEBUG': 'False'}
        with patch.dict(os.environ, inherited):
            env = sandbox.launch_environment(self.root, directory=self.private)
        self.assertEqual(env['DATABASE_CONFIG_FILE'], str(self.private / 'connection.json'))
        self.assertEqual(env['DATA_ENCRYPTION_KEY_FILE'], str(self.private / 'data.key'))
        for key in ('DATABASE_URL', 'LEGACY_SQLITE_SOURCE', 'DB_MAINTENANCE',
                    'STRIPE_SECRET_KEY', 'STRIPE_SHIPPING_RATE'):
            self.assertEqual(env[key], '')
        self.assertEqual(env['EMAIL_BACKEND'], 'courriels.backend.LocalMailboxBackend')
        self.assertEqual(env['SITE_URL'], 'http://127.0.0.1:8010')
        self.assertEqual(env['SEO_INDEXING_ENABLED'], 'False')

    def test_runtime_rejects_production_database_even_with_valid_marker(self):
        self.config['name'] = 'real_shop'
        sandbox.private_write(self.private / 'connection.json', json.dumps(self.config))
        with self.assertRaisesRegex(RuntimeError, 'ne correspond pas'):
            sandbox.validate_environment(self.root, self.private)

    def test_marker_from_another_checkout_cannot_be_reused(self):
        with self.assertRaises(RuntimeError):
            sandbox.validate_environment(self.root.parent / 'another-shop', self.private)

    def test_secrets_must_be_private_and_not_symlinks(self):
        path = self.private / 'data.key'
        path.chmod(0o644)
        with self.assertRaisesRegex(RuntimeError, 'privé'):
            sandbox.validate_environment(self.root, self.private)
        path.chmod(0o600)
        path.unlink()
        path.symlink_to(self.private / 'django.key')
        with self.assertRaises(RuntimeError):
            sandbox.validate_environment(self.root, self.private)

    def test_live_stripe_keys_are_rejected_and_all_test_key_types_work(self):
        for prefix in ('sk_live_', 'rk_live_', 'rkcs_live_', 'pk_test_'):
            sandbox.private_write(self.private / 'stripe.json', json.dumps({'secret_key': prefix + 'example'}))
            with self.subTest(prefix=prefix), self.assertRaisesRegex(RuntimeError, 'clés Stripe de test'):
                sandbox.read_stripe_credentials(self.private)
        for prefix in ('sk_test_', 'rk_test_', 'rkcs_test_'):
            sandbox.private_write(self.private / 'stripe.json', json.dumps({'secret_key': prefix + 'example', 'webhook_secret': 'whsec_example'}))
            with self.subTest(prefix=prefix):
                self.assertEqual(sandbox.read_stripe_credentials(self.private)['secret_key'], prefix + 'example')

    def test_cli_path_is_saved_privately_and_reused_without_environment_variable(self):
        executable = Path(self.folder.name) / 'stripe'
        executable.write_text('#!/bin/sh\nexit 0\n')
        executable.chmod(0o700)
        with patch.dict(os.environ, {'CAVERNE_STRIPE_CLI': str(executable)}):
            self.assertEqual(sandbox.stripe_cli_path(self.private), executable)
        with patch.dict(os.environ, {'CAVERNE_STRIPE_CLI': ''}):
            self.assertEqual(sandbox.stripe_cli_path(self.private), executable)
        self.assertEqual((self.private / 'launch.json').stat().st_mode & 0o777, 0o600)
        for value in ('relative/stripe', '/does-not-exist/stripe'):
            with patch.dict(os.environ, {'CAVERNE_STRIPE_CLI': value}), self.assertRaises(RuntimeError):
                sandbox.stripe_cli_path(self.private)

    def test_listener_uses_private_profile_and_never_puts_keys_in_process_arguments(self):
        spec = importlib.util.spec_from_file_location('payment_launcher_under_test', ROOT / 'scripts/start_payment_test.py')
        launcher = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(ROOT / 'scripts'))
        try:
            spec.loader.exec_module(launcher)
        finally:
            sys.path.pop(0)
        (self.private / 'logs').mkdir(mode=0o700)
        sandbox.private_write(self.private / 'stripe.json', json.dumps({'secret_key': 'sk_test_fictitious'}))
        completed = subprocess.CompletedProcess([], 0, stdout='whsec_fictitious\n', stderr='')
        with patch.object(launcher.subprocess, 'run', return_value=completed) as run, \
                patch.object(launcher.subprocess, 'Popen') as start, \
                patch.object(launcher, 'wait_listener_ready') as ready:
            launcher.stripe_listener('/private/tools/stripe', self.private, 8010)
        for call in (run.call_args, start.call_args):
            arguments = call.args[0]
            self.assertEqual(arguments[1:3], ['--config', str(self.private / 'stripe-cli.toml')])
            self.assertNotIn('sk_test_fictitious', arguments)
            self.assertEqual(call.kwargs['env']['STRIPE_API_KEY'], 'sk_test_fictitious')
            self.assertEqual(arguments[arguments.index('--events') + 1],
                             'checkout.session.completed,checkout.session.async_payment_succeeded,checkout.session.expired,refund.created,refund.updated,refund.failed')
        self.assertIn('http://127.0.0.1:8010/paiement/webhook/', start.call_args.args[0])
        self.assertEqual(sandbox.read_stripe_credentials(self.private)['webhook_secret'], 'whsec_fictitious')
        self.assertEqual((self.private / 'stripe-cli.toml').stat().st_mode & 0o777, 0o600)
        ready.assert_called_once_with(start.return_value, self.private / 'logs/stripe-listener.log', 0)

        logfile = self.private / 'logs/stripe-listener.log'
        logfile.write_text('sk_test_never_print whsec_never_print\nmust specify events to forward using --events\n')
        failed = MagicMock()
        failed.poll.return_value = 1
        with self.assertRaises(RuntimeError) as error:
            launcher.wait_listener_ready(failed, logfile)
        self.assertIn('sélection explicite', str(error.exception))
        self.assertNotIn('sk_test_never_print', str(error.exception))
        self.assertNotIn('whsec_never_print', str(error.exception))

        running = MagicMock()
        running.poll.return_value = None
        logfile.write_text('Ready! Your webhook signing secret is whsec_never_print\n')
        launcher.wait_listener_ready(running, logfile)
        # A successful previous launch must not hide a new launch that hangs.
        with patch.object(launcher.time, 'monotonic', side_effect=[0, 1, 20]), \
                patch.object(launcher.time, 'sleep'), self.assertRaises(RuntimeError):
            launcher.wait_listener_ready(running, logfile, offset=logfile.stat().st_size)

    def test_settings_require_explicit_launch_and_override_inherited_live_configuration(self):
        self.write_configuration(ROOT)
        env = dict(os.environ, CAVERNE_PAYMENT_TEST_DIR=str(self.private),
                   CAVERNE_PAYMENT_TEST='1', DJANGO_SETTINGS_MODULE='config.payment_test_settings',
                   DATABASE_URL='postgres://real:secret@remote/real_shop',
                   LEGACY_SQLITE_SOURCE='/real/db.sqlite3', DB_MAINTENANCE='',
                   STRIPE_SECRET_KEY='sk_live_do_not_use', DEBUG='False',
                   EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend')
        probe = """
import json
from django.conf import settings as s
print(json.dumps({'db':s.DATABASES['default']['NAME'], 'user':s.DATABASES['default']['USER'],
 'mode':s.PAYMENT_TEST_MODE, 'isolated':s.PAYMENT_TEST_ISOLATED, 'key':s.STRIPE_SECRET_KEY,
 'debug':s.DEBUG, 'mail':s.EMAIL_BACKEND, 'mail_dir':str(s.EMAIL_TEST_DIRECTORY),
 'cookie':s.SESSION_COOKIE_NAME, 'indexing':s.SEO_INDEXING_ENABLED, 'url':s.SITE_URL,
 'media':str(s.MEDIA_ROOT), 'seller':s.SHOP_LEGAL['business_name']}))
"""
        result = subprocess.run([sys.executable, '-c', probe], cwd=ROOT, env=env,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        settings = json.loads(result.stdout)
        self.assertEqual(settings['db'], self.config['name'])
        self.assertEqual(settings['user'], self.config['user'])
        self.assertEqual(settings['key'], '')
        self.assertTrue(settings['mode'] and settings['isolated'] and settings['debug'])
        self.assertEqual(settings['mail'], 'courriels.backend.LocalMailboxBackend')
        self.assertEqual(settings['mail_dir'], str(self.private / 'emails'))
        self.assertEqual(settings['media'], str(self.private / 'media'))
        self.assertEqual(settings['cookie'], 'caverne_payment_test_session')
        self.assertFalse(settings['indexing'])
        self.assertEqual(settings['url'], 'http://127.0.0.1:8010')
        self.assertEqual(settings['seller'], 'BOUTIQUE FICTIVE — ESSAIS')
        result = subprocess.run([sys.executable, '-c', probe], cwd=ROOT,
                                env={**env, 'CAVERNE_PAYMENT_TEST': '0'},
                                capture_output=True, text=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)

    def test_unmarked_existing_database_is_never_adopted_or_modified(self):
        connection = MagicMock()
        connection.execute.return_value.fetchone.side_effect = [(None,), None]
        with patch.object(sandbox, '_owner_connection') as connect:
            connect.return_value.__enter__.return_value = connection
            with self.assertRaisesRegex(RuntimeError, 'conservée sans modification'):
                sandbox._provision_database(self.config)
        statements = [str(call.args[0]) for call in connection.execute.call_args_list]
        self.assertFalse(any('CREATE' in statement or 'GRANT' in statement or 'REVOKE' in statement for statement in statements))

    def test_unmarked_existing_role_is_never_adopted_or_modified(self):
        connection = MagicMock()
        connection.execute.return_value.fetchone.side_effect = [None, (None, False, False, False, False, False)]
        with patch.object(sandbox, '_owner_connection') as connect:
            connect.return_value.__enter__.return_value = connection
            with self.assertRaisesRegex(RuntimeError, 'compte PostgreSQL différent'):
                sandbox._provision_database(self.config)
        statements = [str(call.args[0]) for call in connection.execute.call_args_list]
        self.assertFalse(any('CREATE' in statement or 'GRANT' in statement or 'REVOKE' in statement for statement in statements))

    def test_hba_extension_is_limited_to_test_db_and_role_and_is_idempotent(self):
        hba = self.private / 'pg_hba.conf'
        original = 'local all owner peer\nhostssl real_shop real_user 127.0.0.1/32 scram-sha-256\nhost all all 0.0.0.0/0 reject\n'
        sandbox.private_write(hba, original)
        connection = MagicMock()
        def execute(statement, *args):
            result = MagicMock()
            result.fetchone.return_value = (str(hba),) if statement == 'SHOW hba_file' else (True,)
            return result
        connection.execute.side_effect = execute
        with patch.object(sandbox, '_owner_connection') as connect:
            connect.return_value.__enter__.return_value = connection
            sandbox._allow_local_connection(self.config)
            first = hba.read_text()
            sandbox._allow_local_connection(self.config)
        self.assertEqual(hba.read_text(), first)
        self.assertTrue(first.endswith(original))
        self.assertIn(f"hostssl {self.config['name']} {self.config['user']} 127.0.0.1/32 scram-sha-256\n", first)
        reloads = [call for call in connection.execute.call_args_list if call.args[0] == 'SELECT pg_reload_conf()']
        self.assertEqual(len(reloads), 1)

    def test_repeated_prepare_preserves_keys_credentials_and_data(self):
        before = {p.name: p.read_bytes() for p in self.private.iterdir()}
        source = {**self.config, 'name': 'real_shop', 'user': 'real_user', 'password': 'never-copy-this'}
        with patch.object(sandbox, '_source_configuration', return_value=source), \
                patch.object(sandbox, '_provision_database') as provision, \
                patch.object(sandbox, 'assert_database_identity'), \
                patch.object(sandbox, '_allow_local_connection'):
            first = sandbox.prepare_environment(self.root, source_root=self.root, directory=self.private)
            second = sandbox.prepare_environment(self.root, source_root=self.root, directory=self.private)
        self.assertEqual(first['name'], second['name'])
        self.assertEqual(provision.call_count, 2)
        for filename, content in before.items():
            self.assertEqual((self.private / filename).read_bytes(), content)
        self.assertNotIn('never-copy-this', (self.private / 'connection.json').read_text())
