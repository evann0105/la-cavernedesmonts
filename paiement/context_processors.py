from django.conf import settings


def test_environment(request):
    return {'payment_test_mode': bool(getattr(settings, 'PAYMENT_TEST_MODE', False)
                                     and getattr(settings, 'PAYMENT_TEST_ISOLATED', False))}
