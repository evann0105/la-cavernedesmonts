import json
import re
import uuid
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit
from django.conf import settings
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.urls import Resolver404, resolve
from django.views.decorators.http import require_GET
from core.catalog_access import catalog_required


def _security_path(body, view_name):
    """Keep explicitly allowed, same-origin security links outside the preview."""
    origin = urlsplit(settings.SITE_URL)
    for candidate in re.findall(r'https?://[^\s<>"\']+', body):
        try:
            url = urlsplit(candidate)
            if ((url.scheme, url.netloc) != (origin.scheme, origin.netloc)
                    or url.query or url.fragment):
                continue
            if resolve(url.path).view_name == view_name:
                return url.path
        except (ValueError, Resolver404):
            continue
    return ''


def confirmation_path(body):
    # Opening this page still requires recipient login and explicit confirmation.
    return _security_path(body, 'connexion:confirm_email')


def password_reset_path(body):
    # A GET only opens the password form; POST+CSRF consumes the private token.
    return _security_path(body, 'connexion:password_reset_confirm')


def local_only(view):
    @wraps(view)
    def guarded(request, *args, **kwargs):
        if not settings.DEBUG or not settings.EMAIL_TEST_MODE:
            raise Http404
        return view(request, *args, **kwargs)
    return guarded


@local_only
@catalog_required
@require_GET
def inbox(request):
    if not settings.DEBUG or not settings.EMAIL_TEST_MODE:
        raise Http404
    root = Path(settings.EMAIL_TEST_DIRECTORY)
    letters = []
    for path in sorted(root.glob('*.json'), key=lambda p:p.stat().st_mtime, reverse=True)[:50]:
        try:
            letter = json.loads(path.read_text(encoding='utf-8'))
            letter['id'] = uuid.UUID(path.stem)
            letter['confirmation_path'] = confirmation_path(letter.get('body', ''))
            letter['password_reset_path'] = password_reset_path(letter.get('body', ''))
            letters.append(letter)
        except (OSError, ValueError):
            continue
    response = render(request, 'courriels/inbox.html', {'letters':letters})
    response['Cache-Control'] = 'no-store'
    response['Referrer-Policy'] = 'no-referrer'
    return response


@local_only
@catalog_required
@require_GET
def preview(request, message_id):
    if not settings.DEBUG or not settings.EMAIL_TEST_MODE:
        raise Http404
    path = Path(settings.EMAIL_TEST_DIRECTORY) / (message_id.hex + '.json')
    try:
        letter = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise Http404
    response = HttpResponse(letter.get('html', ''), content_type='text/html; charset=utf-8')
    response['Content-Security-Policy'] = "default-src 'none'; sandbox; base-uri 'none'; form-action 'none'"
    response['Cache-Control'] = 'no-store'
    response['Referrer-Policy'] = 'no-referrer'
    return response
