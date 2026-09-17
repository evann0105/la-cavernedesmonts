import time
from datetime import timedelta
from django.db.models import F
from django.shortcuts import render
from django.utils import timezone
from django.utils.crypto import salted_hmac
from .models import SignupQuota


def client_ip(request):
    # A browser-provided X-Forwarded-For must never bypass lockouts.
    # Behind a proxy, configure authenticated IP forwarding at the web server.
    return request.META.get('REMOTE_ADDR') or '0.0.0.0'


def lockout(request, *args, **kwargs):
    response = render(request, 'connexion/locked.html', {'minutes': 15}, status=429)
    response['Retry-After'] = '900'
    response['Cache-Control'] = 'no-store'
    return response


def allow_signup(request):
    window = int(time.time()) // 1800
    key = salted_hmac('signup-quota', f'{client_ip(request)}:{window}', algorithm='sha256').hexdigest()
    SignupQuota.objects.filter(expires_at__lt=timezone.now()-timedelta(days=1)).delete()
    quota, _ = SignupQuota.objects.get_or_create(key=key, defaults={'expires_at': timezone.datetime.fromtimestamp((window+1)*1800, tz=timezone.get_current_timezone())})
    return bool(SignupQuota.objects.filter(pk=quota.pk, attempts__lt=5).update(attempts=F('attempts')+1))
