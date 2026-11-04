from django.db import models
from django.conf import settings
from config.encrypted_fields import EncryptedEmailField, EncryptedJSONField
from django.utils import timezone


class SignupQuota(models.Model):
    # Hashed IP and time window, never a raw IP address.
    key = models.CharField(max_length=64, unique=True)
    attempts = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)


class EmailVerification(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='email_verification')
    verified_email = EncryptedEmailField(blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    token_digest = models.CharField(max_length=64, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)


class PasswordResetMail(models.Model):
    """Private reset queue; requests never disclose whether an account exists."""
    kind = models.CharField(max_length=12, default='reset', choices=[('reset', 'Récupération'), ('changed', 'Mot de passe modifié')])
    recipient = EncryptedEmailField()
    recipient_hash = models.CharField(max_length=64, db_index=True)
    origin = models.URLField(max_length=254)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    payload = EncryptedJSONField(default=dict, blank=True)
    state = models.CharField(max_length=12, default='pending', db_index=True, choices=[
        ('pending', 'En attente'), ('sending', 'Envoi en cours'), ('sent', 'Accepté'),
        ('skipped', 'Sans envoi'), ('failed', 'Échec')])
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    claim_id = models.UUIDField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    sent_at = models.DateTimeField(null=True, blank=True)


class PasswordResetState(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='password_reset_state')
    mail = models.ForeignKey(PasswordResetMail, null=True, blank=True, on_delete=models.SET_NULL)
    token_digest = models.CharField(max_length=64, blank=True)
    identity_digest = models.CharField(max_length=64, blank=True)
    issued_at = models.DateTimeField(null=True, blank=True)
