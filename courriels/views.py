import json
import uuid
from functools import wraps
from pathlib import Path
from django.conf import settings
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET
from core.catalog_access import catalog_required


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
