"""Private support records: contacts and free text use the shop's encrypted fields."""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxLengthValidator
from django.db import models
from django.utils import timezone

from config.encrypted_fields import (EncryptedCharField, EncryptedEmailField,
                                     EncryptedJSONField, EncryptedTextField, email_digest)


class SupportCase(models.Model):
    STATES = [('open', 'À traiter'), ('in_progress', 'En cours'),
              ('waiting_customer', 'En attente du client'), ('resolved', 'Résolu')]
    CHANNELS = [('phone', 'Téléphone'), ('email', 'E-mail'), ('store', 'En boutique')]
    CATEGORIES = [('delivery', 'Livraison'), ('product', 'Produit'), ('return', 'Retour'),
                  ('payment', 'Paiement'), ('other', 'Autre demande')]
    PRIORITIES = [('normal', 'Normale'), ('urgent', 'Urgente')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    creation_key = models.UUIDField(unique=True, editable=False)
    creation_fingerprint = models.CharField(max_length=64, editable=False)
    contact_reference = models.UUIDField(null=True, blank=True, db_index=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='support_cases')
    order = models.ForeignKey('paiement.Order', null=True, blank=True,
        on_delete=models.PROTECT, related_name='support_cases')
    customer_name = EncryptedCharField('Nom du client', max_length=150,
        validators=[MaxLengthValidator(150)])
    customer_email = EncryptedEmailField('Adresse e-mail', max_length=254, blank=True,
        validators=[MaxLengthValidator(254)])
    customer_email_hash = models.CharField(max_length=64, db_index=True, editable=False, default='', blank=True)
    phone = EncryptedCharField('Téléphone', max_length=40, blank=True,
        validators=[MaxLengthValidator(40)])
    subject = EncryptedCharField('Objet de la demande', max_length=160,
        validators=[MaxLengthValidator(160)])
    description = EncryptedTextField('Demande du client', validators=[MaxLengthValidator(5000)])
    channel = models.CharField('Demande reçue par', max_length=12, choices=CHANNELS, default='phone')
    category = models.CharField('Type de demande', max_length=12, choices=CATEGORIES, default='other')
    state = models.CharField('État du dossier', max_length=20, choices=STATES, default='open', db_index=True)
    priority = models.CharField('Priorité', max_length=10, choices=PRIORITIES, default='normal')
    due_date = models.DateField('À reprendre le', null=True, blank=True, db_index=True)
    revision = models.PositiveIntegerField(default=1, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='support_cases_created', editable=False)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='support_cases_updated', editable=False)

    class Meta:
        ordering = ['-updated_at', '-pk']
        verbose_name = 'Dossier SAV'
        verbose_name_plural = 'Dossiers SAV'
        constraints = [
            models.CheckConstraint(condition=models.Q(revision__gte=1), name='support_case_positive_revision'),
            models.CheckConstraint(condition=models.Q(state__in=['open', 'in_progress', 'waiting_customer', 'resolved']), name='support_case_valid_state'),
            models.CheckConstraint(condition=models.Q(priority__in=['normal', 'urgent']), name='support_case_valid_priority'),
            models.CheckConstraint(condition=(models.Q(state='resolved', resolved_at__isnull=False) |
                (~models.Q(state='resolved') & models.Q(resolved_at__isnull=True))), name='support_case_resolution_date'),
        ]

    @property
    def reference(self):
        return 'SAV-' + str(self.pk)[:8].upper()

    @property
    def is_overdue(self):
        return bool(self.state != 'resolved' and self.due_date and self.due_date < timezone.localdate())

    def clean(self):
        super().clean()
        self.customer_email = (self.customer_email or '').strip().lower()
        if not (self.customer_email or (self.phone or '').strip()):
            raise ValidationError('Renseignez un e-mail ou un téléphone pour retrouver le client.')
        if not (self.customer_name or '').strip():
            raise ValidationError({'customer_name': 'Renseignez le nom du client.'})
        if not (self.subject or '').strip() or not (self.description or '').strip():
            raise ValidationError('Renseignez l’objet et la demande du client.')

    def save(self, *args, **kwargs):
        self.customer_email = (self.customer_email or '').strip().lower()
        self.customer_email_hash = email_digest(self.customer_email)
        if kwargs.get('update_fields') is not None and 'customer_email' in kwargs['update_fields']:
            kwargs['update_fields'] = set(kwargs['update_fields']) | {'customer_email_hash'}
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.reference


class SupportEvent(models.Model):
    case = models.ForeignKey(SupportCase, on_delete=models.CASCADE, related_name='events')
    kind = models.CharField(max_length=10, choices=[('created', 'Création'), ('update', 'Mise à jour'), ('note', 'Note')])
    note = EncryptedTextField('Note interne', blank=True, validators=[MaxLengthValidator(5000)])
    changes = EncryptedJSONField(default=dict, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='support_events')
    actor_label = models.CharField(max_length=150)
    previous_state = models.CharField(max_length=20, blank=True)
    state = models.CharField(max_length=20, choices=SupportCase.STATES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        verbose_name = 'Événement SAV'
        verbose_name_plural = 'Événements SAV'

    @property
    def summary(self):
        if self.kind == 'created':
            return 'Dossier ouvert'
        if self.kind == 'note':
            return 'Note ajoutée'
        return ' · '.join(f"{item['label']} : {item['before']} → {item['after']}"
                          for item in self.changes.values()) or 'Dossier mis à jour'
