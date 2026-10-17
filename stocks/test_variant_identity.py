import uuid
from copy import deepcopy
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.admin.models import LogEntry
from django.test import TestCase, override_settings, RequestFactory
from django.urls import reverse
from core.cart import lines
from core.models import Product
from facturation.delivery import build_delivery_slip
from facturation.profile import capture_profile
from facturation.services import build_preview, issue
from facturation.test_services import LEGAL, INVOICING
from paiement.models import Order
from paiement.stock_checkout import settle
from retours.services import submit, complete, ReturnError
from .models import Variant, Reservation, VariantLabelCorrection
from .forms import VariantForm
from .reservations import reserve


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True, EMAIL_DELIVERY_ENABLED=False,
                   SHOP_LEGAL=LEGAL, SHOP_INVOICING=INVOICING)
class VariantIdentityTests(TestCase):
    def setUp(self):
        self.admin=get_user_model().objects.create_superuser('variant-admin','variant@example.test','test-password')
        self.client.force_login(self.admin)
        self.product=Product.objects.create(name='Polaire',price=36)
        self.variant=Variant.objects.create(product=self.product,size='M',color='Beige',quantity=5)
        self.url=reverse('stocks:edit',args=[self.product.pk,self.variant.pk])

    def correct(self, color, size='M'):
        self.variant.refresh_from_db()
        return self.client.post(self.url,{'size':size,'color':color,'quantity':self.variant.quantity,
            'reference':self.variant.reference,'revision':self.variant.revision})

    def pending_order(self):
        item=dict(product=self.product.pk,variant=self.variant.pk,name='Polaire',size='M',
            color='Beige',quantity=2,unit_amount=3600)
        order=reserve(uuid.uuid4().hex,None,[item])
        order.checkout_payload={'line_items':[{'name':'Polaire M Beige'}]}
        order.invoice_profile=capture_profile()
        address={'name':'Client Test','address':{'line1':'10 rue du Test','city':'Lyon','postal_code':'69001','country':'FR'}}
        order.billing_details=address
        order.shipping_details=address
        order.save()
        return order

    def payment(self, order):
        return dict(id='cs_'+str(order.pk),metadata={'order_id':str(order.pk)},
            client_reference_id=str(order.pk),currency='eur',amount_subtotal=7200,
            amount_total=7200,payment_status='paid',status='complete',
            total_details={'amount_shipping':0,'amount_discount':0},
            customer_details={'email':'client@example.test','name':'Client Test',
                'address':{'line1':'10 rue du Test','city':'Lyon','postal_code':'69001','country':'FR'}})

    def paid_order(self):
        order=self.pending_order()
        self.assertTrue(settle(self.payment(order)))
        order.refresh_from_db()
        self.variant.refresh_from_db()
        return order

    def test_unused_variant_can_be_corrected_without_recreating_stock(self):
        self.assertEqual(self.correct('Rouge').status_code,302)
        self.variant.refresh_from_db()
        self.assertEqual((self.variant.color,self.variant.quantity,self.variant.revision),('Rouge',5,1))
        self.assertEqual(Variant.objects.filter(product=self.product).count(),1)
        self.assertIn('Couleur : Beige → Rouge',LogEntry.objects.first().change_message)

    def test_duplicate_color_and_stale_edit_leave_variant_unchanged(self):
        Variant.objects.create(product=self.product,size='M',color='Rouge',quantity=2)
        self.assertContains(self.correct('rouge'),'existent déjà')
        self.variant.revision=1
        self.variant.save(update_fields=['revision'])
        data={'size':'M','color':'Noir','quantity':5,'reference':'','revision':0}
        self.assertContains(self.client.post(self.url,data),'autre onglet')
        self.variant.refresh_from_db()
        self.assertEqual((self.variant.color,self.variant.quantity),('Beige',5))
        self.assertFalse(VariantLabelCorrection.objects.exists())

    def test_color_after_purchase_preserves_order_invoice_and_printable_documents(self):
        order=self.paid_order()
        invoice=issue(order.pk,self.admin)
        original_items=deepcopy(order.items)
        original_payload=deepcopy(order.checkout_payload)
        original_invoice=deepcopy(invoice.snapshot)
        self.assertEqual(self.correct('Rouge').status_code,302)
        self.variant.refresh_from_db(); order.refresh_from_db(); invoice.refresh_from_db()
        self.assertEqual((self.variant.color,self.variant.quantity,self.variant.reserved),('Rouge',3,0))
        self.assertEqual(Variant.objects.filter(product=self.product).count(),1)
        self.assertEqual(order.items,original_items)
        self.assertEqual(order.checkout_payload,original_payload)
        self.assertEqual(invoice.snapshot,original_invoice)
        self.assertEqual(build_preview(order)['lines'][0]['color'],'Beige')
        self.assertEqual(build_delivery_slip(order)['lines'][0]['color'],'Beige')
        # A new purchase uses the corrected label on the same stock identity.
        newer=reserve('new-buyer',None,[{**order.items[0],'color':'Rouge','quantity':1}])
        self.assertEqual(newer.items[0]['color'],'Rouge')
        self.assertEqual(newer.items[0]['variant'],self.variant.pk)

    def test_released_reservation_does_not_permanently_block_corrections(self):
        order=self.pending_order()
        expired=self.payment(order)
        expired.update(status='expired',payment_status='unpaid')
        self.assertTrue(settle(expired))
        self.assertEqual(self.correct('Noir').status_code,302)
        self.variant.refresh_from_db(); order.refresh_from_db()
        self.assertEqual((self.variant.color,self.variant.quantity,self.variant.reserved),('Noir',5,0))
        self.assertEqual(order.reservations.get().state,'released')
        self.assertEqual(order.items[0]['color'],'Beige')

    def test_active_payment_blocks_correction_then_paid_order_allows_it(self):
        order=self.pending_order()
        original_payload=deepcopy(order.checkout_payload)
        self.assertContains(self.correct('Rouge'),'Un paiement est en cours')
        self.assertFalse(VariantLabelCorrection.objects.exists())
        self.assertTrue(settle(self.payment(order)))
        self.assertEqual(self.correct('Rouge').status_code,302)
        self.variant.refresh_from_db(); order.refresh_from_db()
        self.assertEqual((self.variant.quantity,self.variant.reserved,self.variant.color),(3,0,'Rouge'))
        self.assertEqual(order.checkout_payload,original_payload)
        self.assertEqual(order.items[0]['color'],'Beige')

    def test_reservation_added_after_validation_prevents_a_stale_correction(self):
        original=VariantForm.is_valid
        def validate_then_reserve(form):
            valid=original(form)
            if valid:self.pending_order()
            return valid
        with patch.object(VariantForm,'is_valid',validate_then_reserve):
            response=self.correct('Noir')
        self.assertContains(response,'autre onglet')
        self.variant.refresh_from_db()
        self.assertEqual((self.variant.color,self.variant.quantity,self.variant.reserved),('Beige',5,2))
        self.assertFalse(VariantLabelCorrection.objects.exists())

    def test_existing_and_later_returns_keep_purchase_label_and_restock_corrected_variant(self):
        order=self.paid_order()
        before=submit(order.pk,{0:1},uuid.uuid4())
        # Exercise historical JSON identifiers as well as current integer IDs.
        order.items[0]['variant']=str(self.variant.pk)
        order.save(update_fields=['items'])
        self.assertEqual(self.correct('Rouge').status_code,302)
        after=submit(order.pk,{0:1},uuid.uuid4())
        self.assertEqual(self.correct('Bleu',size='L').status_code,302)
        for record in (before,after):
            line=record.lines.get()
            self.assertEqual((line.size,line.color),('M','Beige'))
            record.refund_state='succeeded'; record.save(update_fields=['refund_state'])
            complete(record.pk,self.admin,{line.pk:1},{line.pk:1})
            complete(record.pk,self.admin,{line.pk:1},{line.pk:1})
            line.refresh_from_db()
            self.assertEqual((line.size,line.color,line.restocked),('M','Beige',1))
        self.variant.refresh_from_db(); order.refresh_from_db()
        self.assertEqual((self.variant.size,self.variant.color,self.variant.quantity),('L','Bleu',5))
        self.assertEqual(order.items[0]['color'],'Beige')
        self.assertEqual(self.variant.label_corrections.count(),2)

    def test_failed_audit_rolls_back_label_and_correction_history_together(self):
        with patch.object(LogEntry.objects,'log_actions',side_effect=RuntimeError('audit unavailable')):
            with self.assertRaisesMessage(RuntimeError,'audit unavailable'):
                self.correct('Rouge')
        self.variant.refresh_from_db()
        self.assertEqual((self.variant.color,self.variant.quantity,self.variant.revision),('Beige',5,0))
        self.assertFalse(VariantLabelCorrection.objects.exists())

    def test_color_changed_after_closure_review_requires_a_new_review(self):
        from retours.closure import prepare, unpack
        from retours.forms import CompleteForm
        order=self.paid_order()
        record=submit(order.pk,{0:1},uuid.uuid4()); line=record.lines.get()
        record.refund_state='succeeded'; record.save(update_fields=['refund_state'])
        self.assertEqual(self.correct('Rouge').status_code,302)
        form=CompleteForm(record,{f'received_{line.pk}':1,f'restock_{line.pk}':1,'confirm':'on'})
        self.assertTrue(form.is_valid(),form.errors)
        review=prepare(record,self.admin,form)
        self.assertEqual(review['rows'][0]['current_stock_label'],'M · Rouge')
        received,restock,expected=unpack(review['token'],record,self.admin)
        self.assertEqual(self.correct('Bleu').status_code,302)
        with self.assertRaisesMessage(ReturnError,'Le dossier a changé depuis le récapitulatif'):
            complete(record.pk,self.admin,received,restock,expected_snapshot=expected)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.quantity,3)
        review=prepare(record,self.admin,form)
        self.assertEqual(review['rows'][0]['current_stock_label'],'M · Bleu')
        received,restock,expected=unpack(review['token'],record,self.admin)
        complete(record.pk,self.admin,received,restock,expected_snapshot=expected)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.quantity,4)

    def test_stock_quantity_changed_after_review_does_not_require_relabel_review(self):
        from retours.closure import snapshot
        order=self.paid_order()
        record=submit(order.pk,{0:1},uuid.uuid4()); line=record.lines.get()
        record.refund_state='succeeded'; record.save(update_fields=['refund_state'])
        self.assertEqual(self.correct('Rouge').status_code,302)
        expected=snapshot(record)
        Variant.objects.filter(pk=self.variant.pk).update(quantity=4,revision=10)
        complete(record.pk,self.admin,{line.pk:1},{line.pk:1},expected_snapshot=expected)
        self.variant.refresh_from_db()
        self.assertEqual((self.variant.quantity,self.variant.color),(5,'Rouge'))

    def test_old_cart_identity_is_preserved_and_cannot_pay_if_variant_changed(self):
        request=RequestFactory().get('/panier/')
        request.session={'cart':{f'variant:{self.variant.pk}':{'product':self.product.pk,'variant':self.variant.pk,'size':'M','color':'Beige','quantity':1}}}
        self.assertEqual(self.correct('Rouge').status_code,302)
        item=lines(request)[0]
        self.assertEqual(item['color'],'Beige'); self.assertIsNone(item['variant'])
        self.assertFalse(item['stock_ok'])

    def test_untraced_rename_or_product_move_still_cannot_restock_an_old_return(self):
        order=self.paid_order()
        record=submit(order.pk,{0:1},uuid.uuid4()); line=record.lines.get()
        record.refund_state='succeeded'; record.save(update_fields=['refund_state'])
        self.assertEqual(self.correct('Rouge').status_code,302)
        Variant.objects.filter(pk=self.variant.pk).update(color='Noir')
        with self.assertRaisesMessage(ReturnError,'Cette variante a changé'):
            complete(record.pk,self.admin,{line.pk:1},{line.pk:1})
        Variant.objects.filter(pk=self.variant.pk).update(color='Rouge',product=Product.objects.create(name='Autre',price=36))
        with self.assertRaisesMessage(ReturnError,'Cette variante a changé'):
            complete(record.pk,self.admin,{line.pk:1},{line.pk:1})
        self.variant.refresh_from_db(); record.refresh_from_db()
        self.assertEqual(self.variant.quantity,3)
        self.assertNotEqual(record.state,'completed')
