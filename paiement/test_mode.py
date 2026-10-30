"""Fail closed before using Stripe from the isolated local payment playground.

The dedicated settings validate its private files and database at startup. These
checks also run at each payment entry point, so merely setting a flag on the
ordinary shop cannot relax its readiness checks or admit live Stripe objects.
"""
import re
from urllib.parse import urlsplit
import stripe

from django.conf import settings
from django.db import connections


class UnsafePaymentTestMode(ValueError):
    pass


def enabled():
    return bool(getattr(settings, 'PAYMENT_TEST_MODE', False))


def assert_isolated_test_environment(error_type=UnsafePaymentTestMode):
    if not enabled():
        return False

    def refuse(reason):
        raise error_type('Le paiement de test est bloqué : ' + reason)

    if (getattr(settings, 'PAYMENT_TEST_MODE', False) is not True or
            getattr(settings, 'PAYMENT_TEST_ISOLATED', False) is not True or
            settings.SETTINGS_MODULE != 'config.payment_test_settings' or
            settings.DEBUG is not True):
        refuse('l’environnement de test séparé doit être utilisé.')
    try:
        url = urlsplit(settings.SITE_URL)
        valid_url = (url.scheme == 'http' and url.hostname in ('localhost', '127.0.0.1', '::1')
                     and not url.username and not url.password and url.path in ('', '/')
                     and not url.query and not url.fragment and (url.port is None or url.port > 0))
    except (AttributeError, TypeError, ValueError):
        valid_url = False
    if not valid_url:
        refuse('le site de test doit rester accessible uniquement en local.')

    name = getattr(settings, 'PAYMENT_TEST_DATABASE_NAME', '')
    database = connections['default'].settings_dict
    if (not isinstance(name, str) or not re.fullmatch(r'caverne_payments_test_[0-9a-f]{12}', name)
            or database.get('ENGINE') != 'django.db.backends.postgresql'
            or database.get('NAME') != name or database.get('USER') != name + '_app'):
        refuse('la base de données réservée aux essais n’est pas confirmée.')
    if (settings.EMAIL_BACKEND != 'courriels.backend.LocalMailboxBackend'
            or settings.EMAIL_TEST_MODE is not True or settings.EMAIL_DELIVERY_ENABLED is not True):
        refuse('les e-mails des essais doivent rester dans la boîte de test.')
    return True


def assert_test_runtime(error_type=UnsafePaymentTestMode):
    if not assert_isolated_test_environment(error_type):
        return False
    key = settings.STRIPE_SECRET_KEY
    if (not isinstance(key, str) or not any(
            key.startswith(prefix) and len(key) > len(prefix)
            for prefix in ('sk_test_', 'rk_test_', 'rkcs_test_'))):
        raise error_type('Le paiement de test est bloqué : une clé Stripe de test est nécessaire. Les clés réelles sont refusées.')
    return True


def assert_test_object(value, error_type=UnsafePaymentTestMode):
    """Only an explicit JSON false is acceptable; absence and 0 are rejected."""
    if assert_test_runtime(error_type):
        if not hasattr(value, 'get') or value.get('livemode') is not False:
            raise error_type('Cette réponse Stripe n’est pas confirmée comme fictive. L’opération est bloquée.')


def assert_test_payment_intent(identifier, error_type=UnsafePaymentTestMode):
    if not assert_test_runtime(error_type):
        return
    if not isinstance(identifier, str) or not identifier.startswith('pi_'):
        raise error_type('Le paiement Stripe de test à rembourser doit être vérifié.')
    payment = stripe.PaymentIntent.retrieve(identifier, api_key=settings.STRIPE_SECRET_KEY)
    assert_test_object(payment, error_type)
    if payment.get('id') != identifier:
        raise error_type('Le paiement Stripe reçu ne correspond pas au remboursement de test.')


def assert_test_refund(value, error_type=UnsafePaymentTestMode):
    if not assert_test_runtime(error_type):
        return
    # Stripe Refund has no livemode field. Retrieve its actual parent payment,
    # which does, instead of inventing a mode or trusting an absent property.
    # https://docs.stripe.com/api/refunds/object
    if (not hasattr(value, 'get') or not isinstance(value.get('id'), str)
            or not value['id'].startswith('re_')):
        raise error_type('Le remboursement Stripe de test reçu est incomplet.')
    if 'livemode' in value and value['livemode'] is not False:
        raise error_type('Ce remboursement Stripe n’est pas confirmé comme fictif.')
    assert_test_payment_intent(value.get('payment_intent'), error_type)


def assert_test_refund_list(value, error_type=UnsafePaymentTestMode):
    if assert_test_runtime(error_type):
        if not hasattr(value, 'get') or not isinstance(value.get('data'), list):
            raise error_type('La liste des remboursements de test est incomplète.')
        for refund in value['data']:
            assert_test_refund(refund, error_type)
