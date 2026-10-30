"""Explicit, loopback-only settings for the separate Stripe payment sandbox."""
import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured
from scripts.payment_test_environment import launch_environment, validate_environment

if os.environ.get('CAVERNE_PAYMENT_TEST') != '1':
    raise ImproperlyConfigured('Utilisez ./test-payments pour lancer les paiements fictifs isolés.')

_root = Path(__file__).resolve().parent.parent
_maintenance = os.environ.get('DB_MAINTENANCE') == '1'
try:
    _isolated = validate_environment(_root)
    _environment = launch_environment(_root, int(os.environ.get('CAVERNE_PAYMENT_TEST_PORT', '8010')))
except (RuntimeError, OSError, ValueError) as error:
    raise ImproperlyConfigured(str(error)) from error

# Pin every sensitive value before settings.py reads the normal .env file.
os.environ.update(_environment)
if _maintenance:
    os.environ['DB_MAINTENANCE'] = '1'

from .settings import *  # noqa: F403, E402

PAYMENT_TEST_MODE = True
PAYMENT_TEST_ISOLATED = True
PAYMENT_TEST_DATABASE_NAME = _isolated['name']
PAYMENT_TEST_DIRECTORY = _isolated['private']
DEBUG = True
SITE_URL = _environment['SITE_URL']
ALLOWED_HOSTS = ['127.0.0.1', 'localhost', '[::1]']
SEO_INDEXING_ENABLED = False
EMAIL_BACKEND = 'courriels.backend.LocalMailboxBackend'
EMAIL_TEST_MODE = True
EMAIL_DELIVERY_ENABLED = True
EMAIL_TEST_DIRECTORY = PAYMENT_TEST_DIRECTORY / 'emails'
DEFAULT_FROM_EMAIL = 'La Caverne des Monts — test <boutique@example.test>'
SERVER_EMAIL = 'boutique@example.test'
RETURN_NOTIFICATION_EMAIL = 'vendeuse@example.test'
MEDIA_ROOT = PAYMENT_TEST_DIRECTORY / 'media'
STATIC_ROOT = PAYMENT_TEST_DIRECTORY / 'staticfiles'
SESSION_COOKIE_NAME = 'caverne_payment_test_session'
CSRF_COOKIE_NAME = 'caverne_payment_test_csrf'
LANGUAGE_COOKIE_NAME = 'caverne_payment_test_language'
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                      'LOCATION': PAYMENT_TEST_DATABASE_NAME}}
SESSION_COOKIE_DOMAIN = None
CSRF_COOKIE_DOMAIN = None
CSRF_TRUSTED_ORIGINS = [SITE_URL]
SECURE_SSL_REDIRECT = False
SECURE_SSL_HOST = None
SECURE_HSTS_SECONDS = 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
SECURE_PROXY_SSL_HEADER = None
USE_X_FORWARDED_HOST = False
USE_X_FORWARDED_PORT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
LANGUAGE_COOKIE_SECURE = False
MIDDLEWARE = ['config.local_http.LocalHttpMiddleware', *MIDDLEWARE]  # noqa: F405


class PaymentTestHttpMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response['X-Caverne-Payments'] = 'test'
        response['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
        return response


MIDDLEWARE.insert(0, 'config.payment_test_settings.PaymentTestHttpMiddleware')

# A synthetic seller is required to test invoice generation. These values
# describe only the fictitious shop, never the real seller's tax situation.
SHOP_LEGAL = {
    'business_name': 'BOUTIQUE FICTIVE — ESSAIS', 'legal_form': 'EI',
    'address': '1 rue des Essais, 75001 Paris, France',
    'siren': '000000000', 'siret': '00000000000000',
    'registration': 'DONNÉES FICTIVES — AUCUNE IMMATRICULATION',
    'email': 'boutique@example.test', 'phone': '0100000000',
    'vat_number': '',
    'vat_notice': 'DOCUMENT FICTIF — ESSAI UNIQUEMENT. TVA non applicable, article 293 B du CGI.',
}
SHOP_INVOICING = {'ready': True, 'numbering_confirmed': True,
                  'issue_policy_confirmed': True, 'vat_mode': 'franchise', 'vat_rate': ''}

# These settings do not assert that the seller's legal/payment setup is complete.
# The payment test guard may bypass readiness only for this isolated runtime.
if DATABASES['default']['NAME'] != PAYMENT_TEST_DATABASE_NAME:  # noqa: F405
    raise ImproperlyConfigured('La base de données des paiements fictifs est incorrecte.')
