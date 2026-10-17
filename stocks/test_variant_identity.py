import uuid
from django.contrib.auth import get_user_model
from django.contrib.admin.models import LogEntry
from django.test import TestCase, override_settings, RequestFactory
from django.urls import reverse
from unittest.mock import patch
from core.cart import lines
from core.models import Product
from paiement.models import Order
from retours.services import submit, complete, ReturnError
from .models import Variant, Reservation
from .forms import VariantForm
from retours.models import ReturnRequest, ReturnLine


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True, EMAIL_DELIVERY_ENABLED=False)
class VariantIdentityTests(TestCase):
    def setUp(self):
        self.admin=get_user_model().objects.create_superuser('variant-admin','variant@example.test','test-password')
        self.client.force_login(self.admin)
        self.product=Product.objects.create(name='Polaire',price=36)
        self.variant=Variant.objects.create(product=self.product,size='M',color='Beige',quantity=5)

    def test_unused_variant_can_be_corrected_without_recreating_stock(self):
        url=reverse('stocks:edit',args=[self.product.pk,self.variant.pk])
        data={'size':'M','color':'Rouge','quantity':5,'reference':'','revision':0}
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.variant.refresh_from_db()
        self.assertEqual((self.variant.color,self.variant.quantity,self.variant.revision),('Rouge',5,1))
        self.assertEqual(Variant.objects.filter(product=self.product).count(),1)
        self.assertIn('Couleur : Beige → Rouge',LogEntry.objects.first().change_message)
        data.update(color='Rouge',quantity=7,revision=self.variant.revision)
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.variant.refresh_from_db(); self.assertEqual(self.variant.quantity,7)

    def test_duplicate_color_and_stale_edit_leave_variant_unchanged(self):
        url=reverse('stocks:edit',args=[self.product.pk,self.variant.pk])
        Variant.objects.create(product=self.product,size='M',color='Rouge',quantity=2)
        data={'size':'M','color':'rouge','quantity':5,'reference':'','revision':0}
        self.assertContains(self.client.post(url,data),'existent déjà')
        self.variant.revision=1
        self.variant.save(update_fields=['revision'])
        data['color']='Noir'
        self.assertContains(self.client.post(url,data),'autre onglet')
        self.variant.refresh_from_db()
        self.assertEqual((self.variant.color,self.variant.quantity),('Beige',5))

    def test_used_variants_keep_identity_for_every_reservation_state(self):
        url=reverse('stocks:edit',args=[self.product.pk,self.variant.pk])
        order=Order.objects.create(session_key='identity-history',items=[],subtotal=0)
        for state in ('held','sold','released'):
            with self.subTest(state=state):
                reservation=Reservation.objects.create(order=order,variant=self.variant,quantity=1,state=state)
                response=self.client.post(url,{'size':'M','color':'Noir','quantity':5,'reference':'','revision':0})
                self.assertContains(response,'préserver cet historique')
                reservation.delete()
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.color,'Beige')

    def test_legacy_order_json_and_return_reference_each_protect_the_identity(self):
        url=reverse('stocks:edit',args=[self.product.pk,self.variant.pk])
        data={'size':'M','color':'Noir','quantity':5,'reference':'','revision':0}
        order=Order.objects.create(session_key='legacy-label',items=[{'variant':str(self.variant.pk)}],subtotal=0)
        self.assertContains(self.client.post(url,data),'préserver cet historique')
        order.items=[]
        order.save(update_fields=['items'])
        record=ReturnRequest.objects.create(order=order,submission_id=uuid.uuid4(),amount=0)
        ReturnLine.objects.create(request=record,item_index=0,variant=self.variant,name='Polaire',size='M',color='Beige',quantity=1,amount=0)
        self.assertContains(self.client.post(url,data),'préserver cet historique')
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.color,'Beige')

    def test_usage_added_after_form_validation_is_rechecked_before_save(self):
        original=VariantForm.is_valid
        def validate_then_add_order(form):
            valid=original(form)
            if valid:
                Order.objects.create(session_key='race-label',items=[{'variant':self.variant.pk}],subtotal=0)
            return valid
        with patch.object(VariantForm,'is_valid',validate_then_add_order):
            response=self.client.post(reverse('stocks:edit',args=[self.product.pk,self.variant.pk]),
                {'size':'M','color':'Noir','quantity':5,'reference':'','revision':0})
        self.assertContains(response,'préserver cet historique')
        self.variant.refresh_from_db()
        self.assertEqual((self.variant.color,self.variant.quantity),('Beige',5))

    def test_old_cart_identity_is_preserved_and_cannot_pay_if_variant_changed(self):
        request=RequestFactory().get('/panier/')
        request.session={'cart':{f'variant:{self.variant.pk}':{'product':self.product.pk,'variant':self.variant.pk,'size':'M','color':'Beige','quantity':1}}}
        self.client.post(reverse('stocks:edit',args=[self.product.pk,self.variant.pk]),
            {'size':'M','color':'Rouge','quantity':5,'reference':'','revision':0})
        item=lines(request)[0]
        self.assertEqual(item['color'],'Beige'); self.assertIsNone(item['variant'])
        self.assertFalse(item['stock_ok'])

    def test_legacy_renamed_variant_cannot_receive_an_old_return(self):
        order=Order.objects.create(status='paid',session_key='identity',items=[dict(product=self.product.pk,variant=self.variant.pk,name='Polaire',size='M',color='Beige',quantity=1,unit_amount=3600)],subtotal=3600,total_paid=3600,return_shipping_amount=0)
        record=submit(order.pk,{0:1},uuid.uuid4()); line=record.lines.get()
        record.refund_state='succeeded';record.save()
        Variant.objects.filter(pk=self.variant.pk).update(color='Rouge')
        with self.assertRaisesMessage(ReturnError,'Cette variante a changé'):
            complete(record.pk,self.admin,{line.pk:1},{line.pk:1})
        self.variant.refresh_from_db();record.refresh_from_db()
        self.assertEqual(self.variant.quantity,5); self.assertNotEqual(record.state,'completed')
