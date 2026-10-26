"""Staff-only document endpoints. No attachment is served through public media."""
import base64
import binascii

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_GET, require_POST

from retours.access import returns_required
from .attachments import (MAX_FILE_SIZE, SupportAttachmentForm, SupportAttachmentRemoveForm,
                          add_attachment, attachment_context, remove_attachment)
from .forms import SupportUpdateForm
from .models import SupportAttachment, SupportCase
from .selectors import get_customer
from .services import SupportConflict


def _private(response):
    response['Cache-Control'] = 'private, no-store, max-age=0'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    response['Referrer-Policy'] = 'same-origin'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def _form_errors(form, error):
    if hasattr(error, 'message_dict'):
        for key, errors in error.message_dict.items():
            for message in errors:
                form.add_error(key if key in form.fields else None, message)
    else:
        for message in error.messages:
            form.add_error(None, message)


def _detail_error(request, case, *, form=None, removal_errors=None, status=400):
    context = {
        'case': case, 'customer': get_customer(f'c-{case.pk}'),
        'form': SupportUpdateForm(instance=case), 'workspace': 'sav',
        'events': case.events.select_related('actor').order_by('-created_at', '-pk'),
        **attachment_context(case, form=form),
        'attachment_remove_errors': removal_errors,
    }
    return _private(render(request, 'clients/case_detail.html', context, status=status))


@returns_required
@require_POST
def attachment_add(request, pk):
    case = get_object_or_404(SupportCase.objects.select_related('user', 'order'), pk=pk)
    values = {key: request.POST.get(key, '') for key in ('submission_key', 'revision', 'title')}
    form = SupportAttachmentForm(values, request.FILES)
    if not form.is_valid():
        return _detail_error(request, case, form=form)
    try:
        attachment, repeated = add_attachment(request.user, case.pk, values, request.FILES)
    except ValidationError as error:
        _form_errors(form, error)
        case.refresh_from_db()
        return _detail_error(request, case, form=form, status=409 if isinstance(error, SupportConflict) else 400)
    if attachment.removed_at:
        messages.info(request, 'Ce document avait déjà été ajouté puis retiré. Il reste retiré du dossier.')
    else:
        messages.success(request, 'Ce document est déjà enregistré dans le dossier.' if repeated else
                         'Pièce jointe enregistrée. Elle reste privée à la boutique.')
    return _private(redirect('clients:case_detail', pk=case.pk))


@returns_required
@require_GET
def attachment_download(request, pk, attachment_id):
    # Authorize before selecting any payload; the URL must also match its case.
    attachment = get_object_or_404(SupportAttachment.objects.defer('payload'),
        pk=attachment_id, case_id=pk, removed_at__isnull=True)
    if attachment.content_type not in ('image/webp', 'application/pdf') or not 0 < attachment.byte_size <= MAX_FILE_SIZE:
        raise Http404
    try:
        content = base64.b64decode(attachment.payload, validate=True)
    except (ValueError, TypeError, binascii.Error):
        raise Http404 from None
    if len(content) != attachment.byte_size:
        raise Http404
    response = HttpResponse(content, content_type=attachment.content_type)
    response['Content-Disposition'] = content_disposition_header(True, attachment.filename)
    response['Content-Length'] = len(content)
    response['Content-Security-Policy'] = "sandbox; default-src 'none'"
    response['Cross-Origin-Resource-Policy'] = 'same-origin'
    return _private(response)


@returns_required
@require_POST
def attachment_remove(request, pk, attachment_id):
    case = get_object_or_404(SupportCase.objects.select_related('user', 'order'), pk=pk)
    get_object_or_404(case.attachments.defer('payload'), pk=attachment_id)
    values = {key: request.POST.get(key, '') for key in ('revision', 'confirm')}
    form = SupportAttachmentRemoveForm(values)
    if not form.is_valid():
        return _detail_error(request, case, removal_errors=form.errors)
    try:
        attachment, repeated = remove_attachment(request.user, case.pk, attachment_id, values)
    except ValidationError as error:
        _form_errors(form, error)
        case.refresh_from_db()
        return _detail_error(request, case, removal_errors=form.errors,
                             status=409 if isinstance(error, SupportConflict) else 400)
    messages.success(request, 'Cette pièce jointe était déjà retirée.' if repeated else
                     'Pièce jointe retirée. Son retrait figure dans l’historique du dossier.')
    return _private(redirect('clients:case_detail', pk=case.pk))
