"""A small guide available exclusively on the isolated payment-test site."""
from django.conf import settings
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


@never_cache
@require_GET
def guide(request):
    if not (getattr(settings, 'PAYMENT_TEST_MODE', False)
            and getattr(settings, 'PAYMENT_TEST_ISOLATED', False)):
        raise Http404
    response = render(request, 'paiement/test_guide.html')
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response
