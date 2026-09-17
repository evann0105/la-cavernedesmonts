import uuid
from django.conf import settings
from django.db import models


class Order(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='orders')
    session_key = models.CharField(max_length=80)
    items = models.JSONField()
    subtotal = models.PositiveIntegerField(help_text='Montant des articles en centimes')
    stripe_session_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    status = models.CharField(max_length=20, default='pending', choices=[('pending', 'En attente'), ('paid', 'Payée')])
    customer_email = models.EmailField(blank=True)
    shipping_details = models.JSONField(default=dict, blank=True)
    total_paid = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    @property
    def display_total(self):
        from decimal import Decimal
        return Decimal(self.total_paid if self.total_paid is not None else self.subtotal) / 100
