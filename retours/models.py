import uuid
from decimal import Decimal
from django.conf import settings
from django.db import models
from config.encrypted_fields import EncryptedTextField, EncryptedJSONField


class ReturnRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey('paiement.Order', on_delete=models.PROTECT, related_name='returns')
    submission_id = models.UUIDField(unique=True, editable=False)
    state = models.CharField(max_length=12, default='requested', choices=[('requested','Demande enregistrée'),('received','Retour reçu'),('completed','Terminé')])
    return_state = models.CharField(max_length=12, default='declared', choices=[('declared','Déclaré'),('shipped','Expédié'),('received','Reçu'),('checked','Contrôlé')])
    shipped_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    checked_at = models.DateTimeField(null=True, blank=True)
    kind = models.CharField(max_length=12, choices=[('withdrawal','Rétractation'),('problem','Article défectueux / erreur')], default='withdrawal')
    reason = EncryptedTextField(blank=True)
    declarant = EncryptedJSONField(default=dict, blank=True)
    amount = models.PositiveIntegerField()
    shipping_amount = models.PositiveIntegerField(default=0)
    refund_state = models.CharField(max_length=12, default='none', choices=[('none','À rembourser'),('submitting','Confirmation nécessaire'),('pending','Remboursement en cours'),('succeeded','Remboursement confirmé'),('failed','Remboursement échoué'),('review','Vérification nécessaire')])
    stripe_refund_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    payment_intent = models.CharField(max_length=255, blank=True)
    refund_started_at = models.DateTimeField(null=True, blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        permissions = [('manage_returns', 'Gérer les retours et les remboursements')]

    @property
    def display_amount(self):
        return Decimal(self.amount) / 100

    @property
    def short_reference(self):
        return str(self.pk)[:8].upper()


    @property
    def dossier_status(self):
        return 'Dossier terminé' if self.state == 'completed' else 'Dossier en cours'

    @property
    def amount_breakdown(self):
        from .amounts import breakdown
        return breakdown(self)

    @property
    def return_steps(self):
        stages = [('declared', 'Déclaré', self.created_at), ('shipped', 'Expédié', self.shipped_at),
                  ('received', 'Reçu', self.received_at), ('checked', 'Contrôlé', self.checked_at)]
        current = [key for key, _, _ in stages].index(self.return_state)
        steps = []
        for index, (key, label, date) in enumerate(stages):
            state = 'current' if index == current else ('done' if index < current else 'upcoming')
            # A receipt does not prove that shipment was recorded (e.g. hand delivery).
            if key == 'shipped' and index < current and not self.shipped_at:
                state = 'unknown'
            steps.append({'label':label, 'state':state, 'date':date})
        return steps

    @property
    def refund_steps(self):
        started = self.refund_state != 'none'
        terminal = self.refund_state in ('succeeded', 'failed')
        final = 'Confirmé' if self.refund_state == 'succeeded' else ('Échoué' if self.refund_state == 'failed' else 'Confirmation')
        return [
            {'label':'À effectuer', 'state':'done' if started else 'current'},
            {'label':'En cours', 'state':'done' if terminal else ('current' if started else 'upcoming')},
            {'label':final, 'state':('error' if self.refund_state == 'failed' else 'current') if terminal else 'upcoming'},
        ]

    @property
    def return_tracking_note(self):
        return {
            'declared':'La déclaration est enregistrée. La réception des articles n’a pas encore été signalée.',
            'shipped':'L’expédition a été signalée à la boutique. Les articles ne sont pas encore enregistrés comme reçus.',
            'received':'La boutique a enregistré la réception. Le contrôle des articles reste à confirmer.',
            'checked':'Le contrôle des articles est terminé. La remise en stock est enregistrée uniquement à la clôture du dossier.',
        }[self.return_state] if self.state != 'completed' else 'Le contrôle et les quantités finales sont enregistrés dans ce dossier.'

    @property
    def refund_tracking_note(self):
        return {
            'none':'Le remboursement n’a pas encore été déclenché.',
            'submitting':'Une demande est en cours de vérification. Le remboursement n’est pas encore confirmé.',
            'pending':'Le remboursement est en cours de traitement. Sa confirmation est attendue.',
            'succeeded':('Le remboursement a été simulé : aucune somme n’a été transférée.' if self.order.is_demo else 'Le remboursement est confirmé. Son apparition sur le compte dépend des délais bancaires.'),
            'failed':'Le remboursement a échoué. La boutique doit vérifier le dossier ; aucune réussite n’est présumée.',
            'review':'Le remboursement doit être vérifié par la boutique. Il n’est pas confirmé.',
        }[self.refund_state]


class ReturnLine(models.Model):
    request = models.ForeignKey(ReturnRequest, on_delete=models.PROTECT, related_name='lines')
    item_index = models.PositiveIntegerField()
    variant = models.ForeignKey('stocks.Variant', null=True, blank=True, on_delete=models.PROTECT)
    name = models.CharField(max_length=255)
    size = models.CharField(max_length=50, blank=True)
    color = models.CharField(max_length=80, blank=True)
    quantity = models.PositiveIntegerField()
    amount = models.PositiveIntegerField()
    unit_amount = models.PositiveIntegerField(null=True, blank=True, help_text='Prix unitaire au moment de l’achat, en centimes ; jamais le prix courant.')
    received = models.PositiveIntegerField(default=0)
    restocked = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['item_index']
        constraints = [
            models.UniqueConstraint(fields=['request','item_index'],name='one_return_item'),
            models.CheckConstraint(condition=models.Q(quantity__gt=0),name='return_quantity_positive'),
            models.CheckConstraint(condition=models.Q(received__lte=models.F('quantity')),name='return_received_limit'),
            models.CheckConstraint(condition=models.Q(restocked__lte=models.F('received')),name='return_restock_limit'),
        ]

    @property
    def display_amount(self):
        return Decimal(self.amount) / 100


class ReturnEvent(models.Model):
    request = models.ForeignKey(ReturnRequest, on_delete=models.PROTECT, related_name='events')
    label = models.TextField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['created_at','pk']


class ReturnMail(models.Model):
    key = models.CharField(max_length=100, unique=True)
    order = models.ForeignKey('paiement.Order', on_delete=models.PROTECT)
    request = models.ForeignKey(ReturnRequest, null=True, on_delete=models.PROTECT)
    payload = EncryptedJSONField(default=dict)
    sent_at = models.DateTimeField(null=True)
    attempts = models.PositiveIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True)


class ReturnShipment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(ReturnRequest, on_delete=models.PROTECT, related_name='shipments')
    submission_id = models.UUIDField(unique=True, editable=False)
    carrier = EncryptedTextField(blank=True)
    tracking_number = EncryptedTextField(blank=True)
    proof = EncryptedTextField(blank=True)
    has_proof = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-pk']
