from django.db import models


class SignupQuota(models.Model):
    # Hashed IP and time window, never a raw IP address.
    key = models.CharField(max_length=64, unique=True)
    attempts = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)
