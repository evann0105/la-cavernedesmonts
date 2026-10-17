import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core import mail
from django.db import close_old_connections, connection
from django.db.models import Sum
from django.test import TransactionTestCase, override_settings, Client
from django.urls import reverse
from django.utils import timezone
from core.models import Product
from paiement.models import Order
from stocks.models import Variant
from .models import ReturnRequest, ReturnLine, ReturnMail, ReturnEvent
from .services import submit, complete, ReturnError
from .refunds import initiate, reconcile, apply_verified_event
from .mail import deliver_pending, order_confirmation
from .access import order_token


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',EMAIL_DELIVERY_ENABLED=True,LOCAL_SKIP_EMAIL_VERIFICATION=True,DEBUG=True,PAYMENTS_ENABLED=True,STRIPE_SECRET_KEY='sk_test_fake',SITE_URL='http://testserver',RETURN_ADDRESS='La Caverne des Monts\n225 Rue des Monts Jura\n01410 Lélex')
class ReturnsTests(TransactionTestCase):
    def setUp(self):
        self.admin=get_user_model().objects.create_superuser('returns-admin',email='admin@example.test',password='test-password')
        self.user=get_user_model().objects.create_user('returns-client',email='client@example.test')
        self.product=Product.objects.create(name='Polaire',slug='polaire-test',price=Decimal('36'))
        self.variant=Variant.objects.create(product=self.product,size='M',color='Bleu',quantity=5)
        self.order=Order.objects.create(user=self.user,session_key='test',status='paid',paid_at=timezone.now(),customer_email=self.user.email,stripe_session_id='cs_return',items=[{'product':self.product.pk,'variant':self.variant.pk,'quantity':2,'unit_amount':3600,'name':'Polaire','size':'M','color':'Bleu'}],subtotal=7200,total_paid=7800,return_shipping_amount=600)

    def declare(self,q=1,**kwargs):return submit(self.order.pk,{0:q},kwargs.pop('submission_id',uuid.uuid4()),**kwargs)
    def refund(self,record,status='succeeded',**changes):
        value={'id':'re_'+str(record.pk),'amount':record.amount,'currency':'eur','payment_intent':'pi_return','metadata':{'return_id':str(record.pk)},'status':status}
        value.update(changes);return value

    def stripe_session(self):return {'payment_status':'paid','amount_total':7800,'client_reference_id':str(self.order.pk),'payment_intent':'pi_return'}

    def test_overlap_and_repeat_submission_are_blocked(self):
        nonce=uuid.uuid4();first=self.declare(submission_id=nonce)
        self.assertEqual(self.declare(submission_id=nonce).pk,first.pk)
        second=self.declare();self.assertEqual(first.amount,3600);self.assertEqual(second.amount,4200)
        with self.assertRaises(ReturnError):self.declare()
        self.assertEqual(ReturnRequest.objects.count(),2)
        self.assertEqual(ReturnMail.objects.count(),2)

    def test_discount_rounding_and_shipping_once(self):
        self.order.total_paid=6601;self.order.paid_line_totals=[6001];self.order.save(update_fields=['total_paid','paid_line_totals'])
        a=self.declare();b=self.declare()
        self.assertEqual(a.amount+b.amount,6601)
        self.assertEqual(a.shipping_amount,0);self.assertEqual(b.shipping_amount,600)

    def test_concurrent_same_item_cannot_overclaim(self):
        if connection.vendor!='postgresql':self.skipTest('PostgreSQL locking test')
        def task():
            close_old_connections()
            try:return submit(self.order.pk,{0:2},uuid.uuid4()).pk
            except ReturnError:return None
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:result=list(pool.map(lambda _:task(),range(2)))
        self.assertEqual(sum(pk is not None for pk in result),1)
        self.assertEqual(ReturnLine.objects.aggregate(total=Sum('quantity'))['total'],2)

    def test_completed_only_once_and_only_resellable_units(self):
        record=self.declare(2);line=record.lines.get()
        with self.assertRaises(ReturnError):complete(record.pk,self.admin,{line.pk:2},{line.pk:2})
        record.refund_state='succeeded';record.save()
        complete(record.pk,self.admin,{line.pk:2},{line.pk:1})
        complete(record.pk,self.admin,{line.pk:2},{line.pk:2})
        self.variant.refresh_from_db();self.assertEqual(self.variant.quantity,6)
        line.refresh_from_db();self.assertEqual(line.restocked,1)
        self.assertEqual(record.events.filter(label__startswith='Dossier terminé').count(),1)

    def test_concurrent_completion_restock_once(self):
        if connection.vendor!='postgresql':self.skipTest('PostgreSQL locking test')
        record=self.declare();record.refund_state='succeeded';record.save();line=record.lines.get()
        def task():
            close_old_connections()
            try:return complete(record.pk,self.admin,{line.pk:1},{line.pk:1}).pk
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _:task(),range(2)))
        self.variant.refresh_from_db();self.assertEqual(self.variant.quantity,6)

    def test_unknown_stock_and_invalid_counts_do_not_complete(self):
        record=self.declare();record.refund_state='succeeded';record.save();line=record.lines.get()
        with self.assertRaises(ReturnError):complete(record.pk,self.admin,{line.pk:0},{line.pk:1})
        self.variant.quantity=None;self.variant.save()
        with self.assertRaises(ReturnError):complete(record.pk,self.admin,{line.pk:1},{line.pk:1})
        record.refresh_from_db();self.assertNotEqual(record.state,'completed')

    def test_real_refund_is_idempotent_and_email_once(self):
        record=self.declare()
        with patch('retours.refunds.stripe.checkout.Session.retrieve',return_value=self.stripe_session()),patch('retours.refunds.stripe.Refund.list',return_value={'data':[]}),patch('retours.refunds.stripe.Refund.create',return_value=self.refund(record)) as create:
            initiate(record.pk,self.admin);initiate(record.pk,self.admin)
            self.assertEqual(create.call_count,1)
            self.assertEqual(create.call_args.kwargs['idempotency_key'],f'return-{record.pk}')
            self.assertEqual(create.call_args.kwargs['amount'],3600)
        deliver_pending();deliver_pending()
        self.assertEqual(len(mail.outbox),2)
        self.assertIn('225 Rue des Monts Jura',mail.outbox[0].body)
        self.assertEqual(record.events.filter(label__startswith='Stripe').count(),1)

    def test_pending_refund_cannot_complete_and_event_verifies(self):
        record=self.declare();record.refund_state='pending';record.payment_intent='pi_return';record.stripe_refund_id='re_'+str(record.pk);record.save()
        line=record.lines.get()
        with self.assertRaises(ReturnError):complete(record.pk,self.admin,{line.pk:1},{line.pk:1})
        with self.assertRaises(ReturnError):apply_verified_event(self.refund(record,amount=999))
        apply_verified_event(self.refund(record));apply_verified_event(self.refund(record))
        self.assertEqual(record.events.filter(label__startswith='Stripe').count(),1)

    def test_ambiguous_attempt_reconciles_existing_refund_without_creating(self):
        record=self.declare();record.refund_state='submitting';record.refund_started_at=timezone.now();record.save()
        with patch('retours.refunds.stripe.checkout.Session.retrieve',return_value=self.stripe_session()),patch('retours.refunds.stripe.Refund.list',return_value={'data':[self.refund(record)]}),patch('retours.refunds.stripe.Refund.create') as create:
            reconcile(record.pk);create.assert_not_called()
        record.refresh_from_db();self.assertEqual(record.refund_state,'succeeded')

    def test_external_refund_blocks_new_attempt(self):
        record=self.declare()
        with patch('retours.refunds.stripe.checkout.Session.retrieve',return_value=self.stripe_session()),patch('retours.refunds.stripe.Refund.list',return_value={'data':[{'id':'external','metadata':{}}]}),patch('retours.refunds.stripe.Refund.create') as create:
            initiate(record.pk,self.admin);create.assert_not_called()
        record.refresh_from_db();self.assertEqual(record.refund_state,'review')

    def test_order_confirmation_contains_private_withdrawal_link(self):
        order_confirmation(self.order);order_confirmation(self.order);deliver_pending();deliver_pending()
        self.assertEqual(len(mail.outbox),1)
        self.assertIn('Déclarer ma rétractation',mail.outbox[0].body)
        self.assertIn('2 × Polaire',mail.outbox[0].body)
        self.assertIn('token=',mail.outbox[0].body)
        self.assertNotIn(self.user.email,order_token(self.order))

    def test_guest_access_owner_permissions_and_csrf(self):
        url=reverse('retours:order',args=[self.order.pk])
        self.assertEqual(self.client.get(url).status_code,403)
        signed=url+'?token='+order_token(self.order)
        response=self.client.get(signed);self.assertEqual(response.status_code,200)
        self.assertEqual(response['Referrer-Policy'],'no-referrer')
        self.assertEqual(Client(enforce_csrf_checks=True).post(signed,{}).status_code,403)
        self.client.force_login(self.user,backend='connexion.backends.EmailOrUsernameBackend')
        self.assertContains(self.client.get(url),'Déclarer ma rétractation')
        self.assertEqual(self.client.get(reverse('retours:manage')).status_code,403)
        other=Order.objects.create(session_key='another',items=[],subtotal=0)
        self.assertEqual(self.client.get(reverse('retours:order',args=[other.pk])).status_code,403)
        self.client.force_login(self.admin,backend='connexion.backends.EmailOrUsernameBackend')
        self.assertContains(self.client.get(reverse('retours:manage')),'Retours et remboursements')

    def test_customer_post_receipt_and_account_history(self):
        self.client.force_login(self.user,backend='connexion.backends.EmailOrUsernameBackend')
        response=self.client.post(reverse('retours:order',args=[self.order.pk]),{'submission_id':uuid.uuid4(),'customer_name':'Client Test','quantity_0':1,'kind':'withdrawal','confirm':'on'},follow=True)
        self.assertContains(response,'Votre déclaration a été enregistrée')
        self.assertContains(self.client.get(reverse('retours:mine')),'Polaire')
        self.assertEqual(len(mail.outbox),1)

    def test_financial_budget_and_disabled_stripe(self):
        record=self.declare();record.amount=9000;record.save()
        with self.assertRaises(ReturnError):initiate(record.pk,self.admin)
        record.amount=3600;record.save()
        with override_settings(PAYMENTS_ENABLED=False),patch('retours.refunds.stripe.Refund.create') as create:
            with self.assertRaises(ReturnError):initiate(record.pk,self.admin)
            create.assert_not_called()

    def test_two_requests_same_customer_refund_independently(self):
        if connection.vendor!='postgresql':self.skipTest('PostgreSQL locking test')
        first=self.declare();second=self.declare()
        def create(**kwargs):
            pk=kwargs['metadata']['return_id']
            return self.refund(ReturnRequest.objects.get(pk=pk))
        def retrieve(refund_id,**kwargs):
            return self.refund(ReturnRequest.objects.get(pk=refund_id[3:]))
        def task(pk):
            close_old_connections()
            try:return initiate(pk,self.admin).refund_state
            finally:close_old_connections()
        with patch('retours.refunds.stripe.checkout.Session.retrieve',return_value=self.stripe_session()),patch('retours.refunds.stripe.Refund.list',return_value={'data':[]}),patch('retours.refunds.stripe.Refund.create',side_effect=create) as call,patch('retours.refunds.stripe.Refund.retrieve',side_effect=retrieve):
            with ThreadPoolExecutor(max_workers=3) as pool:result=list(pool.map(task,[first.pk,second.pk,first.pk]))
            self.assertEqual(result,['succeeded']*3)
            self.assertEqual(call.call_count,2)
            self.assertEqual(sum(c.kwargs['amount'] for c in call.call_args_list),7800)
        self.assertEqual(ReturnRequest.objects.filter(refund_state='succeeded').count(),2)

    def test_old_ambiguous_attempt_is_never_recreated(self):
        from datetime import timedelta
        record=self.declare();record.refund_state='submitting';record.refund_started_at=timezone.now()-timedelta(days=2);record.save()
        with patch('retours.refunds.stripe.checkout.Session.retrieve',return_value=self.stripe_session()),patch('retours.refunds.stripe.Refund.list',return_value={'data':[]}),patch('retours.refunds.stripe.Refund.create') as call:
            reconcile(record.pk);call.assert_not_called()
        record.refresh_from_db();self.assertEqual(record.refund_state,'review')

    def test_email_failure_remains_retryable(self):
        record=self.declare()
        with patch('courriels.messages.EmailMultiAlternatives.send',side_effect=RuntimeError('Delivery unavailable')):
            self.assertEqual(deliver_pending(),0)
        notice=ReturnMail.objects.get();self.assertIsNone(notice.sent_at)
        notice.last_attempt_at=None;notice.save()
        self.assertEqual(deliver_pending(),1)
        self.assertEqual(len(mail.outbox),1)

    def test_admin_complete_post_and_customer_cannot_trigger_payment(self):
        record=self.declare();line=record.lines.get()
        url=reverse('retours:manage_detail',args=[record.pk])
        self.client.force_login(self.user,backend='connexion.backends.EmailOrUsernameBackend')
        with patch('retours.refunds.stripe.Refund.create') as call:
            self.assertEqual(self.client.post(url,{'action':'refund','confirm_refund':'yes'}).status_code,403)
            call.assert_not_called()
        record.refund_state='succeeded';record.save()
        self.client.force_login(self.admin,backend='connexion.backends.EmailOrUsernameBackend')
        review=self.client.post(url,{'action':'review_complete',f'received_{line.pk}':1,f'restock_{line.pk}':1,'confirm':'on'})
        token=review.context['review']['token']
        response=self.client.post(url,{'action':'complete','completion_token':token,'confirm_completion':'yes'},follow=True)
        self.assertContains(response,'Dossier terminé')
        self.variant.refresh_from_db();self.assertEqual(self.variant.quantity,6)

    @override_settings(PAYMENTS_ENABLED=False,STRIPE_SECRET_KEY='')
    def test_local_demo_refunds_without_stripe_and_without_real_stock(self):
        self.order.status='demo';self.order.save()
        record=self.declare();line=record.lines.get()
        with patch('retours.refunds.stripe.Refund.create') as api:
            initiate(record.pk,self.admin);initiate(record.pk,self.admin)
            complete(record.pk,self.admin,{line.pk:1},{line.pk:1})
            api.assert_not_called()
        self.variant.refresh_from_db();self.assertEqual(self.variant.quantity,5)
        record.refresh_from_db();self.assertEqual(record.state,'completed')
        self.assertEqual(record.events.filter(label__startswith='SIMULATION').count(),1)
        self.client.force_login(self.admin,backend='connexion.backends.EmailOrUsernameBackend')
        response=self.client.get('/gestion/tableau-de-bord/')
        self.assertEqual(response.context['order_count'],0)
        self.assertEqual(response.context['refund_amount'],0)
        deliver_pending();self.assertTrue(all(m.subject.startswith('[TEST]') for m in mail.outbox))

    def test_demo_is_blocked_in_production_and_cannot_be_used_for_real_order(self):
        from retours.demo import simulate_refund
        record=self.declare()
        with self.assertRaises(ReturnError):simulate_refund(record.pk,self.admin)
        self.order.status='demo';self.order.save()
        with override_settings(DEBUG=False):
            with self.assertRaises(ReturnError):initiate(record.pk,self.admin)
            with self.assertRaises(ReturnError):complete(record.pk,self.admin,{record.lines.get().pk:1},{record.lines.get().pk:1})

    def test_return_stages_advance_without_refund_or_stock_change(self):
        from .services import mark_shipped, mark_received, mark_checked
        record = self.declare()
        with self.assertRaises(ReturnError):mark_checked(record.pk, self.admin)
        for action, expected, stamp in [(mark_shipped, 'shipped', 'shipped_at'), (mark_received, 'received', 'received_at'), (mark_checked, 'checked', 'checked_at')]:
            updated = action(record.pk, self.admin)
            self.assertEqual(updated.return_state, expected)
            self.assertIsNotNone(getattr(updated, stamp))
            self.assertEqual(updated.refund_state, 'none')
            action(record.pk, self.admin)
        mark_received(record.pk, self.admin)
        record.refresh_from_db()
        self.assertEqual(record.return_state, 'checked')
        self.assertEqual(record.events.count(), 4)  # declaration + three physical stages
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.quantity, 5)
        self.assertEqual(record.lines.get().restocked, 0)

    def test_receipt_without_reported_shipment_does_not_invent_shipment(self):
        from .services import mark_received
        record = mark_received(self.declare().pk, self.admin)
        self.assertEqual(record.return_state, 'received')
        self.assertIsNone(record.shipped_at)
        self.assertEqual(record.return_steps[1]['state'], 'unknown')
        self.assertEqual(record.return_steps[2]['state'], 'current')

    def test_refund_confirmation_does_not_advance_physical_return(self):
        record = self.declare()
        with patch('retours.refunds.stripe.checkout.Session.retrieve', return_value=self.stripe_session()), patch('retours.refunds.stripe.Refund.list', return_value={'data': []}), patch('retours.refunds.stripe.Refund.create', return_value=self.refund(record)):
            initiate(record.pk, self.admin)
        record.refresh_from_db()
        self.assertEqual(record.refund_state, 'succeeded')
        self.assertEqual(record.return_state, 'declared')
        self.assertNotEqual(record.state, 'completed')
        self.assertEqual(record.refund_steps[-1]['label'], 'Confirmé')
        url = reverse('retours:order', args=[self.order.pk]) + '?token=' + order_token(self.order)
        page = self.client.get(url)
        self.assertContains(page, 'Retour des articles')
        self.assertContains(page, 'Remboursement confirmé')
        self.assertContains(page, 'La réception des articles n’a pas encore été signalée.')

    def test_admin_control_confirmation_permissions_and_customer_tracking(self):
        record = self.declare()
        url = reverse('retours:manage_detail', args=[record.pk])
        self.client.force_login(self.admin, backend='connexion.backends.EmailOrUsernameBackend')
        self.client.post(url, {'action': 'check', 'confirm_check': 'yes'})
        record.refresh_from_db()
        self.assertEqual(record.return_state, 'declared')
        self.client.post(url, {'action': 'receive'})
        self.client.post(url, {'action': 'check'})
        record.refresh_from_db()
        self.assertEqual(record.return_state, 'received')
        self.client.post(url, {'action': 'check', 'confirm_check': 'yes'})
        record.refresh_from_db()
        self.assertEqual(record.return_state, 'checked')
        self.assertEqual(record.refund_state, 'none')
        self.client.force_login(self.user, backend='connexion.backends.EmailOrUsernameBackend')
        for action in ['ship', 'receive', 'check']:
            self.assertEqual(self.client.post(url, {'action': action, 'confirm_check': 'yes'}).status_code, 403)
        page = self.client.get(reverse('retours:mine'))
        self.assertContains(page, 'Retour des articles')
        self.assertContains(page, 'Contrôlé')
        self.assertContains(page, 'Le remboursement n’a pas encore été déclenché.')

    def test_tracking_displays_failed_or_uncertain_refunds_without_confirming(self):
        record = self.declare()
        self.client.force_login(self.user, backend='connexion.backends.EmailOrUsernameBackend')
        for state in ['pending', 'submitting', 'failed', 'review']:
            record.refund_state = state
            record.save(update_fields=['refund_state'])
            page = self.client.get(reverse('retours:mine'))
            self.assertContains(page, record.get_refund_state_display())
            self.assertNotContains(page, 'Remboursement confirmé')
            self.assertNotEqual(record.refund_steps[-1]['label'], 'Confirmé')

    def test_legacy_tracking_migration_preserves_refunds_and_stock(self):
        from importlib import import_module
        from types import SimpleNamespace
        from django.apps import apps
        a = self.declare()
        b = self.declare()
        a.state = 'received'; a.save(update_fields=['state'])
        b.state = 'completed'; b.refund_state = 'succeeded'; b.completed_at = timezone.now()
        b.save(update_fields=['state', 'refund_state', 'completed_at'])
        migrate = import_module('retours.migrations.0003_separate_return_tracking').populate_return_tracking
        migrate(apps, SimpleNamespace(connection=connection))
        a.refresh_from_db(); b.refresh_from_db()
        self.assertEqual((a.return_state, a.refund_state), ('received', 'none'))
        self.assertEqual((b.return_state, b.refund_state), ('checked', 'succeeded'))
        self.assertEqual(b.checked_at, b.completed_at)
        self.variant.refresh_from_db(); self.assertEqual(self.variant.quantity, 5)

    def shipment_data(self, **changes):
        data = {'submission_id': str(uuid.uuid4()), 'carrier': 'Colissimo', 'tracking_number': 'TEST-123', 'confirm': 'on'}
        data.update(changes)
        return data

    def shipment_photo(self):
        from io import BytesIO
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        stream = BytesIO()
        Image.new('RGB', (120, 80), 'white').save(stream, 'JPEG')
        return SimpleUploadedFile('recu.jpeg', stream.getvalue(), content_type='image/jpeg')

    def test_customer_upload_is_private_encrypted_and_not_financial(self):
        record = self.declare()
        url = reverse('retours:shipment', args=[record.pk]) + '?token=' + order_token(self.order)
        data = self.shipment_data(proof=self.shipment_photo())
        response = self.client.post(url, data, follow=True)
        self.assertContains(response, 'Votre expédition a été enregistrée')
        shipment = record.shipments.get()
        record.refresh_from_db()
        self.assertEqual(record.return_state, 'shipped')
        self.assertEqual(record.refund_state, 'none')
        self.assertTrue(shipment.has_proof)
        self.assertIsNone(shipment.seen_at)
        self.variant.refresh_from_db(); self.assertEqual(self.variant.quantity, 5)
        with connection.cursor() as cursor:
            cursor.execute('SELECT carrier, tracking_number, proof FROM retours_returnshipment WHERE id = %s', [shipment.pk])
            raw = cursor.fetchone()
        self.assertTrue(all(value.startswith('enc:v1:') for value in raw))
        self.assertNotIn('TEST-123', str(raw))
        # Repeat the same POST nonce: no duplicate record or notification.
        self.client.post(url, self.shipment_data(submission_id=data['submission_id']))
        self.assertEqual(record.shipments.count(), 1)

    def test_private_proof_requires_owner_token_or_verified_returns_manager(self):
        record = self.declare()
        self.client.force_login(self.user, backend='connexion.backends.EmailOrUsernameBackend')
        self.client.post(reverse('retours:shipment', args=[record.pk]), self.shipment_data(proof=self.shipment_photo()))
        shipment = record.shipments.get()
        url = reverse('retours:proof', args=[shipment.pk])
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.get(url + '?token=bad').status_code, 403)
        other = get_user_model().objects.create_user('unrelated-return-user')
        self.client.force_login(other, backend='connexion.backends.EmailOrUsernameBackend')
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(reverse('retours:shipment', args=[record.pk]), self.shipment_data()).status_code, 403)
        self.client.logout()
        response = self.client.get(url + '?token=' + order_token(self.order))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/webp')
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.client.force_login(self.admin, backend='connexion.backends.EmailOrUsernameBackend')
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_invalid_missing_and_oversize_proofs_are_rejected(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        record = self.declare()
        self.client.force_login(self.user, backend='connexion.backends.EmailOrUsernameBackend')
        url = reverse('retours:shipment', args=[record.pk])
        for changes in [
            {'tracking_number':'', 'carrier':''},
            {'proof':SimpleUploadedFile('fake.jpeg', b'<script>bad</script>', content_type='image/jpeg')},
            {'proof':SimpleUploadedFile('large.jpeg', b'x' * (5 * 1024 * 1024 + 1), content_type='image/jpeg')},
            {'confirm':''},
        ]:
            response = self.client.post(url, self.shipment_data(**changes))
            self.assertEqual(response.status_code, 200)
            self.assertFalse(record.shipments.exists())
        self.assertEqual(Client(enforce_csrf_checks=True).post(url + '?token=' + order_token(self.order), self.shipment_data()).status_code, 403)

    def test_notification_visible_and_acknowledgement_keeps_proof_history(self):
        from .services import add_shipment
        record = self.declare()
        shipment = add_shipment(record.pk, uuid.uuid4(), tracking_number='TEST-42')
        self.client.force_login(self.admin, backend='connexion.backends.EmailOrUsernameBackend')
        self.assertContains(self.client.get(reverse('retours:manage')), 'nouvelle(s) information(s) d’expédition')
        url = reverse('retours:manage_detail', args=[record.pk])
        self.assertContains(self.client.get(url), 'Nouvelle expédition client')
        self.client.post(url, {'action':'ack_shipments'})
        shipment.refresh_from_db(); self.assertIsNotNone(shipment.seen_at)
        self.assertNotContains(self.client.get(url), 'Nouvelle expédition client')
        self.assertContains(self.client.get(url), 'TEST-42')
        self.assertEqual(record.shipments.count(), 1)

    @override_settings(RETURN_NOTIFICATION_EMAIL='boutique@example.test')
    def test_optional_staff_email_is_idempotent_and_contains_no_guest_token(self):
        from .services import add_shipment
        record = self.declare(); nonce = uuid.uuid4()
        shipment = add_shipment(record.pk, nonce, tracking_number='TEST-45')
        add_shipment(record.pk, nonce, tracking_number='TEST-45')
        notice = ReturnMail.objects.get(key=f'shipment-{shipment.pk}')
        self.assertEqual(notice.payload['recipient'], 'boutique@example.test')
        deliver_pending(); deliver_pending()
        staff_mail = [message for message in mail.outbox if message.to == ['boutique@example.test']]
        self.assertEqual(len(staff_mail), 1)
        self.assertIn('/gestion/retours/', staff_mail[0].body)
        self.assertNotIn('token=', staff_mail[0].body)
        self.assertEqual(len(staff_mail[0].attachments), 0)

    def test_multiple_parcels_no_regression_and_closed_record_is_immutable(self):
        from .services import add_shipment, mark_received
        record = self.declare()
        mark_received(record.pk, self.admin)
        for n in range(2):add_shipment(record.pk, uuid.uuid4(), tracking_number=f'PARCEL-{n}')
        record.refresh_from_db(); self.assertEqual(record.return_state, 'received')
        self.assertEqual(record.shipments.count(), 2)
        record.refund_state = 'succeeded'; record.save()
        line = record.lines.get()
        complete(record.pk, self.admin, {line.pk:1}, {line.pk:1})
        with self.assertRaises(ReturnError):add_shipment(record.pk, uuid.uuid4(), tracking_number='TOO-LATE')
        self.assertEqual(record.shipments.count(), 2)

    def test_shipment_count_cap_and_nonce_is_bound_to_its_record(self):
        from .services import add_shipment
        first = self.declare(); second = self.declare(); nonce = uuid.uuid4()
        add_shipment(first.pk, nonce, tracking_number='FIRST')
        with self.assertRaises(ReturnError):add_shipment(second.pk, nonce, tracking_number='WRONG')
        for n in range(9):add_shipment(first.pk, uuid.uuid4(), tracking_number=str(n))
        with self.assertRaises(ReturnError):add_shipment(first.pk, uuid.uuid4(), tracking_number='ELEVEN')
        self.assertEqual(first.shipments.count(), 10)

    def test_refund_breakdown_prorates_discounts_without_losing_cents(self):
        self.order.total_paid = 6601
        self.order.paid_line_totals = [6001]
        self.order.save(update_fields=['total_paid','paid_line_totals'])
        first = self.declare(); last = self.declare()
        a, b = first.amount_breakdown, last.amount_breakdown
        self.assertEqual(a['gross'], Decimal('36'))
        self.assertEqual((a['reductions'], b['reductions']), (Decimal('6'), Decimal('5.99')))
        self.assertEqual(a['net'] + b['net'], Decimal('60.01'))
        self.assertEqual((a['shipping'], b['shipping']), (Decimal('0'), Decimal('6')))
        self.assertEqual(a['total'] + b['total'], Decimal('66.01'))
        self.assertTrue(a['balanced'] and b['balanced'])

    def test_multi_article_partial_refund_shows_only_selected_quantities(self):
        self.order.items = [
            {'name':'Veste', 'quantity':2, 'unit_amount':3000},
            {'name':'Bonnet', 'quantity':1, 'unit_amount':1000},
        ]
        self.order.subtotal = 7000; self.order.total_paid = 6500; self.order.return_shipping_amount = 500
        self.order.paid_line_totals = [5142,858]
        self.order.save()
        first = submit(self.order.pk, {0:1}, uuid.uuid4())
        last = submit(self.order.pk, {0:1, 1:1}, uuid.uuid4())
        a, b = first.amount_breakdown, last.amount_breakdown
        self.assertEqual(a['gross'], Decimal('30'))
        self.assertEqual(a['reductions'], Decimal('4.29'))
        self.assertEqual(b['gross'], Decimal('40'))
        self.assertEqual(b['reductions'], Decimal('5.71'))
        self.assertEqual(a['total'] + b['total'], Decimal('65'))
        self.assertEqual(len(a['rows']), 1)
        self.assertEqual(len(b['rows']), 2)
        self.assertEqual(a['shipping'] + b['shipping'], Decimal('5'))

    def test_price_snapshot_never_uses_current_catalogue_or_rewritten_order_items(self):
        record = self.declare()
        self.product.price = Decimal('999'); self.product.save()
        self.order.items[0]['unit_amount'] = 99000; self.order.save(update_fields=['items'])
        record.refresh_from_db()
        self.assertEqual(record.lines.get().unit_amount, 3600)
        self.assertEqual(record.amount_breakdown['gross'], Decimal('36'))
        self.assertEqual(record.amount, 3600)

    def test_unavailable_historical_detail_and_inconsistent_totals_are_not_invented(self):
        record = self.declare()
        record.lines.update(unit_amount=None)
        self.assertFalse(record.amount_breakdown['known'])
        self.assertIsNone(record.amount_breakdown['rows'][0]['reduction'])
        self.client.force_login(self.user, backend='connexion.backends.EmailOrUsernameBackend')
        page = self.client.get(reverse('retours:mine'))
        self.assertContains(page, 'Le détail des remises n’est pas disponible')
        record.amount += 1; record.save(update_fields=['amount'])
        self.assertFalse(record.amount_breakdown['balanced'])
        self.assertContains(self.client.get(reverse('retours:mine')), 'La ventilation de ce dossier doit être vérifiée')

    def test_breakdown_is_rendered_in_customer_admin_and_new_refund_email(self):
        self.order.total_paid = 6600; self.order.paid_line_totals=[6000]; self.order.save(update_fields=['total_paid','paid_line_totals'])
        record = self.declare()
        self.client.force_login(self.user, backend='connexion.backends.EmailOrUsernameBackend')
        page = self.client.get(reverse('retours:mine'))
        for label in ['Détail du montant', 'Part des remises', 'Livraison incluse dans ce remboursement', 'Total à rembourser']:
            self.assertContains(page, label)
        self.assertContains(page, '6,00 €')
        self.client.force_login(self.admin, backend='connexion.backends.EmailOrUsernameBackend')
        self.assertContains(self.client.get(reverse('retours:manage_detail', args=[record.pk])), 'Articles après remises')
        record.refund_state = 'succeeded'; record.save(update_fields=['refund_state'])
        from .mail import return_notice
        return_notice(record, 'refund', 'Remboursement confirmé.')
        deliver_pending()
        notice = next(message for message in mail.outbox if message.subject.startswith('Votre remboursement'))
        self.assertIn('Avant remise : 36,00 €', notice.body)
        self.assertIn('Part des remises : 6,00 €', notice.body)
        self.assertIn('Total remboursé : 30,00 €', notice.body)

    def test_price_backfill_preserves_existing_refund_amount_and_unknown_prices(self):
        from importlib import import_module
        from types import SimpleNamespace
        from django.apps import apps
        a = self.declare(); b = self.declare()
        a.lines.update(unit_amount=None); b.lines.update(unit_amount=None, name='Référence historique inconnue')
        migrate = import_module('retours.migrations.0005_refund_price_snapshot').restore_purchase_prices
        migrate(apps, SimpleNamespace(connection=connection))
        self.assertEqual(a.lines.get().unit_amount, 3600)
        self.assertIsNone(b.lines.get().unit_amount)
        a.refresh_from_db(); b.refresh_from_db()
        self.assertEqual((a.amount, b.amount), (3600, 4200))

    def closure_review(self, record, received=1, restock=1):
        self.client.force_login(self.admin, backend='connexion.backends.EmailOrUsernameBackend')
        line = record.lines.get()
        return self.client.post(reverse('retours:manage_detail', args=[record.pk]), {
            'action':'review_complete', f'received_{line.pk}':received,
            f'restock_{line.pk}':restock, 'confirm':'on'})

    def test_closure_review_is_required_and_does_not_change_stock(self):
        record = self.declare(); record.refund_state='succeeded'; record.save()
        review = self.closure_review(record)
        self.assertContains(review, 'Récapitulatif de clôture')
        self.assertEqual(review.context['review']['restock_total'], 1)
        self.variant.refresh_from_db(); self.assertEqual(self.variant.quantity, 5)
        record.refresh_from_db(); self.assertNotEqual(record.state, 'completed')
        url = reverse('retours:manage_detail', args=[record.pk])
        self.client.post(url, {'action':'complete', 'confirm_completion':'yes'})
        record.refresh_from_db(); self.assertNotEqual(record.state, 'completed')
        token = review.context['review']['token']
        self.client.post(url, {'action':'complete', 'completion_token':token})
        record.refresh_from_db(); self.assertNotEqual(record.state, 'completed')
        for _ in range(2):
            self.client.post(url, {'action':'complete', 'completion_token':token, 'confirm_completion':'yes'})
        record.refresh_from_db(); self.assertEqual(record.state, 'completed')
        self.variant.refresh_from_db(); self.assertEqual(self.variant.quantity, 6)
        self.assertEqual(ReturnMail.objects.filter(key=f'return_completed-{record.pk}').count(), 1)
        notice = ReturnMail.objects.get(key=f'return_completed-{record.pk}')
        self.assertIn('Articles reçus : 1', notice.payload['details'])
        self.assertIn('Articles remis en stock : 1', notice.payload['details'])
        self.assertIn('Total remboursé : 36,00 €', notice.payload['details'])

    def test_closure_review_can_be_corrected_and_rejects_invalid_quantities(self):
        record = self.declare(); record.refund_state='succeeded'; record.save()
        review = self.closure_review(record, restock=0)
        self.assertContains(review, 'Quantité reçue qui ne sera pas remise en stock')
        line = record.lines.get()
        url = reverse('retours:manage_detail', args=[record.pk])
        correction = self.client.post(url, {'action':'edit_complete', 'confirm':'on',
            f'received_{line.pk}':1, f'restock_{line.pk}':0})
        self.assertEqual(correction.context['form'][f'restock_{line.pk}'].value(), '0')
        invalid = self.closure_review(record, received=0, restock=1)
        self.assertContains(invalid, 'ne peut pas dépasser la quantité reçue')
        self.assertNotContains(invalid, 'name="completion_token"')

    def test_stale_tampered_and_expired_closure_confirmations_are_rejected(self):
        import time
        record = self.declare(); record.refund_state='succeeded'; record.save()
        review = self.closure_review(record)
        token = review.context['review']['token']
        url = reverse('retours:manage_detail', args=[record.pk])
        self.client.post(url, {'action':'complete', 'completion_token':token+'tampered', 'confirm_completion':'yes'})
        now = time.time()
        with patch('django.core.signing.time.time', return_value=now + 901):
            self.client.post(url, {'action':'complete', 'completion_token':token, 'confirm_completion':'yes'})
        record.return_state='received'; record.save(update_fields=['return_state'])
        page = self.client.post(url, {'action':'complete', 'completion_token':token, 'confirm_completion':'yes'}, follow=True)
        self.assertContains(page, 'Le dossier a changé depuis le récapitulatif')
        record.refresh_from_db(); self.assertNotEqual(record.state, 'completed')
        self.variant.refresh_from_db(); self.assertEqual(self.variant.quantity, 5)

    def test_closure_cannot_be_confirmed_by_another_administrator(self):
        record = self.declare(); record.refund_state='succeeded'; record.save()
        review = self.closure_review(record)
        other = get_user_model().objects.create_superuser('other-return-admin', email='other@example.test')
        self.client.force_login(other, backend='connexion.backends.EmailOrUsernameBackend')
        self.client.post(reverse('retours:manage_detail', args=[record.pk]), {
            'action':'complete', 'completion_token':review.context['review']['token'], 'confirm_completion':'yes'})
        record.refresh_from_db(); self.assertNotEqual(record.state, 'completed')
