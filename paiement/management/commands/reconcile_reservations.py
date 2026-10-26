from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings
from django.db import close_old_connections
import time
from paiement.models import Order
from paiement.stock_checkout import get_session, settle
from stocks.reservations import StockUnavailable
import stripe

class Command(BaseCommand):
    help='Vérifie les réservations échues auprès de Stripe, sans libération sur simple délai local.'
    def add_arguments(self,parser):
        parser.add_argument('--loop',action='store_true',help='Vérification chaque minute pour le serveur local.')

    def handle(self,*args,**options):
        while True:
            close_old_connections()
            if settings.STRIPE_SECRET_KEY:
                self.reconcile()
            from retours.refunds import reconcile_all
            from retours.mail import deliver_pending
            from avoirs.mail import deliver_pending as deliver_benefits
            reconcile_all()
            deliver_pending()
            deliver_benefits()
            from alertes.stock import scan_low_stock
            from alertes.mail import deliver_pending as deliver_seller_notices
            scan_low_stock()
            deliver_seller_notices()
            if not options['loop']:break
            time.sleep(60)

    def reconcile(self):
        from avoirs.services import BenefitError
        checked=failed=0
        for pk in Order.objects.filter(status='pending',reservation_expires_at__lte=timezone.now()).values_list('pk',flat=True).iterator():
            try:
                session=get_session(pk)
                if session is not None and not settle(session):
                    failed+=1;continue
                checked+=1
            except (stripe.StripeError,StockUnavailable,BenefitError):
                failed+=1
        self.stdout.write(f'{checked} réservations vérifiées ; {failed} à vérifier (stock conservé).')
