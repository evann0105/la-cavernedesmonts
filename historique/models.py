from django.conf import settings
from django.db import models
from config.encrypted_fields import EncryptedJSONField


class ImmutableHistoryQuerySet(models.QuerySet):
    def update(self, **kwargs):
        # A deleted staff account may clear its FK; the frozen author label
        # and every piece of historical content remain unchanged.
        if len(kwargs) == 1 and any(key in kwargs and kwargs[key] is None for key in ('actor', 'actor_id')):
            return super().update(**kwargs)
        raise ValueError('Une version conservée ne peut pas être modifiée.')

    def delete(self):
        raise ValueError('Une version conservée ne peut pas être supprimée.')

    def bulk_update(self, *args, **kwargs):
        raise ValueError('Une version conservée ne peut pas être modifiée.')

    def bulk_create(self, objs, batch_size=None, ignore_conflicts=False, update_conflicts=False,
                    update_fields=None, unique_fields=None):
        if update_conflicts:
            raise ValueError('Une version conservée ne peut pas être modifiée.')
        return super().bulk_create(objs, batch_size=batch_size, ignore_conflicts=ignore_conflicts,
            update_conflicts=update_conflicts, update_fields=update_fields, unique_fields=unique_fields)


class HistoryVersion(models.Model):
    """Append-only content states, independent from stock and financial ledgers."""
    scope = models.CharField(max_length=16, choices=[('product', 'Fiche produit'), ('homepage', 'Accueil')])
    object_id = models.PositiveBigIntegerField()
    object_label = models.CharField(max_length=200)
    sequence = models.PositiveIntegerField()
    snapshot = EncryptedJSONField()
    assets = models.JSONField(default=list, blank=True)
    fingerprint = models.CharField(max_length=64)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    actor_label = models.CharField(max_length=150, blank=True)
    label = models.CharField(max_length=250)
    source = models.CharField(max_length=16, choices=[('baseline', 'État initial'), ('edit', 'Modification'),
        ('restore', 'Restauration'), ('checkpoint', 'État conservé avant modification')], default='edit')
    restored_from = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='restorations')
    created_at = models.DateTimeField(auto_now_add=True)
    objects = ImmutableHistoryQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [
            models.UniqueConstraint(fields=['scope', 'object_id', 'sequence'], name='history_object_sequence_unique'),
            models.CheckConstraint(condition=models.Q(sequence__gte=1), name='history_sequence_positive'),
            models.CheckConstraint(condition=models.Q(object_id__gte=1), name='history_object_positive'),
            models.CheckConstraint(condition=models.Q(scope__in=['product', 'homepage']), name='history_scope_valid'),
        ]
        indexes = [models.Index(fields=['scope', 'object_id', '-created_at'], name='history_object_dates')]

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            raise ValueError('Une version conservée ne peut pas être modifiée.')
        kwargs['force_insert'] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('Une version conservée ne peut pas être supprimée.')

    def __str__(self):
        return f'{self.object_label} · version {self.sequence}'


class RestorationReceipt(models.Model):
    """One signed confirmation can only apply once, including double clicks."""
    nonce = models.CharField(max_length=32, unique=True)
    version = models.ForeignKey(HistoryVersion, on_delete=models.PROTECT, related_name='+')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    result = models.ForeignKey(HistoryVersion, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
