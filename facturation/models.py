"""Issued invoices are records, never editable views of the current catalogue."""
from django.core.exceptions import ValidationError
from django.db import models
from config.encrypted_fields import EncryptedJSONField


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
