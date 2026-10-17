"""HTTP-only development settings, selected exclusively by ./start."""
import os
from django.core.exceptions import ImproperlyConfigured
from .settings import *  # noqa: F403

if not DEBUG or os.environ.get('LOCAL_DEVELOPMENT_HTTP') != '1':
    raise ImproperlyConfigured('Ces réglages HTTP sont réservés au lancement local avec ./start.')
if not SITE_URL.startswith('http://'):
    raise ImproperlyConfigured('Le démarrage local exige une SITE_URL en http://.')

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
MIDDLEWARE = ['config.local_http.LocalHttpMiddleware', *MIDDLEWARE]
