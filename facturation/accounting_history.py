"""Private metadata and explicit, reversible declarations of transmission.

Preparing a response does not prove browser receipt or delivery to a comptable.
Only the seller's declaration records transmission, and no mail is sent here.
"""
import hashlib
import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import OuterRef, Q, Subquery
from django.utils import timezone

from connexion.email_verification import email_access_allowed
from retours.access import manager
from .accounting import PARIS
from .models import AccountingExport, AccountingExportTransmission


def _authorized(user):
    if not manager(user) or not email_access_allowed(user):
        raise PermissionDenied


def _actor(user):
    label = user.get_full_name().strip() or user.get_username()
    return {'actor_identifier': str(user.pk)[:128], 'actor_label': str(label)[:200]}


def _content_digest(content):
    digest, size = hashlib.sha256(), 0
    if isinstance(content, (bytes, bytearray, memoryview)):
        digest.update(content)
        return digest.hexdigest(), len(content)
    # A file response always starts at zero, even if hashing fails.
    content.seek(0)
    try:
        while chunk := content.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    finally:
        content.seek(0)
    return digest.hexdigest(), size


def _summary(data):
    summary = {}
    for key in ('payment_count', 'refund_count', 'invoice_count', 'payments_cents', 'refunds_cents', 'net_cents'):
        summary[key] = data['summary'][key]
    for key in ('payments', 'refunds', 'net'):
        summary[key] = format(Decimal(data['summary'][key]), '.2f')
    summary.update(missing_invoice_count=len(data['missing_invoice_orders']),
                   incomplete_order_count=len(data['incomplete_orders']),
                   warning_count=len(data['warnings']), warnings=list(data['warnings']))
    return summary


def record_export(data, kind, filename, content, user):
    """Record a fully prepared response, before it is offered for download.

    Files are hashed in chunks then rewound; bytes and customer rows are never
    persisted. Calling this again creates a distinct export, even if identical.
    """
    _authorized(user)
    if kind not in AccountingExport.Kind.values:
        raise ValidationError('Choisissez un type d’export valide.')
    if (not isinstance(filename, str) or len(filename) > 180
            or not re.fullmatch(r'[A-Za-z0-9_.-]+', filename)):
        raise ValidationError('Le nom du fichier d’export est invalide.')
    selected = data['period']
    start_date = selected['start'].astimezone(PARIS).date()
    end_date = selected['end'].astimezone(PARIS).date() - timedelta(days=1)
    params = selected.get('params') or {'annee': selected['year'], 'mois': selected['month']}
    # No searched customer name/email or unrelated GET parameter enters history.
    params = {key: value for key, value in params.items()
              if key in {'periode', 'annee', 'mois', 'trimestre', 'debut', 'fin'}}
    digest, size = _content_digest(content)
    with transaction.atomic():
        return AccountingExport.objects.create(
            kind=kind, filename=filename, period_kind=selected.get('kind', 'month'),
            period_label=selected['label'], period_slug=selected['slug'], period_params=params,
            start_date=start_date, end_date=end_date, file_sha256=digest, file_size=size,
            summary=_summary(data), generated_at=timezone.now(), **_actor(user))


def export_history_queryset(state=''):
    """Filter the latest declaration, including entries with none yet."""
    latest_action = AccountingExportTransmission.objects.filter(export_id=OuterRef('pk')).order_by('-revision').values('action')[:1]
    records = AccountingExport.objects.annotate(last_action=Subquery(latest_action)).prefetch_related('transmissions')
    if state == 'sent':
        records = records.filter(last_action='sent')
    elif state == 'pending':
        records = records.filter(Q(last_action__isnull=True) | Q(last_action='reopen'))
    return records


def transmission_service(record_id, user, action='sent', sent_on=None, recipient='', note='', expected_revision=None):
    """Add a declaration under a row lock; previous statements remain readable."""
    _authorized(user)
    if action not in AccountingExportTransmission.Action.values:
        raise ValidationError('Choisissez une action de transmission valide.')
    recipient, note = str(recipient or '').strip(), str(note or '').strip()
    if len(recipient) > 200 or len(note) > 1000:
        raise ValidationError('Limitez le destinataire à 200 caractères et la note à 1 000 caractères.')
    if isinstance(expected_revision, bool):
        raise ValidationError('Rechargez la page avant de modifier le suivi.')
    try:
        if str(expected_revision) != str(int(expected_revision)) or int(expected_revision) < 0:
            raise ValueError
        expected_revision = int(expected_revision)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValidationError('Rechargez la page avant de modifier le suivi.') from error
    with transaction.atomic():
        record = AccountingExport.objects.select_for_update().get(pk=record_id)
        latest = AccountingExportTransmission.objects.filter(export=record).order_by('-revision').first()
        revision = latest.revision if latest else 0
        is_sent = latest is not None and latest.action == 'sent'
        if revision != expected_revision:
            raise ValidationError('Le suivi a changé depuis l’ouverture de cette page. Rechargez-la avant de réessayer.', code='stale')
        if action == 'sent':
            if is_sent:
                raise ValidationError('Cette transmission est déjà notée.')
            if not isinstance(sent_on, date) or isinstance(sent_on, datetime):
                raise ValidationError('Indiquez la date à laquelle vous avez transmis le fichier.')
            first_day = timezone.localtime(record.generated_at, PARIS).date()
            today = timezone.localtime(timezone.now(), PARIS).date()
            if not first_day <= sent_on <= today:
                raise ValidationError('La date de transmission doit être comprise entre la préparation du fichier et aujourd’hui.')
        else:
            if not is_sent:
                raise ValidationError('Cet export est déjà à transmettre.')
            sent_on, recipient = None, ''
        AccountingExportTransmission.objects.create(export=record, revision=revision + 1,
            action=action, transmitted_on=sent_on, recipient=recipient, note=note, recorded_at=timezone.now(), **_actor(user))
        return export_history_queryset().get(pk=record.pk)
