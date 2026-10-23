"""Manual parcel handling is separate from payment, stock and return parcels."""
from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.core.validators import MaxLengthValidator
from config.encrypted_fields import EncryptedTextField


class Fulfillment(models.Model):
    STATES = [('preparing', 'En préparation'), ('ready', 'Prête à expédier'), ('shipped', 'Expédiée')]
    CARRIERS = [('colissimo', 'Colissimo / La Poste'), ('other', 'Autre transporteur')]
    order = models.OneToOneField('paiement.Order', related_name='fulfillment', on_delete=models.PROTECT)
    state = models.CharField('État de préparation', max_length=12, choices=STATES, default='preparing')
    revision = models.PositiveIntegerField(default=1, editable=False)
    ready_at = models.DateTimeField(null=True, blank=True, editable=False)
    shipped_at = models.DateTimeField(null=True, blank=True, editable=False)
    carrier = models.CharField('Transporteur', max_length=12, choices=CARRIERS, default='colissimo')
    carrier_name = models.CharField('Nom de l’autre transporteur', max_length=150, blank=True)
    tracking_number = EncryptedTextField('Numéro de suivi', blank=True, default='',
                                         validators=[MaxLengthValidator(120)])
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, editable=False)

    class Meta:
        verbose_name = 'Préparation de commande'
        verbose_name_plural = 'Préparations de commandes'
        constraints = [
            models.CheckConstraint(condition=Q(revision__gte=1), name='fulfillment_positive_revision'),
            models.CheckConstraint(condition=Q(carrier__in=['colissimo', 'other']), name='fulfillment_known_carrier'),
            models.CheckConstraint(condition=(
                Q(state='preparing', ready_at__isnull=True, shipped_at__isnull=True)
                | Q(state='ready', ready_at__isnull=False, shipped_at__isnull=True)
                | Q(state='shipped', ready_at__isnull=False, shipped_at__isnull=False,
                    shipped_at__gte=F('ready_at'))
            ), name='fulfillment_consistent_state_dates'),
        ]

    def __str__(self):
        return f'Commande {self.order_id} · {self.get_state_display()}'

    @property
    def carrier_label(self):
        return 'Colissimo / La Poste' if self.carrier == 'colissimo' else self.carrier_name

    @property
    def tracking_url(self):
        from .tracking import tracking_url
        return tracking_url(self)


class FulfillmentEvent(models.Model):
    fulfillment = models.ForeignKey(Fulfillment, related_name='events', on_delete=models.PROTECT)
    action = models.CharField(max_length=32)
    from_state = models.CharField(max_length=12)
    to_state = models.CharField(max_length=12)
    label = models.CharField(max_length=200)
    # 2,000 customer-entered characters plus the old/new tracking audit text.
    note = EncryptedTextField('Note interne', blank=True, default='', validators=[MaxLengthValidator(3000)])
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'pk']
        verbose_name = 'Événement de préparation'
        verbose_name_plural = 'Historique de préparation'
