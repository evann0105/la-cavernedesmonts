"""Customer-specific, single-use benefits, separate from refund credit notes."""
import secrets
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxLengthValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from config.encrypted_fields import EncryptedEmailField, EncryptedJSONField, EncryptedTextField, email_digest


def generate_code():
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    return 'CDM-' + '-'.join(''.join(secrets.choice(alphabet) for _ in range(5)) for _ in range(2))


class PermanentBenefitQuerySet(models.QuerySet):
    def delete(self):
        raise ValidationError('Les codes émis sont conservés. Désactivez le code au lieu de le supprimer.')


class CustomerBenefit(models.Model):
    KINDS = [('credit', 'Avoir en euros'), ('amount', 'Bon de réduction en euros'), ('percent', 'Bon de réduction en pourcentage')]
    code = models.CharField('Code', max_length=32, unique=True, default=generate_code, editable=False)
    creation_key = models.UUIDField(null=True, blank=True, unique=True, editable=False)
    recipient_email = EncryptedEmailField('Adresse e-mail du client', max_length=254)
    recipient_email_hash = models.CharField(max_length=64, db_index=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='customer_benefits')
    # Keep the original attachment even after account deletion (the FK above
    # becomes NULL). Reuse of an old e-mail must never transfer issued value.
    bound_user_id = models.PositiveBigIntegerField(null=True, blank=True, editable=False)
    label = models.CharField('Intitulé', max_length=160)
    justification = EncryptedTextField('Justification visible par le client', blank=True, default='',
                                       validators=[MaxLengthValidator(2000)])
    kind = models.CharField('Type', max_length=12, choices=KINDS, default='credit')
    value = models.DecimalField('Montant / pourcentage', max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    minimum_subtotal = models.DecimalField('Minimum d’articles (€)', max_digits=10, decimal_places=2, default=Decimal('0.00'), validators=[MinValueValidator(Decimal('0.00'))])
    starts_at = models.DateTimeField('Début', null=True, blank=True)
    ends_at = models.DateTimeField('Fin', null=True, blank=True)
    active = models.BooleanField('Actif', default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='issued_customer_benefits', editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    notified_at = models.DateTimeField(null=True, blank=True, editable=False)
    objects = PermanentBenefitQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at', '-pk']
        verbose_name = 'avoir ou bon client'
        verbose_name_plural = 'avoirs et bons clients'
        constraints = [
            models.CheckConstraint(condition=models.Q(value__gt=0), name='benefit_positive_value'),
            models.CheckConstraint(condition=models.Q(minimum_subtotal__gte=0), name='benefit_nonnegative_minimum'),
            models.CheckConstraint(condition=~models.Q(kind='percent') | models.Q(value__lte=100), name='benefit_percent_at_most_100'),
            models.CheckConstraint(condition=models.Q(starts_at__isnull=True) | models.Q(ends_at__isnull=True) | models.Q(ends_at__gt=models.F('starts_at')), name='benefit_ordered_dates'),
        ]

    def __str__(self):
        return f'{self.code} · {self.label}'

    def clean(self):
        super().clean()
        self.recipient_email = (self.recipient_email or '').strip().lower()
        errors = {}
        if not self.recipient_email:
            errors['recipient_email'] = 'Renseignez l’adresse e-mail du client.'
        if self.value is not None and (self.value <= 0 or (self.kind == 'percent' and self.value > 100)):
            errors['value'] = 'Renseignez une valeur positive, au maximum 100 pour un pourcentage.'
        if self.starts_at and timezone.is_naive(self.starts_at):
            errors['starts_at'] = 'Le début doit comporter un fuseau horaire.'
        if self.ends_at and timezone.is_naive(self.ends_at):
            errors['ends_at'] = 'La fin doit comporter un fuseau horaire.'
        if self.starts_at and self.ends_at and timezone.is_aware(self.starts_at) == timezone.is_aware(self.ends_at) and self.ends_at <= self.starts_at:
            errors['ends_at'] = 'La fin doit être postérieure au début.'
        if self.user_id:
            # A ModelForm or a caller can carry a cached User object whose
            # address has changed since it was loaded. Validate current data.
            from django.contrib.auth import get_user_model
            account_email = get_user_model().objects.filter(pk=self.user_id).values_list('email', flat=True).first() or ''
        else:
            account_email = self.recipient_email
        if self.user_id and email_digest(account_email) != email_digest(self.recipient_email):
            errors['recipient_email'] = 'L’adresse doit correspondre au compte client choisi.'
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values_list('code', flat=True).first()
            if previous and previous != self.code:
                errors['code'] = 'Un code déjà émis ne peut pas être changé.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).values_list('code', flat=True).first()
            if original and self.code != original:
                raise ValidationError('Un code déjà émis ne peut pas être changé.')
        self.recipient_email = (self.recipient_email or '').strip().lower()
        self.recipient_email_hash = email_digest(self.recipient_email)
        if kwargs.get('update_fields') is not None and 'recipient_email' in kwargs['update_fields']:
            kwargs['update_fields'] = set(kwargs['update_fields']) | {'recipient_email_hash'}
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Les codes émis sont conservés. Désactivez le code au lieu de le supprimer.')

    @property
    def effective_minimum(self):
        return max(self.minimum_subtotal, self.value) if self.kind == 'credit' else self.minimum_subtotal

    @property
    def value_label(self):
        if self.value is None:
            return ''
        amount = f'{self.value:f}'.rstrip('0').rstrip('.') if self.value % 1 else str(int(self.value))
        return amount.replace('.', ',') + (' %' if self.kind == 'percent' else ' €')

    @property
    def status(self):
        cached = getattr(self, '_prefetched_objects_cache', {}).get('redemptions')
        states = ([entry.state for entry in cached] if cached is not None else
                  list(self.redemptions.filter(state__in=['held', 'used']).values_list('state', flat=True)) if self.pk else [])
        if 'used' in states:
            return 'used'
        if 'held' in states:
            return 'reserved'
        if not self.active:
            return 'inactive'
        now = timezone.now()
        if self.ends_at and self.ends_at <= now:
            return 'expired'
        if self.starts_at and self.starts_at > now:
            return 'scheduled'
        return 'available'

    @property
    def status_label(self):
        return {'used': 'Utilisé', 'reserved': 'Paiement en cours', 'inactive': 'Désactivé', 'expired': 'Expiré', 'scheduled': 'Programmé', 'available': 'Disponible'}[self.status]

    @property
    def available(self):
        return self.status == 'available'

    @property
    def validity_label(self):
        state = self.status
        if state == 'used':
            return 'Ce code a déjà été utilisé.'
        if state == 'inactive':
            return 'Ce code est désactivé.'
        if state == 'expired':
            return 'La période de validité est terminée.'
        if state == 'reserved':
            return 'Ce code est réservé pour votre paiement en cours.'
        if not self.ends_at:
            return 'Sans date limite d’utilisation.'
        start = max(timezone.now(), self.starts_at) if self.starts_at else timezone.now()
        seconds = max(0, int((self.ends_at - start).total_seconds()))
        if seconds < 60:
            duration = 'moins d’une minute'
        elif seconds < 3600:
            minutes = seconds // 60
            duration = f'{minutes} minute' + ('s' if minutes > 1 else '')
        elif seconds < 86400:
            hours = seconds // 3600
            duration = f'{hours} heure' + ('s' if hours > 1 else '')
        else:
            days = seconds // 86400
            duration = f'{days} jour' + ('s' if days > 1 else '')
        return (f'Durée d’utilisation : {duration} à partir du début de validité.' if state == 'scheduled'
                else f'Temps restant : {duration}.')


class BenefitRedemption(models.Model):
    benefit = models.ForeignKey(CustomerBenefit, on_delete=models.PROTECT, related_name='redemptions')
    order = models.OneToOneField('paiement.Order', on_delete=models.PROTECT, related_name='benefit_redemption')
    state = models.CharField(max_length=12, choices=[('held', 'Réservé'), ('used', 'Utilisé'), ('released', 'Libéré')], default='held')
    discount_amount = models.PositiveIntegerField(help_text='Remise en centimes, figée à la réservation.')
    snapshot = models.JSONField(default=dict, editable=False)
    justification_snapshot = EncryptedTextField(blank=True, default='', editable=False,
                                               validators=[MaxLengthValidator(2000)])
    stripe_coupon_id = models.CharField(max_length=255, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [
            models.UniqueConstraint(fields=['benefit'], condition=models.Q(state__in=['held', 'used']), name='single_live_benefit_redemption'),
            models.CheckConstraint(condition=models.Q(discount_amount__gt=0), name='redemption_positive_discount'),
        ]


class BenefitEvent(models.Model):
    benefit = models.ForeignKey(CustomerBenefit, on_delete=models.PROTECT, related_name='events')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    label = models.CharField(max_length=180)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'pk']


class BenefitMail(models.Model):
    benefit = models.ForeignKey(CustomerBenefit, on_delete=models.PROTECT, related_name='notifications')
    key = models.CharField(max_length=160, unique=True)
    payload = EncryptedJSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
