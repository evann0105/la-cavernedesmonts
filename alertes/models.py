"""Private seller notifications, separate from customer mail and financial state."""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator
from django.db import models

from config.encrypted_fields import EncryptedEmailField, EncryptedJSONField


class AlertSettings(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    recipient = EncryptedEmailField('Adresse qui reçoit les alertes', max_length=254, blank=True, default='')
    order_notifications_enabled = models.BooleanField('Nouvelles commandes payées', default=False)
    stock_notifications_enabled = models.BooleanField('Alertes de stock par e-mail', default=False)
    stock_threshold = models.PositiveIntegerField('Seuil de stock par défaut', default=3, validators=[MaxValueValidator(99999)])
    revision = models.PositiveIntegerField(default=1, editable=False)
    stock_generation = models.PositiveIntegerField(default=1, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='seller_alert_settings_changes')

    class Meta:
        verbose_name = 'réglages des alertes'
        verbose_name_plural = 'réglages des alertes'
        constraints = [
            models.CheckConstraint(condition=models.Q(pk=1), name='one_seller_alert_settings'),
            models.CheckConstraint(condition=models.Q(stock_threshold__lte=99999), name='seller_stock_threshold_max'),
        ]

    def clean(self):
        super().clean()
        self.recipient = (self.recipient or '').strip().lower()
        if self.pk != 1:
            raise ValidationError('Il n’existe qu’une configuration des alertes de la boutique.')
        if (self.order_notifications_enabled or self.stock_notifications_enabled) and not self.recipient:
            raise ValidationError({'recipient': 'Renseignez l’adresse de la personne qui doit recevoir les alertes.'})


class AlertNotice(models.Model):
    KINDS = [('paid_order', 'Commande payée'), ('low_stock', 'Stock à surveiller')]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(max_length=200, unique=True, editable=False)
    kind = models.CharField(max_length=20, choices=KINDS)
    order = models.ForeignKey('paiement.Order', null=True, blank=True, on_delete=models.PROTECT,
                              related_name='seller_alerts')
    variant = models.ForeignKey('stocks.Variant', null=True, blank=True, on_delete=models.SET_NULL,
                                related_name='seller_alerts')
    payload = EncryptedJSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    attempts = models.PositiveIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    delivery_mode = models.CharField(max_length=12, blank=True, default='',
                                    choices=[('', 'Non traité'), ('local', 'Boîte de test'), ('provider', 'Transmis au service e-mail')])
    failure_code = models.CharField(max_length=40, blank=True, default='')

    class Meta:
        ordering = ['-created_at', '-pk']
        verbose_name = 'notification de la boutique'
        verbose_name_plural = 'notifications de la boutique'
        indexes = [models.Index(fields=['sent_at', 'cancelled_at', 'last_attempt_at'], name='seller_alert_queue_idx')]


class AlertEvent(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name='seller_alert_events')
    created_at = models.DateTimeField(auto_now_add=True)
    kind = models.CharField(max_length=40)
    details = EncryptedJSONField(default=dict)

    class Meta:
        ordering = ['-created_at', '-pk']


class StockWatch(models.Model):
    variant = models.OneToOneField('stocks.Variant', on_delete=models.CASCADE, related_name='alert_watch')
    threshold = models.PositiveIntegerField('Seuil spécifique', null=True, blank=True,
                                             validators=[MaxValueValidator(99999)])
    enabled = models.BooleanField('Surveiller cette taille et couleur', default=True)
    was_low = models.BooleanField(default=False, editable=False)
    episode = models.PositiveIntegerField(default=0, editable=False)
    revision = models.PositiveIntegerField(default=1, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(threshold__isnull=True) | models.Q(threshold__lte=99999),
                                              name='variant_alert_threshold_max')]
