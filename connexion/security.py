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


def resolve_login_identifier(identifier):
    """Use exactly the same account resolution for authentication and lockouts."""
    from django.contrib.auth import get_user_model
    model = get_user_model()
    identifier = identifier or ''
    user = model.objects.filter(username=identifier).first()
    if user is None:
        matches = list(model.objects.filter(email__iexact=identifier)[:2])
        user = matches[0] if len(matches) == 1 else None
    return user


def login_account_key(request, credentials=None):
    identifier = (credentials or {}).get('username')
    if identifier is None:
        identifier = request.POST.get('username', '') if request else ''
    user = resolve_login_identifier(identifier)
    identity = f'user:{user.pk}' if user else f'unknown:{identifier.casefold()}'
    return salted_hmac('login-account-limit', identity, algorithm='sha256').hexdigest()


class PasswordCheckLimited(Exception):
    """The account has exhausted its password confirmation attempts."""


def check_current_password(user, password):
    """Reserve an attempt before hashing, shared across sessions and forms.

    Atomic conditional updates prevent parallel requests from exceeding the
    five-attempt budget. Successful checks release only their own reservation.
    """
    now = timezone.now()
    key = salted_hmac('current-password-limit', str(user.pk), algorithm='sha256').hexdigest()
    quota, _ = SignupQuota.objects.get_or_create(
        key=key, defaults={'expires_at': now + timedelta(minutes=15)}
    )
    SignupQuota.objects.filter(pk=quota.pk, expires_at__lte=now).update(
        attempts=0, expires_at=now + timedelta(minutes=15)
    )
    quota.refresh_from_db(fields=['expires_at'])
    window = quota.expires_at
    if not SignupQuota.objects.filter(pk=quota.pk, expires_at=window, attempts__lt=5).update(attempts=F('attempts')+1):
        raise PasswordCheckLimited
    valid = user.check_password(password)
    if valid:
        SignupQuota.objects.filter(pk=quota.pk, expires_at=window, attempts__gt=0).update(attempts=F('attempts')-1)
    return valid
