import uuid
from config.encrypted_fields import EncryptedEmailField, EncryptedJSONField
from django.conf import settings
from django.db import models


class Order(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='orders')
    session_key = models.CharField(max_length=80)
    items = models.JSONField()
    subtotal = models.PositiveIntegerField(help_text='Montant des articles en centimes')
    stripe_session_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    status = models.CharField(max_length=20, default='pending', choices=[('pending', 'En attente'), ('paid', 'Payée'), ('expired', 'Expirée'), ('demo', 'Simulation')])
    customer_email = EncryptedEmailField(blank=True)
    shipping_details = EncryptedJSONField(default=dict, blank=True)
    billing_details = EncryptedJSONField(default=dict, blank=True)
    invoice_profile = models.JSONField(default=dict, blank=True, editable=False)
    total_paid = models.PositiveIntegerField(null=True, blank=True)
    paid_line_totals = models.JSONField(null=True, blank=True, default=None, editable=False, help_text='Montants réellement payés par ligne, après remise, en centimes.')
    checkout_fingerprint = models.CharField(max_length=64, null=True, blank=True, unique=True, editable=False)
    checkout_payload = models.JSONField(default=dict, blank=True, editable=False)
    shipping_snapshot = models.JSONField(default=dict, blank=True, editable=False)
    terms_snapshot = models.JSONField(default=dict, blank=True, editable=False)
    terms_accepted_at = models.DateTimeField(null=True, blank=True, editable=False)
    reservation_expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    return_shipping_amount = models.PositiveIntegerField(null=True, blank=True, help_text="Frais de livraison standard effectivement payés, en centimes")

    @property
    def is_demo(self):
        return self.status == 'demo'

    @property
    def display_total(self):
        from decimal import Decimal
        return Decimal(self.total_paid if self.total_paid is not None else self.subtotal) / 100
