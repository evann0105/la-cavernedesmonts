"""Private SAV uploads: validate first, then lock the case and record one audit event."""
import base64
from dataclasses import dataclass
import hashlib
import json
import re
import unicodedata
import uuid

from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import Count, Sum
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from connexion.email_verification import email_access_allowed
from core.catalog_forms import ShopImageField
from retours.access import manager
from .models import SupportAttachment, SupportCase, SupportEvent
from .services import SupportConflict, SupportError

MAX_FILE_SIZE = 5 * 1024 * 1024
MAX_CASE_FILES = 20
MAX_CASE_BYTES = 25 * 1024 * 1024


@dataclass(frozen=True)
class PreparedAttachment:
    content: bytes
    filename: str
    content_type: str
    source_digest: str


def _filename(name, extension):
    # Remove paths and control characters before constructing response headers.
    name = str(name or 'document').replace('\\', '/').rsplit('/', 1)[-1]
    name = ''.join(char for char in name if not unicodedata.category(char).startswith('C'))
    stem = name.rsplit('.', 1)[0].strip(' .') if '.' in name else name.strip(' .')
    return (stem or 'document')[:190] + extension


def _pdf_content(raw):
    """Check the actual PDF container; PDFs are always downloaded, never embedded."""
    if not re.match(rb'%PDF-(?:1\.[0-7]|2\.0)(?:\r\n|\r|\n)', raw):
        return False
    # A complete PDF ends with the offset of its cross-reference table/stream.
    ending = re.search(rb'startxref\s+(\d+)\s+%%EOF\s*\Z', raw[-2048:])
    if not ending:
        return False
    offset = int(ending.group(1))
    if not 0 < offset < len(raw):
        return False
    target = raw[offset:offset + 4096]
    classic = target.startswith(b'xref') and b'trailer' in raw[offset:]
    stream = (re.match(rb'\d+\s+\d+\s+obj\b', target) is not None and
              re.search(rb'/Type\s*/XRef\b', target) is not None)
    return bool(classic or stream)


def prepare_attachment(upload):
    if not upload or not hasattr(upload, 'read'):
        raise forms.ValidationError('Choisissez une photo ou un fichier PDF.')
    if upload.size > MAX_FILE_SIZE:
        raise forms.ValidationError('Chaque pièce jointe doit peser au maximum 5 Mo.')
    try:
        upload.seek(0)
        raw = upload.read(MAX_FILE_SIZE + 1)
    finally:
        upload.seek(0)
    if not raw or len(raw) > MAX_FILE_SIZE:
        raise forms.ValidationError('Le fichier est vide ou dépasse la limite de 5 Mo.')
    source_digest = hashlib.sha256(raw).hexdigest()
    if raw.startswith(b'%PDF-'):
        if not _pdf_content(raw):
            raise forms.ValidationError('Ce PDF est incomplet ou illisible. Exportez-le à nouveau avant de l’ajouter.')
        return PreparedAttachment(raw, _filename(upload.name, '.pdf'), 'application/pdf', source_digest)
    try:
        normalized = ShopImageField().clean(ContentFile(raw, name=upload.name or 'photo'))
    except ValidationError as exc:
        raise forms.ValidationError('Choisissez une photo JPEG, PNG ou WebP valide, ou un PDF complet.') from exc
    content = normalized.read(MAX_FILE_SIZE + 1)
    if not content or len(content) > MAX_FILE_SIZE:
        raise forms.ValidationError('Cette photo reste trop volumineuse. Choisissez une image plus petite.')
    return PreparedAttachment(content, _filename(upload.name, '.webp'), 'image/webp', source_digest)


class SupportAttachmentForm(forms.Form):
    submission_key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)
    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    title = forms.CharField(label='Description du document', max_length=160, required=False,
        widget=forms.TextInput(attrs={'placeholder': 'Ex. : photo du défaut ou justificatif de livraison'}))
    file = forms.FileField(label='Photo ou document',
        widget=forms.FileInput(attrs={'accept': 'image/jpeg,image/png,image/webp,application/pdf'}),
        help_text='JPEG, PNG, WebP ou PDF, 5 Mo maximum. Accessible uniquement à l’équipe de la boutique.')

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('auto_id', 'attachment_%s')
        super().__init__(*args, **kwargs)

    def clean_file(self):
        return prepare_attachment(self.cleaned_data['file'])


class SupportAttachmentRemoveForm(forms.Form):
    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    confirm = forms.BooleanField(label='Je confirme retirer cette pièce jointe du dossier.')


def attachment_context(case, form=None):
    """No payload is selected or decrypted while listing documents."""
    attachments = case.attachments.defer('payload').order_by('-created_at', '-pk')
    totals = attachments.aggregate(count=Count('id'), size=Sum('byte_size'))
    return {
        'attachments': attachments.filter(removed_at__isnull=True),
        'retired_attachments': attachments.filter(removed_at__isnull=False),
        'attachment_form': form if form is not None else SupportAttachmentForm(initial={'revision': case.revision}),
        'attachment_limit_reached': totals['count'] >= MAX_CASE_FILES or (totals['size'] or 0) >= MAX_CASE_BYTES,
        'attachment_total_count': totals['count'],
        'attachment_total_size': totals['size'] or 0,
    }


