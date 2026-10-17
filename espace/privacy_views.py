"""Authenticated, password-confirmed access to a customer's own data export."""
import json

from django import forms
from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_POST

from connexion.security import PasswordCheckLimited, check_current_password, lockout
from .legal import profile
from .privacy import subject_export
from .views import private


class ExportForm(forms.Form):
    current_password = forms.CharField(label='Mot de passe actuel', strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'current-password', 'maxlength': '1024'}),
        max_length=1024, help_text='Cette confirmation protège le téléchargement de vos données personnelles.')


def confidential(response):
    response['Cache-Control'] = 'private, no-store, max-age=0'
    response['Referrer-Policy'] = 'no-referrer'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def _page(request, form, status=200):
    return confidential(render(request, 'espace/privacy_account.html', {
        'title': 'Mes données personnelles', 'form': form, 'privacy_contact': profile()['email'],
    }, status=status))


@private
@require_GET
def account(request):
    return _page(request, ExportForm())


@private
@require_POST
@sensitive_post_parameters('current_password')
def download(request):
    form = ExportForm(request.POST)
    if not form.is_valid():
        return _page(request, form, status=400)
    try:
        valid = check_current_password(request.user, form.cleaned_data['current_password'])
    except PasswordCheckLimited:
        return confidential(lockout(request))
    if not valid:
        form.add_error('current_password', 'Le mot de passe actuel est incorrect.')
        return _page(request, form, status=400)
    content = json.dumps(subject_export(request.user), ensure_ascii=False, indent=2, cls=DjangoJSONEncoder)
    response = HttpResponse(content, content_type='application/json; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="mes-donnees-la-caverne-des-monts.json"'
    response['Content-Security-Policy'] = "default-src 'none'; sandbox"
    return confidential(response)
