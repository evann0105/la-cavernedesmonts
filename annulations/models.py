from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from config.encrypted_fields import EncryptedTextField
from .reasons import INTERNAL_NOTE_MAX_LENGTH, REASON_CHOICES


class Cancellation(models.Model):
    """An operational cancellation with a shared, auditable refund ledger."""
    order = models.OneToOneField('paiement.Order', on_delete=models.PROTECT, related_name='cancellation')
    refund = models.OneToOneField('retours.ReturnRequest', on_delete=models.PROTECT, related_name='cancellation')
    reason = EncryptedTextField('Motif communiqué au client')
    reason_code = models.CharField('Motif proposé', max_length=20, choices=REASON_CHOICES,
        blank=True, default='')
    internal_note = EncryptedTextField('Note interne à la boutique',
        max_length=INTERNAL_NOTE_MAX_LENGTH, blank=True, default='')
    internal_note_revision = models.PositiveIntegerField(default=0, editable=False)
    internal_note_updated_at = models.DateTimeField(null=True, blank=True, editable=False)
    internal_note_updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
        blank=True, on_delete=models.SET_NULL, related_name='updated_cancellation_notes', editable=False)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    stock_completed_at = models.DateTimeField(null=True, blank=True, editable=False)
    stock_error = models.TextField(blank=True, default='', editable=False)
    refund_error = models.TextField(blank=True, default='', editable=False)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Annulation avant expédition'
        verbose_name_plural = 'Annulations avant expédition'


class CancellationStockMovementQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('Un mouvement de remise en stock ne peut pas être modifié.')

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError('Un mouvement de remise en stock ne peut pas être modifié.')

    def bulk_create(self, objs, **kwargs):
        raise ValidationError('Utilisez le suivi de l’annulation pour enregistrer sa remise en stock.')

    def delete(self):
        raise ValidationError('L’historique de remise en stock doit être conservé.')


class CancellationStockMovement(models.Model):
    """Exact stock values frozen when an unshipped variant is restored."""
    cancellation = models.ForeignKey(Cancellation, on_delete=models.PROTECT,
        related_name='stock_movements', editable=False)
    variant = models.ForeignKey('stocks.Variant', on_delete=models.PROTECT,
        related_name='cancellation_stock_movements', editable=False)
    product_name = models.CharField(max_length=255, editable=False)
    size = models.CharField(max_length=50, editable=False)
    color = models.CharField(max_length=80, editable=False)
    quantity_before = models.PositiveIntegerField(editable=False)
    quantity_added = models.PositiveIntegerField(editable=False)
    quantity_after = models.PositiveIntegerField(editable=False)
    reserved = models.PositiveIntegerField(editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    source = models.CharField(max_length=9, choices=[('automatic', 'Automatique'),
        ('manual', 'Reprise par la boutique')], default='automatic', editable=False)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, editable=False)

    objects = CancellationStockMovementQuerySet.as_manager()

    class Meta:
        ordering = ['created_at', 'pk']
        verbose_name = 'Remise en stock après annulation'
        verbose_name_plural = 'Historique du stock après annulation'
        constraints = [
            models.UniqueConstraint(fields=['cancellation', 'variant'], name='one_cancel_variant_stock_move'),
            models.CheckConstraint(condition=models.Q(quantity_added__gt=0), name='cancel_stock_added_positive'),
            models.CheckConstraint(condition=models.Q(
                quantity_after=models.F('quantity_before') + models.F('quantity_added')),
                name='cancel_stock_balanced'),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding or (self.pk and type(self).objects.filter(pk=self.pk).exists()):
            raise ValidationError('Un mouvement de remise en stock ne peut pas être modifié.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('L’historique de remise en stock doit être conservé.')
