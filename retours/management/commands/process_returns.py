from django.core.management.base import BaseCommand
from retours.mail import deliver_pending
from retours.refunds import reconcile_all

class Command(BaseCommand):
    help='Rapproche les remboursements Stripe et envoie les notifications en attente. À planifier chaque minute en production.'
    def handle(self,*args,**kwargs):
        reconcile_all()
        self.stdout.write(f'{deliver_pending()} notifications envoyées.')
