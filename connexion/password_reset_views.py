from django import forms
from django.conf import settings
from django.contrib.auth import logout
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.cache import add_never_cache_headers
from django.views.decorators.http import require_http_methods, require_GET
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from core.catalog_access import can_manage_catalog

from . import password_reset as recovery

RESET_PLACEHOLDER = 'nouveau-mot-de-passe'


class ResetEmailForm(forms.Form):
    email = forms.EmailField(label='Adresse e-mail', max_length=254,
        widget=forms.EmailInput(attrs={'autocomplete': 'email', 'autofocus': True}))


def _context(request):
    available = recovery.delivery_available()
    test = bool(settings.DEBUG and settings.EMAIL_TEST_MODE)
    return {'ready': available, 'email_delivery_available': available,
            'test_mode': test, 'email_test_mode': test, 'reset_wait_minutes': 60,
            'can_view_test_mailbox': test and can_manage_catalog(request.user)}


def _private(response, *, contains_token=False):
    add_never_cache_headers(response)
    response['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
    # A clean form URL contains no recovery secret. same-origin keeps browsers'
    # Origin/Referer checks intact for CSRF-protected POSTs, including local HTTP.
    # Only the emailed URL needs strict suppression before its clean redirect.
    response['Referrer-Policy'] = 'no-referrer' if contains_token else 'same-origin'
    return response


@never_cache
@sensitive_post_parameters('email')
@require_http_methods(['GET', 'POST'])
def request_reset(request):
    form = ResetEmailForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        result, retry = recovery.queue_password_reset(form.cleaned_data['email'], remote_addr=request.META.get('REMOTE_ADDR') or '')
        if result == 'limited_ip':
            response = render(request, 'connexion/password_reset_form.html', {
                'form': form, 'reset_retry_after': retry, **_context(request)}, status=429)
            response['Retry-After'] = str(retry)
            return _private(response)
        return _private(redirect('connexion:password_reset_done'))
    return _private(render(request, 'connexion/password_reset_form.html', {'form': form, **_context(request)}))


@never_cache
@require_GET
def reset_done(request):
    return _private(render(request, 'connexion/password_reset_done.html', _context(request)))


@never_cache
@sensitive_post_parameters('new_password1', 'new_password2')
@sensitive_variables()
@require_http_methods(['GET', 'POST'])
def confirm_reset(request, uidb64, token):
    user = recovery.user_from_uid(uidb64)
    valid = False
    form = None
    if token != RESET_PLACEHOLDER:
        # Mail scanners may open this URL. Only an explicit CSRF-protected POST
        # on the clean URL can change the password; opening never consumes it.
        if request.method == 'GET' and user and recovery.make_session_grant(user, token, request.session):
            return _private(redirect('connexion:password_reset_confirm', uidb64=uidb64, token=RESET_PLACEHOLDER), contains_token=True)
    elif user:
        grant = request.session.get(recovery.SESSION_KEY, '')
        if request.method == 'POST':
            form, changed = recovery.reset_password(user.pk, grant, request.session.session_key, request.POST)
            if changed:
                logout(request)
                return _private(redirect('connexion:password_reset_complete'))
            valid = form is not None
        else:
            valid = recovery.grant_valid(user, grant, request.session.session_key)
        if valid and form is None:
            form = recovery.PasswordResetPasswordForm(user)
    if not valid:
        request.session.pop(recovery.SESSION_KEY, None)
    return _private(render(request, 'connexion/password_reset_confirm.html', {
        'form': form, 'validlink': valid, **_context(request)}), contains_token=token != RESET_PLACEHOLDER)


@never_cache
@require_GET
def reset_complete(request):
    return _private(render(request, 'connexion/password_reset_complete.html', _context(request)))
