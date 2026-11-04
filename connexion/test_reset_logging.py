import logging
import sys
from types import SimpleNamespace

from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse

from .reset_logging import PasswordResetLogFilter, install_password_reset_log_filter


class PasswordResetLogTests(SimpleTestCase):
    def path(self):
        return reverse('connexion:password_reset_confirm', kwargs={
            'uidb64': 'MQ', 'token': 'fictitious-private-token',
        })

    def test_access_log_masks_the_secret_but_retains_status_and_other_routes(self):
        path = self.path()
        record = logging.LogRecord('django.server', logging.INFO, '', 1,
                                   '"%s" %s %s', (f'GET {path} HTTP/1.1', 302, 0), None)
        record.status_code = 302
        self.assertTrue(PasswordResetLogFilter().filter(record))
        self.assertNotIn('fictitious-private-token', record.getMessage())
        self.assertNotIn('/MQ/', record.getMessage())
        self.assertIn('302', record.getMessage())
        self.assertEqual(record.status_code, 302)
        ordinary = logging.LogRecord('django.server', logging.INFO, '', 1,
                                     '%s', ('GET /produits/ HTTP/1.1',), None)
        PasswordResetLogFilter().filter(ordinary)
        self.assertEqual(ordinary.getMessage(), 'GET /produits/ HTTP/1.1')

    def test_error_request_is_sanitized_without_modifying_the_live_request(self):
        path = self.path()
        request = RequestFactory().get(path)
        request.resolver_match = SimpleNamespace(kwargs={'uidb64': 'MQ', 'token': 'fictitious-private-token'})
        record = logging.LogRecord('django.request', logging.ERROR, '', 1,
                                   'Bad request: %s', (path,), None)
        record.request = request
        PasswordResetLogFilter().filter(record)
        self.assertNotIn('fictitious-private-token', record.getMessage())
        self.assertNotIn('fictitious-private-token', record.request.get_full_path())
        self.assertNotIn('fictitious-private-token', str(record.request.resolver_match.kwargs))
        self.assertEqual(request.path, path)
        self.assertEqual(request.resolver_match.kwargs['token'], 'fictitious-private-token')

    def test_encoded_route_prefix_and_separator_cannot_bypass_redaction(self):
        path = self.path().replace('accounts', '%61ccounts').replace('nouveau', '%6Eouveau').replace('/MQ/', '/MQ%2F')
        record = logging.LogRecord('django.server', logging.INFO, '', 1,
                                   '"%s" %s', (f'GET {path} HTTP/1.1', 302), None)
        PasswordResetLogFilter().filter(record)
        self.assertNotIn('fictitious-private-token', record.getMessage())
        self.assertNotIn('MQ', record.getMessage())

    def test_startup_filter_is_idempotent_and_filters_security_logger_messages(self):
        install_password_reset_log_filter()
        before = len(logging.getLogger('django.server').filters)
        install_password_reset_log_filter()
        self.assertEqual(len(logging.getLogger('django.server').filters), before)
        previous = logging.root.manager.disable
        self.addCleanup(logging.disable, previous)
        logging.disable(logging.NOTSET)
        with self.assertLogs('django.security.csrf', level='WARNING') as captured:
            logging.getLogger('django.security.csrf').warning('Forbidden: %s', self.path())
        self.assertNotIn('fictitious-private-token', '\n'.join(captured.output))

    def test_formatter_cannot_reintroduce_a_secret_from_exception_text(self):
        try:
            raise ValueError('Invalid recovery URL: ' + self.path())
        except ValueError:
            record = logging.LogRecord('django.request', logging.ERROR, '', 1,
                                       'Recovery failed', (), sys.exc_info())
        PasswordResetLogFilter().filter(record)
        result = logging.Formatter().format(record)
        self.assertIn('ValueError', result)
        self.assertNotIn('fictitious-private-token', result)
        self.assertIsNone(record.exc_info)
