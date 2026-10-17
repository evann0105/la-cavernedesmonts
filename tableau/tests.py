from datetime import datetime
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from paiement.models import Order


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True)
class DashboardTests(TestCase):
    def setUp(self):
        self.admin=get_user_model().objects.create_superuser('dashboard-admin',email='admin@example.invalid',password='test-only')
        self.client.force_login(self.admin,backend='connexion.backends.EmailOrUsernameBackend')
        self.url=reverse('tableau:dashboard')

    def sale(self,year,month,amount=1000,**extra):
        data=dict(session_key='test',items=[{'product':1,'name':'Polaire','size':'M','quantity':2,'unit_amount':400}],subtotal=800,total_paid=amount,status='paid',paid_at=timezone.make_aware(datetime(year,month,1)))
        data.update(extra)
        return Order.objects.create(**data)

    def test_paid_date_amount_and_monthly_breakdown(self):
        self.sale(2026,1,1000);self.sale(2026,1,2000)
        self.sale(2026,2,500);self.sale(2025,12,9999)
        self.sale(2026,1,9999,status='pending')
        self.sale(2026,1,total_paid=None)
        response=self.client.get(self.url,{'annee':2026,'mois':1})
        c=response.context
        self.assertEqual(c['amount'],Decimal('30'))
        self.assertEqual(c['order_count'],2)
        self.assertEqual(c['average'],Decimal('15'))
        self.assertEqual(c['units'],4)
        self.assertEqual(c['annual_amount'],Decimal('35'))
        self.assertEqual(c['incomplete'],1)
        self.assertEqual(c['monthly'][0]['amount'],Decimal('30'))
        self.assertEqual(c['monthly'][1]['count'],1)
        self.assertEqual(c['top_products'][0]['amount'],Decimal('16'))
        self.assertEqual(self.client.get(self.url,{'annee':2026,'mois':0}).context['order_count'],3)

    def test_empty_invalid_period_and_pagination(self):
        response=self.client.get(self.url,{'annee':'invalid','mois':99})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['average'],0)
        self.assertContains(response,'Aucune vente payée')
        for _ in range(16):self.sale(2026,1)
        c=self.client.get(self.url,{'annee':2026,'mois':1,'page':2}).context
        self.assertEqual(len(c['page_obj']),1)
        self.assertEqual(c['order_count'],16)

    def test_customer_and_anonymous_cannot_read_sales(self):
        user=get_user_model().objects.create_user('customer')
        self.client.force_login(user,backend='connexion.backends.EmailOrUsernameBackend')
        self.assertEqual(self.client.get(self.url).status_code,403)
        self.assertNotContains(self.client.get('/mon-compte/'),'Tableau de bord')
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code,302)

    def test_return_actions_cover_all_periods_and_exclude_completed_and_demo(self):
        import uuid
        from datetime import timedelta
        from retours.models import ReturnRequest, ReturnShipment
        from retours.worklist import action_definitions
        order = self.sale(2025, 1)
        def create(**values):
            return ReturnRequest.objects.create(order=order,submission_id=uuid.uuid4(),amount=500,**values)
        new = create()
        received = create(state='received', return_state='received')
        pending = create(return_state='checked', refund_state='pending')
        failed = create(return_state='checked', refund_state='failed')
        ready = create(return_state='checked', refund_state='succeeded')
        older = create(return_state='shipped', refund_state='succeeded')
        ReturnRequest.objects.filter(pk=older.pk).update(created_at=timezone.now()-timedelta(days=8))
        create(state='completed', return_state='checked', refund_state='succeeded')
        demo = self.sale(2026, 1, status='demo')
        ReturnRequest.objects.create(order=demo,submission_id=uuid.uuid4(),amount=500)
        ReturnShipment.objects.create(request=new, submission_id=uuid.uuid4(), tracking_number='TEST')
        response = self.client.get(self.url, {'annee':2026, 'mois':1})
        attention = response.context['return_attention']
        counts = {card['key']:card['count'] for card in attention['cards']}
        self.assertEqual(attention['total'], 6)
        self.assertEqual(counts, {'new':1,'check':1,'refund_due':2,'refund_pending':1,'refund_failed':1,'shipments':1,'close':1,'older':1})
        self.assertEqual(attention['records'][0].pk, failed.pk)
        expected = {'new':{new.pk},'check':{received.pk},'refund_due':{new.pk,received.pk},
            'refund_pending':{pending.pk},'refund_failed':{failed.pk},'shipments':{new.pk},'close':{ready.pk},'older':{older.pk}}
        for key, _, _ in action_definitions():
            page = self.client.get(reverse('retours:manage'), {'action':key})
            self.assertEqual({record.pk for record in page.context['records']}, expected[key])

    def test_catalogue_only_manager_cannot_see_return_alerts(self):
        from django.contrib.auth.models import Permission
        user = get_user_model().objects.create_user('catalog-only-dashboard', is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename='manage_catalog',content_type__app_label='core'))
        self.client.force_login(user, backend='connexion.backends.EmailOrUsernameBackend')
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertIsNone(page.context['return_attention'])
        self.assertNotContains(page, 'Retours : les actions à prévoir')
