import importlib.util
import sys
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch, MagicMock

SCRIPT_DIR = Path(__file__).resolve().parents[1] / 'scripts'
spec = importlib.util.spec_from_file_location('start_network_under_test', SCRIPT_DIR / 'start_local.py')
launcher = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(SCRIPT_DIR))
try:
    spec.loader.exec_module(launcher)
finally:
    sys.path.pop(0)


class NetworkDetectionTests(TestCase):
    def test_active_address_is_detected_again_after_network_change(self):
        with patch.object(launcher.socket, 'socket') as factory:
            probe = factory.return_value.__enter__.return_value
            probe.getsockname.side_effect = [('192.168.1.12', 1234), ('172.20.10.2', 1235)]
            self.assertEqual(launcher.detect_network_address(), '192.168.1.12')
            self.assertEqual(launcher.detect_network_address(), '172.20.10.2')
            probe.send.assert_not_called()
            probe.sendto.assert_not_called()

    def test_no_network_is_reported_before_any_setup(self):
        with patch.object(sys, 'argv', ['start', '--lan']), patch.object(launcher, 'detect_network_address', side_effect=OSError), patch.object(launcher, 'run') as run:
            self.assertEqual(launcher.main(), 1)
            run.assert_not_called()

    def test_rejects_loopback_or_all_interfaces(self):
        for address in ('127.0.0.1', '0.0.0.0', '224.0.0.1'):
            with self.subTest(address=address), patch.object(launcher.socket, 'socket') as factory:
                factory.return_value.__enter__.return_value.getsockname.return_value = (address, 1234)
                with self.assertRaises(OSError):
                    launcher.detect_network_address()

    def test_normal_start_stays_on_loopback(self):
        import errno
        with patch.object(sys, 'argv', ['start']), patch.object(launcher, 'detect_network_address') as detect, patch.object(launcher.socket, 'socket') as factory, patch.object(launcher, 'local_server_ready', return_value=False):
            probe = factory.return_value.__enter__.return_value
            probe.bind.side_effect = OSError(errno.EADDRINUSE, 'busy')
            self.assertEqual(launcher.main(), 1)
            detect.assert_not_called()
            probe.bind.assert_called_once_with(('127.0.0.1', 8000))


