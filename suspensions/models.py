from django.conf import settings
from django.db import models
from django.utils import timezone


class PauseConfiguration(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    revision = models.PositiveIntegerField(default=1, editable=False)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name='single_purchase_pause_configuration')]


class PurchasePause(models.Model):
    name = models.CharField('Nom pour vous retrouver', max_length=120)
    message = models.CharField('Message affiché aux clients', max_length=300)
    starts_at = models.DateTimeField('Début de la suspension')
    ends_at = models.DateTimeField('Reprise automatique des commandes')
    revision = models.PositiveIntegerField(default=1, editable=False)
    stopped_at = models.DateTimeField(null=True, blank=True, editable=False)
    stop_kind = models.CharField(max_length=8, blank=True, choices=[('cancel', 'Annulée'), ('resume', 'Reprise anticipée')])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    updated_by_label = models.CharField(max_length=150, blank=True)

    class Meta:
        ordering = ['starts_at', 'pk']
        constraints = [
            models.CheckConstraint(condition=models.Q(ends_at__gt=models.F('starts_at')), name='purchase_pause_positive_duration'),
            models.CheckConstraint(condition=(models.Q(stopped_at__isnull=True, stop_kind='') |
                models.Q(stopped_at__isnull=False, stop_kind__in=['cancel', 'resume'])), name='purchase_pause_consistent_stop'),
        ]
        indexes = [models.Index(fields=['stopped_at', 'starts_at', 'ends_at'], name='purchase_pause_live_dates')]

    def status_at(self, at):
        if self.stopped_at is not None:
            return 'cancelled' if self.stop_kind == 'cancel' else 'past'
        return 'upcoming' if at < self.starts_at else 'past' if at >= self.ends_at else 'active'

    @property
    def status(self):
        return self.status_at(timezone.now())

    @property
    def status_label(self):
        if self.stop_kind == 'resume':
            return 'Terminée · reprise anticipée'
        return {'upcoming': 'À venir', 'active': 'En cours', 'past': 'Terminée', 'cancelled': 'Annulée'}[self.status]

    def __str__(self):
        return self.name


class PauseEvent(models.Model):
    pause = models.ForeignKey(PurchasePause, on_delete=models.PROTECT, related_name='events')
    kind = models.CharField(max_length=8, choices=[('create', 'Programmation'), ('edit', 'Modification'),
        ('cancel', 'Annulation'), ('resume', 'Reprise anticipée')])
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    actor_label = models.CharField(max_length=150)
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']


class PurchaseAdmission(models.Model):
    """A durable admission before a provider request, including ambiguous retries."""
    order = models.OneToOneField('paiement.Order', on_delete=models.CASCADE, related_name='purchase_admission')
    admitted_at = models.DateTimeField()
    migrated = models.BooleanField(default=False, editable=False)
