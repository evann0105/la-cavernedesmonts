from django.db import models


class SignupQuota(models.Model):
    # Hashed IP and time window, never a raw IP address.
    key = models.CharField(max_length=64, unique=True)
    attempts = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)


class EmailVerification(models.Model):
    user = models.OneToOneField('auth.User', on_delete=models.CASCADE, related_name='email_verification')
    verified_email = models.EmailField(blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    token_digest = models.CharField(max_length=64, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