class LocalHttpTests(TestCase):
    def test_environment_overrides_inherited_https_configuration(self):
        with patch.dict(launcher.os.environ, {'DEBUG':'False', 'SITE_URL':'https://example.test', 'DJANGO_SETTINGS_MODULE':'config.settings'}):
            env = launcher.local_environment('127.0.0.1', 8000)
        self.assertEqual(env['SITE_URL'], 'http://127.0.0.1:8000')
        self.assertEqual(env['DEBUG'], 'True')
        self.assertEqual(env['DJANGO_SETTINGS_MODULE'], 'config.local_settings')
        self.assertEqual(env['LOCAL_DEVELOPMENT_HTTP'], '1')

    def test_readiness_check_refuses_https_redirect(self):
        import urllib.request
        import urllib.error
        request = urllib.request.Request('http://127.0.0.1:8000/')
        with self.assertRaises(urllib.error.URLError):
            launcher.HttpOnlyRedirectHandler().redirect_request(request, None, 301, 'Moved', {}, 'https://127.0.0.1:8000/')

    def test_local_settings_reject_production_and_https(self):
        import subprocess
        python = str(SCRIPT_DIR.parent / '.venv/bin/python')
        probe = "from django.conf import settings; print(settings.SECURE_SSL_REDIRECT, settings.SECURE_HSTS_SECONDS, settings.SESSION_COOKIE_SECURE, settings.SECURE_PROXY_SSL_HEADER, settings.SITE_URL)"
        env = launcher.local_environment('127.0.0.1', 8000)
        result = subprocess.run([python, '-c', probe], env=env, cwd=SCRIPT_DIR.parent, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'False 0 False None http://127.0.0.1:8000')
        for changes in ({'DEBUG':'False'}, {'SITE_URL':'https://127.0.0.1:8000'}, {'LOCAL_DEVELOPMENT_HTTP':'0'}):
            result = subprocess.run([python, '-c', probe], env={**env, **changes}, cwd=SCRIPT_DIR.parent, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)

    def test_existing_local_instance_is_reused_without_database_setup(self):
        import errno
        with patch.object(sys, 'argv', ['start']), patch.object(launcher.socket, 'socket') as factory, patch.object(launcher, 'local_server_ready', return_value=True), patch.object(launcher, 'open_browser') as browser, patch.object(launcher, 'ensure_started') as database:
            factory.return_value.__enter__.return_value.bind.side_effect = OSError(errno.EADDRINUSE, 'busy')
            self.assertEqual(launcher.main(), 0)
            browser.assert_called_once_with('http://127.0.0.1:8000/', False)
            database.assert_not_called()

    def test_readiness_rejects_another_service_and_hsts(self):
        url = 'http://127.0.0.1:8000/'
        with patch.object(launcher.urllib.request, 'build_opener') as factory:
            response = factory.return_value.open.return_value.__enter__.return_value
            response.status = 200
            response.geturl.return_value = url
            for headers, expected in (({}, False), ({'X-Caverne-Local': 'http'}, True), ({'X-Caverne-Local': 'http', 'Strict-Transport-Security': 'max-age=3600'}, False)):
                response.headers = headers
                self.assertEqual(launcher.local_server_ready(url), expected)

    def test_tls_or_non_http_service_is_reported_as_not_ready(self):
        import http.client
        with patch.object(launcher.urllib.request, 'build_opener') as factory:
            factory.return_value.open.side_effect = http.client.BadStatusLine('not HTTP')
            self.assertFalse(launcher.local_server_ready('http://127.0.0.1:8000/'))

    def test_no_browser_option_does_not_launch_browser(self):
        with patch.object(launcher, 'open_local_browser') as browser:
            launcher.open_browser('http://127.0.0.1:8000/', disabled=True)
            browser.assert_not_called()

    def test_local_responses_and_redirects_are_not_cached(self):
        import subprocess
        probe = """
from django.http import HttpResponse, HttpResponsePermanentRedirect
from config.local_http import LocalHttpMiddleware
for response in (HttpResponse('OK'), HttpResponsePermanentRedirect('/produits/')):
    result = LocalHttpMiddleware(lambda request: response)(None)
    assert result['X-Caverne-Local'] == 'http'
    assert 'no-store' in result['Cache-Control']
    assert 'Strict-Transport-Security' not in result
"""
        result = subprocess.run([sys.executable, '-c', probe], env=launcher.local_environment('127.0.0.1', 8000), cwd=SCRIPT_DIR.parent, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_production_keeps_https_and_excludes_local_middleware(self):
        import subprocess
        probe = """
from django.conf import settings
assert settings.SECURE_SSL_REDIRECT
assert settings.SESSION_COOKIE_SECURE
assert settings.CSRF_COOKIE_SECURE
assert 'config.local_http.LocalHttpMiddleware' not in settings.MIDDLEWARE
"""
        env = {**launcher.os.environ, 'DJANGO_SETTINGS_MODULE': 'config.settings', 'DEBUG': 'False'}
        result = subprocess.run([sys.executable, '-c', probe], env=env, cwd=SCRIPT_DIR.parent, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class DemoBrowserTests(TestCase):
    def setUp(self):
        self.browser = sys.modules['local_browser']

    def test_only_local_http_origins_are_allowed(self):
        for url in ('https://127.0.0.1:8000/', 'http://example.com:8000/', 'http://8.8.8.8:8000/', 'http://0.0.0.0:8000/', 'http://user:secret@127.0.0.1:8000/', 'http://127.0.0.1/'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.browser.local_origin(url)
        self.assertEqual(self.browser.local_origin('http://127.0.0.1:8000/'), ('127.0.0.1', 8000))
        self.assertEqual(self.browser.local_origin('http://172.20.10.2:8001/'), ('172.20.10.2', 8001))

    def test_profile_is_private_and_exception_is_limited_to_current_host(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as folder, patch.object(self.browser, 'find_browser', return_value='/test/chrome'), patch.object(self.browser.sys, 'platform', 'linux'), patch.object(self.browser.subprocess, 'Popen') as process:
            process.return_value.wait.return_value = 0
            self.assertTrue(self.browser.open_local_browser(folder, 'http://127.0.0.1:8000/'))
            preferences = Path(folder) / '.local-browser/http-127.0.0.1-8000/Default/Preferences'
            content = json.loads(preferences.read_text())
            self.assertEqual(content['https_upgrades']['policy']['http_allowlist'], ['127.0.0.1'])
            self.assertEqual(preferences.stat().st_mode & 0o777, 0o600)
            command = process.call_args.args[0]
            self.assertIn('--user-data-dir=' + str(preferences.parent.parent), command)
            self.assertEqual(command[-1], 'http://127.0.0.1:8000/')
            self.assertFalse(any('disable-web-security' in item or 'ignore-certificate-errors' in item for item in command))
            # A second start must not overwrite a running browser's preferences.
            preferences.write_text('{"existing":true}')
            self.browser.open_local_browser(folder, 'http://127.0.0.1:8000/')
            self.assertEqual(preferences.read_text(), '{"existing":true}')

    def test_mac_launches_separate_application_instance(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder, patch.object(self.browser, 'find_browser', return_value='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'), patch.object(self.browser.sys, 'platform', 'darwin'), patch.object(self.browser.subprocess, 'run') as run:
            run.return_value.returncode = 0
            self.browser.open_local_browser(folder, 'http://127.0.0.1:8000/')
            command = run.call_args.args[0]
            self.assertEqual(command[:3], ['open', '-n', '-a'])
            self.assertEqual(command[3], '/Applications/Google Chrome.app')
            self.assertIn('--args', command)
            self.assertTrue(any(value.startswith('--user-data-dir=') for value in command))

    def test_mac_launch_failure_is_not_reported_as_success(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder, patch.object(self.browser, 'find_browser', return_value='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'), patch.object(self.browser.sys, 'platform', 'darwin'), patch.object(self.browser.subprocess, 'run') as run:
            run.return_value.returncode = 1
            run.return_value.stderr = 'Application unavailable'
            with self.assertRaisesRegex(OSError, 'Application unavailable'):
                self.browser.open_local_browser(folder, 'http://127.0.0.1:8000/')

    def test_missing_chromium_uses_explicit_http_fallback(self):
        with patch.object(self.browser, 'find_browser', return_value=None), patch.object(self.browser.webbrowser, 'open', return_value=True) as browser:
            self.assertTrue(self.browser.open_local_browser('/unused', 'http://127.0.0.1:8000/'))
            browser.assert_called_once_with('http://127.0.0.1:8000/')
