import uuid
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand,CommandError
from django.db import transaction
from django.utils import timezone
from paiement.models import Order
from retours.models import ReturnRequest
from retours.services import submit
from retours.demo import simulate_refund
from retours.mail import order_confirmation,deliver_pending
from retours.access import order_link

class Command(BaseCommand):
    help='Crée une démonstration locale de retour, sans banque ni mouvement de stock réel.'
    def add_arguments(self,parser):
        parser.add_argument('--username',required=True)
        parser.add_argument('--email',required=True)
    def handle(self,*args,**options):
        if not settings.DEBUG or not settings.EMAIL_TEST_MODE or settings.EMAIL_BACKEND!='courriels.backend.LocalMailboxBackend':
            raise CommandError('La démonstration nécessite le serveur de développement et la boîte e-mail locale.')
        user=get_user_model().objects.get(username=options['username'],is_active=True,is_staff=True)
        order_id=uuid.uuid5(uuid.NAMESPACE_URL,'caverne-local-refund-demo-20261015-'+str(user.pk))
        nonce=uuid.uuid5(uuid.NAMESPACE_URL,'caverne-local-refund-request-'+str(order_id))
        with transaction.atomic():
            order,created=Order.objects.get_or_create(pk=order_id,defaults={'user':user,'session_key':'DEMONSTRATION-LOCALE','status':'demo','items':[{'name':'Veste polaire de montagne Beige','size':'M','color':'Beige','quantity':1,'unit_amount':3600,'variant':None,'product':None}],'subtotal':3600,'total_paid':4200,'return_shipping_amount':600,'customer_email':options['email'],'paid_at':timezone.now()})
            if not order.is_demo or order.user_id!=user.pk or order.customer_email!=options['email']:
                raise CommandError('Cette référence existe avec des données différentes ; aucune modification.')
            order_confirmation(order)
            record=submit(order.pk,{0:1},nonce,reason='Simulation demandée pour découvrir le parcours de remboursement.')
            simulate_refund(record.pk,user)
        deliver_pending(order_id=order.pk)
        self.stdout.write('Simulation prête : '+str(record.pk))
        self.stdout.write('Gestion : /gestion/retours/'+str(record.pk)+'/')
        self.stdout.write('Compte client : /mes-retours/')
        # No guest bearer token printed in the terminal log.