def _authorize(actor):
    if not actor or not manager(actor) or not email_access_allowed(actor):
        raise PermissionDenied


def _fingerprint(case_id, prepared, title):
    values = {'case': str(case_id), 'source': prepared.source_digest,
              'filename': prepared.filename, 'type': prepared.content_type, 'title': title}
    return salted_hmac('sav-private-attachment-v1',
        json.dumps(values, ensure_ascii=False, sort_keys=True), algorithm='sha256').hexdigest()


def _case(case_id):
    try:
        case = SupportCase.objects.select_for_update().filter(pk=case_id).first()
    except (TypeError, ValueError, ValidationError) as exc:
        raise SupportError('Ce dossier n’existe pas.') from exc
    if case is None:
        raise SupportConflict('Ce dossier n’existe plus. Revenez à la liste des dossiers.')
    return case


def _replay(actor, case, submission_key, fingerprint):
    attachment = SupportAttachment.objects.defer('payload').filter(submission_key=submission_key).first()
    if attachment is None:
        return None
    if (attachment.case_id != case.pk or attachment.created_by_id != actor.pk or
            not constant_time_compare(attachment.submission_fingerprint, fingerprint)):
        raise SupportConflict('Cette demande d’ajout a déjà été utilisée. Rechargez le dossier avant d’ajouter un autre document.')
    return attachment


def _revision(case, revision):
    if case.revision != revision:
        raise SupportConflict('Le dossier a été modifié depuis son ouverture. Rechargez-le avant de modifier ses pièces jointes.')


def _record(case, actor, attachment, *, removed=False):
    case.updated_by = actor
    case.revision += 1
    case.save(update_fields=['updated_by', 'revision', 'updated_at'])
    label = attachment.display_title
    SupportEvent.objects.create(case=case, kind='update', actor=actor, actor_label=actor.get_username(),
        previous_state=case.state, state=case.state, note='', changes={
            'attachment': {'label': 'Pièce jointe', 'before': label if removed else 'Sans ce document',
                           'after': 'Document retiré' if removed else label}})


def add_attachment(actor, case_id, data, files):
    """Accept raw form values/files; duplicate submissions do not create a second file."""
    _authorize(actor)
    if set(data) - {'submission_key', 'revision', 'title'} or set(files) != {'file'}:
        raise SupportError('Utilisez le formulaire prévu pour ajouter un document.')
    form = SupportAttachmentForm(data, files)
    if not form.is_valid():
        raise SupportError(form.errors.as_data())
    values = form.cleaned_data
    prepared = values['file']
    fingerprint = _fingerprint(case_id, prepared, values['title'])
    # Validation/decompression happen outside the transaction, before holding locks.
    with transaction.atomic():
        case = _case(case_id)
        repeated = _replay(actor, case, values['submission_key'], fingerprint)
        if repeated is not None:
            return repeated, True
        _revision(case, values['revision'])
        totals = case.attachments.aggregate(count=Count('id'), size=Sum('byte_size'))
        if totals['count'] >= MAX_CASE_FILES or (totals['size'] or 0) + len(prepared.content) > MAX_CASE_BYTES:
            raise SupportError('Ce dossier a atteint sa limite de pièces jointes (20 fichiers ou 25 Mo, documents retirés compris).')
        attachment = SupportAttachment(case=case, submission_key=values['submission_key'],
            submission_fingerprint=fingerprint, title=values['title'], filename=prepared.filename,
            content_type=prepared.content_type, byte_size=len(prepared.content),
            payload=base64.b64encode(prepared.content).decode('ascii'), created_by=actor)
        try:
            with transaction.atomic():
                attachment.save(force_insert=True)
        except IntegrityError:
            repeated = _replay(actor, case, values['submission_key'], fingerprint)
            if repeated is None:
                raise
            return repeated, True
        _record(case, actor, attachment)
        return attachment, False


def remove_attachment(actor, case_id, attachment_id, data):
    """Soft withdrawal: keep encrypted evidence and its audit trail, revoke download."""
    _authorize(actor)
    if set(data) - {'revision', 'confirm'}:
        raise SupportError('Utilisez le formulaire prévu pour retirer un document.')
    form = SupportAttachmentRemoveForm(data)
    if not form.is_valid():
        raise SupportError(form.errors.as_data())
    with transaction.atomic():
        case = _case(case_id)
        try:
            attachment = case.attachments.defer('payload').filter(pk=attachment_id).first()
        except (TypeError, ValueError, ValidationError) as exc:
            raise SupportError('Cette pièce jointe n’existe pas dans ce dossier.') from exc
        if attachment is None:
            raise SupportError('Cette pièce jointe n’existe pas dans ce dossier.')
        if attachment.removed_at is not None:
            return attachment, True
        _revision(case, form.cleaned_data['revision'])
        attachment.removed_at = timezone.now()
        attachment.removed_by = actor
        attachment.save(update_fields=['removed_at', 'removed_by'])
        _record(case, actor, attachment, removed=True)
        return attachment, False
