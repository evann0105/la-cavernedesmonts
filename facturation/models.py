"""Issued invoices are records, never editable views of the current catalogue."""
import uuid

from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db import models
from config.encrypted_fields import EncryptedJSONField, EncryptedTextField


class InvoiceQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('Une facture émise ne peut pas être modifiée. Émettez un document rectificatif avec votre comptable.')

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError('Une facture émise ne peut pas être modifiée.')

    def bulk_create(self, objs, **kwargs):
        raise ValidationError('Utilisez le service d’émission pour conserver la numérotation des factures.')

    def delete(self):
        raise ValidationError('Une facture émise doit être conservée et ne peut pas être supprimée.')


class InvoiceSequence(models.Model):
    year = models.PositiveSmallIntegerField(primary_key=True)
    next_number = models.PositiveBigIntegerField(default=1)

    class Meta:
        verbose_name = 'Séquence annuelle des factures web'
        constraints = [models.CheckConstraint(condition=models.Q(next_number__gte=1), name='invoice_sequence_positive')]


class Invoice(models.Model):
    order = models.OneToOneField('paiement.Order', on_delete=models.PROTECT, related_name='invoice')
    number = models.CharField('Numéro de facture', max_length=40, unique=True, editable=False)
    issued_at = models.DateTimeField('Date d’émission', editable=False)
    snapshot = EncryptedJSONField(editable=False)
    # No user FK: account erasure must not erase accounting records.
    issued_by_identifier = models.CharField(max_length=128, blank=True, editable=False)

    objects = InvoiceQuerySet.as_manager()

    class Meta:
        ordering = ['-issued_at', '-pk']
        verbose_name = 'Facture émise'
        verbose_name_plural = 'Factures émises'

    def save(self, *args, **kwargs):
        if not self._state.adding or (self.pk and type(self).objects.filter(pk=self.pk).exists()):
            raise ValidationError('Une facture émise ne peut pas être modifiée.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Une facture émise doit être conservée et ne peut pas être supprimée.')

    def __str__(self):
        return self.number


# Export history stores private metadata, never downloadable customer files.


class AccountingHistoryQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('L’historique comptable ne peut pas être réécrit.')

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError('L’historique comptable ne peut pas être réécrit.')

    def bulk_create(self, objs, **kwargs):
        raise ValidationError('Utilisez le service d’historique comptable.')

    def delete(self):
        raise ValidationError('L’historique comptable doit être conservé.')


class AccountingHistoryRecord(models.Model):
    objects = AccountingHistoryQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding or (self.pk and type(self).objects.filter(pk=self.pk).exists()):
            raise ValidationError('Une entrée d’historique comptable ne peut pas être modifiée.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('L’historique comptable doit être conservé.')


class AccountingExport(AccountingHistoryRecord):
    class Kind(models.TextChoices):
        BUNDLE = 'bundle', 'Dossier comptable'
        SALES = 'sales', 'Ventes et remboursements'
        INVOICES = 'invoices', 'Factures PDF'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField('Contenu', max_length=12, choices=Kind.choices, editable=False)
    filename = models.CharField('Nom du fichier', max_length=180, editable=False)
    period_kind = models.CharField('Type de période', max_length=12, editable=False)
    period_label = models.CharField('Période', max_length=120, editable=False)
    period_slug = models.CharField(max_length=50, editable=False)
    period_params = models.JSONField(editable=False)
    start_date = models.DateField('Début inclus', editable=False)
    end_date = models.DateField('Fin incluse', editable=False)
    generated_at = models.DateTimeField('Fichier préparé le', default=timezone.now, editable=False, db_index=True)
    actor_identifier = models.CharField(max_length=128, blank=True, editable=False)
    actor_label = EncryptedTextField(blank=True, editable=False)
    file_sha256 = models.CharField(max_length=64, editable=False)
    file_size = models.PositiveBigIntegerField('Taille du fichier', editable=False)
    summary = EncryptedJSONField(editable=False)

    class Meta:
        ordering = ['-generated_at', '-pk']
        verbose_name = 'Export comptable préparé'
        verbose_name_plural = 'Exports comptables préparés'
        constraints = [models.CheckConstraint(condition=models.Q(end_date__gte=models.F('start_date')),
                                               name='accounting_export_date_order')]

    @property
    def latest_event(self):
        # .all() reuses the prefetch cache on list and detail pages.
        return next(iter(self.transmissions.all()), None)

    @property
    def revision(self):
        event = self.latest_event
        return event.revision if event else 0

    @property
    def is_transmitted(self):
        event = self.latest_event
        return bool(event and event.action == 'sent')

    @property
    def transmitted_on(self):
        return self.latest_event.transmitted_on if self.is_transmitted else None

    @property
    def recipient(self):
        return self.latest_event.recipient if self.is_transmitted else ''

    def __str__(self):
        return f'{self.get_kind_display()} — {self.period_label}'


class AccountingExportTransmission(AccountingHistoryRecord):
    class Action(models.TextChoices):
        SENT = 'sent', 'Transmission notée'
        REOPEN = 'reopen', 'Remis à transmettre'

    export = models.ForeignKey(AccountingExport, on_delete=models.PROTECT, related_name='transmissions', editable=False)
    revision = models.PositiveIntegerField(editable=False)
    action = models.CharField(max_length=12, choices=Action.choices, editable=False)
    recorded_at = models.DateTimeField(default=timezone.now, editable=False)
    transmitted_on = models.DateField('Date de transmission déclarée', null=True, blank=True, editable=False)
    recipient = EncryptedTextField(blank=True, editable=False)
    note = EncryptedTextField(blank=True, editable=False)
    actor_identifier = models.CharField(max_length=128, blank=True, editable=False)
    actor_label = EncryptedTextField(blank=True, editable=False)

    class Meta:
        ordering = ['-revision', '-pk']
        verbose_name = 'Suivi de transmission comptable'
        verbose_name_plural = 'Suivis de transmission comptable'
        constraints = [
            models.UniqueConstraint(fields=['export', 'revision'], name='accounting_export_revision_unique'),
            models.CheckConstraint(condition=models.Q(revision__gte=1), name='accounting_export_revision_positive'),
            models.CheckConstraint(condition=(models.Q(action='sent', transmitted_on__isnull=False)
                                               | models.Q(action='reopen', transmitted_on__isnull=True)),
                                   name='accounting_transmission_date_state'),
        ]
