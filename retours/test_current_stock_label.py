import uuid
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from core.models import Product
from paiement.models import Order
from stocks.models import Variant
from .models import ReturnLine, ReturnRequest


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True, EMAIL_DELIVERY_ENABLED=False)
class ReturnStockLabelDisplayTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_superuser('label-manager', email='manager@example.test', password='test-only')
        self.owner = get_user_model().objects.create_user('label-owner', email='owner@example.test')
        self.product = Product.objects.create(name='Polaire', price=36)
        self.variant = Variant.objects.create(product=self.product, size='M', color='Beige', quantity=5)
        self.order = Order.objects.create(user=self.owner, status='paid', session_key='return-label',
            customer_email=self.owner.email, subtotal=3600, total_paid=3600,
            return_shipping_amount=0, paid_line_totals=[3600],
            items=[{'product':self.product.pk, 'variant':self.variant.pk, 'name':'Polaire',
                    'size':'M', 'color':'Beige', 'quantity':1, 'unit_amount':3600}])
        self.record = ReturnRequest.objects.create(order=self.order, submission_id=uuid.uuid4(), amount=3600, refund_state='succeeded')
        self.line = ReturnLine.objects.create(request=self.record, item_index=0, variant=self.variant,
            name='Polaire', size='M', color='Beige', quantity=1, amount=3600, unit_amount=3600)

    def test_corrected_stock_label_is_separate_from_purchase_snapshots(self):
        self.assertEqual(self.line.current_stock_label, '')
        self.variant.color = 'Rouge'
        self.variant.save(update_fields=['color'])
        self.assertEqual(self.line.current_stock_label, 'M · Rouge')
        self.line.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.line.color, 'Beige')
        self.assertEqual(self.order.items[0]['color'], 'Beige')

    def test_missing_or_foreign_variant_never_proposes_a_stock_destination(self):
        self.line.variant = None
        self.assertEqual(self.line.current_stock_label, '')
        self.line.variant_id = self.variant.pk + 10000
        self.assertEqual(self.line.current_stock_label, '')
        foreign_product = Product.objects.create(name='Autre article', price=36)
        foreign = Variant.objects.create(product=foreign_product, size='M', color='Rouge', quantity=1)
        self.line.variant = foreign
        self.order.items[0]['variant'] = foreign.pk
        self.assertEqual(self.line.current_stock_label, '')
        self.order.items = []
        self.assertEqual(self.line.current_stock_label, '')

    def test_management_and_closure_show_current_label_but_client_keeps_purchase_label(self):
        self.variant.color = 'Rouge'
        self.variant.save(update_fields=['color'])
        self.client.force_login(self.manager)
        url = reverse('retours:manage_detail', args=[self.record.pk])
        response = self.client.get(url)
        self.assertContains(response, 'Libellé actuel du stock : M · Rouge')
        self.assertContains(response, 'M Beige')
        review = self.client.post(url, {'action':'review_complete',
            f'received_{self.line.pk}':1, f'restock_{self.line.pk}':1, 'confirm':'on'})
        self.assertContains(review, 'Libellé actuel du stock : M · Rouge')
        self.assertContains(review, 'M Beige')
        self.client.force_login(self.owner)
        client_page = self.client.get(reverse('retours:order', args=[self.order.pk]))
        self.assertContains(client_page, 'M Beige')
        self.assertNotContains(client_page, 'Libellé actuel du stock')
        self.assertNotContains(client_page, 'Rouge')
