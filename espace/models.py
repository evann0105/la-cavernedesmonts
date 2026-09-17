from decimal import Decimal
from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.utils import timezone


class Address(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    label = models.CharField('Nom de cette adresse', max_length=80, default='Maison')
    recipient = models.CharField('Destinataire', max_length=150)
    line1 = models.CharField('Adresse', max_length=200)
    line2 = models.CharField('Complément', max_length=200, blank=True)
    postal_code = models.CharField('Code postal', max_length=20)
    city = models.CharField('Ville', max_length=100)
    country = models.CharField('Pays', max_length=2, choices=[('FR','France')], default='FR')
    class Meta:
        ordering = ['label', 'pk']


class CreditNote(models.Model):
    order = models.ForeignKey('paiement.Order', on_delete=models.PROTECT, related_name='credit_notes')
    reference = models.CharField('Référence de l’avoir émis', max_length=100, unique=True, help_text='Référence du document déjà émis. Cet enregistrement ne déclenche pas de remboursement.')
    amount = models.DecimalField('Montant (€)', max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    reason = models.TextField('Motif')
    issued_at = models.DateField('Date d’émission', default=timezone.localdate)
    class Meta:
        ordering = ['-issued_at', '-pk']
    def clean(self):
        if self.order_id and (not self.order.user_id or self.order.status != 'paid'):
            raise ValidationError('Choisissez une commande payée liée à un compte client.')


class Voucher(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    code = models.CharField('Code créé dans Stripe', max_length=100, help_text='Créer d’abord le code dans Stripe et y définir ses restrictions. Cette fiche ne crée pas de remise chez Stripe.')
    description = models.CharField('Conditions du bon', max_length=500)
    expires_at = models.DateField('Valable jusqu’au', null=True, blank=True)
    active = models.BooleanField('Actif', default=True)
    class Meta:
        ordering = ['-pk']
    @property
    def available(self):
        return self.active and (not self.expires_at or self.expires_at >= timezone.localdate())
