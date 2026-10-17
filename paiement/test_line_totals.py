import uuid
from unittest.mock import patch
import stripe
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from core.models import Product
from stocks.models import Variant
from stocks.reservations import reserve, StockUnavailable
from .models import Order
from .stock_checkout import get_session, settle
from .tests import CONFIG, SimpleNamespace
from retours.services import allocation, submit, ReturnError
from retours.models import ReturnRequest, ReturnLine
from retours.refunds import initiate


@override_settings(**{**CONFIG, "EMAIL_DELIVERY_ENABLED":True})
class ExactDiscountTests(TestCase):
    def setUp(self):
        self.items=[]
        for n in range(2):
            product=Product.objects.create(name=f'Produit {n}',price=100)
            variant=Variant.objects.create(product=product,size='M',color='Beige',quantity=10)
            self.items.append(dict(product=product.pk,variant=variant.pk,name=product.name,size='M',color='Beige',quantity=1,unit_amount=10000))
        self.order=reserve('discount-test',None,self.items)
        with patch('paiement.stock_checkout.stripe.checkout.Session.create',return_value=SimpleNamespace(id='cs_discount',url='https://checkout.stripe.com/test')):
            get_session(self.order.pk)
        self.order.refresh_from_db()

    def session(self, totals=(5000,10000), shipping=600):
        return dict(id='cs_discount',payment_status='paid',status='complete',currency='eur',
            amount_subtotal=self.order.subtotal,amount_total=sum(totals)+shipping,
            total_details={'amount_shipping':shipping,'amount_discount':self.order.subtotal-sum(totals)},
            client_reference_id=str(self.order.pk),metadata={'order_id':str(self.order.pk)},
            customer_details={'email':'discount@example.test'},payment_intent='pi_discount')

    def row(self, index, total):
        item=self.order.items[index]
        return dict(id=f'li_{index}',currency='eur',quantity=item['quantity'],
            amount_subtotal=item['unit_amount']*item['quantity'],amount_total=total,
            amount_discount=item['unit_amount']*item['quantity']-total,
            description=item['name'],price={'unit_amount':item['unit_amount'],
                'product':{'metadata':{'order_id':str(self.order.pk),'order_item':str(index)}}})

    def pay(self, totals=(5000,10000), shipping=600):
        rows=[self.row(n,total) for n,total in enumerate(totals)]
        with patch('paiement.line_totals.stripe.checkout.Session.list_line_items',return_value={'data':list(reversed(rows)),'has_more':False}):
            self.assertTrue(settle(self.session(totals,shipping)))
        self.order.refresh_from_db()

    def test_targeted_discount_uses_paid_line_amount_not_global_prorata(self):
        self.pay()
        self.assertEqual(self.order.paid_line_totals,[5000,10000])
        discounted=submit(self.order.pk,{0:1},uuid.uuid4())
        full_price=submit(self.order.pk,{1:1},uuid.uuid4())
        self.assertEqual(discounted.amount,5000)
        self.assertEqual(full_price.lines.get().amount,10000)
        self.assertEqual(full_price.shipping_amount,600)
        self.assertEqual(discounted.amount+full_price.amount,15600)
        self.assertEqual(discounted.amount_breakdown['reductions'],50)
        self.assertEqual(full_price.amount_breakdown['reductions'],0)

    def test_quantity_rounding_across_multiple_returns_keeps_all_cents(self):
        # Existing checkout has immutable quantity 1; use a paid legacy fixture
        # with an exact stored Stripe line total for three units.
        self.order.status='paid'; self.order.items[0]['quantity']=3
        self.order.subtotal=40000;self.order.total_paid=30000
        self.order.return_shipping_amount=0;self.order.paid_line_totals=[20000,10000];self.order.save()
        records=[submit(self.order.pk,{0:1},uuid.uuid4()) for _ in range(3)]
        self.assertEqual([record.amount for record in records],[6666,6667,6667])
        final=submit(self.order.pk,{1:1},uuid.uuid4())
        self.assertEqual(sum(r.amount for r in records)+final.amount,30000)
        with self.assertRaises(ReturnError):submit(self.order.pk,{0:1},uuid.uuid4())

    def test_cent_remainder_and_free_item_do_not_shift_to_other_products(self):
        self.pay((0,9999))
        free=submit(self.order.pk,{0:1},uuid.uuid4())
        paid=submit(self.order.pk,{1:1},uuid.uuid4())
        self.assertEqual(free.amount,0);self.assertEqual(paid.amount,10599)
        with patch('retours.refunds.stripe.Refund.create') as create:
            initiate(free.pk,None)
        create.assert_not_called();free.refresh_from_db();self.assertEqual(free.refund_state,'succeeded')

    def test_all_pages_are_read_and_matched_by_metadata_not_response_order(self):
        pages=[{'data':[self.row(1,10000)],'has_more':True},{'data':[self.row(0,5000)],'has_more':False}]
        with patch('paiement.line_totals.stripe.checkout.Session.list_line_items',side_effect=pages) as listing:
            self.assertTrue(settle(self.session()))
        self.assertEqual(listing.call_args_list[1].kwargs['starting_after'],'li_1')
        self.order.refresh_from_db();self.assertEqual(self.order.paid_line_totals,[5000,10000])

    def test_incomplete_mismatched_or_duplicate_detail_keeps_payment_retryable(self):
        invalid=[
            [self.row(0,5000)],
            [self.row(0,5000),self.row(0,5000)],
            [self.row(0,5000),self.row(1,9999)],
            [dict(self.row(0,5000),quantity=2),self.row(1,10000)],
        ]
        wrong=self.row(1,10000);wrong['price']['product']['metadata']['order_id']=str(uuid.uuid4())
        invalid.append([self.row(0,5000),wrong])
        for rows in invalid:
            with self.subTest(rows=rows),patch('paiement.line_totals.stripe.checkout.Session.list_line_items',return_value={'data':rows,'has_more':False}):
                with self.assertRaises(StockUnavailable):settle(self.session())
            self.order.refresh_from_db()
            self.assertEqual(self.order.status,'pending');self.assertIsNone(self.order.paid_line_totals)
            self.assertTrue(self.order.reservations.filter(state='held').exists())

    def test_stripe_timeout_retries_without_selling_stock_twice(self):
        with patch('paiement.line_totals.stripe.checkout.Session.list_line_items',side_effect=stripe.APIConnectionError('timeout')):
            with self.assertRaises(stripe.APIConnectionError):settle(self.session())
        self.order.refresh_from_db();self.assertEqual(self.order.status,'pending')
        self.pay()
        with patch('paiement.line_totals.stripe.checkout.Session.list_line_items') as listing:
            self.assertTrue(settle(self.session()))
        listing.assert_not_called()
        variant=Variant.objects.get(pk=self.items[0]['variant'])
        self.assertEqual(variant.quantity,9);self.assertEqual(variant.reserved,0)

    def test_old_discounted_order_recovers_exact_totals_without_rewriting_history(self):
        self.order.status='paid';self.order.total_paid=15600;self.order.return_shipping_amount=600
        self.order.checkout_payload={};self.order.save()
        rows=[self.row(0,5000),self.row(1,10000)]
        for n,row in enumerate(rows):
            row['price']['product']={'metadata':{}}
            row['description']=f"{self.items[n]['name']} — M · Beige"
        with patch('paiement.line_totals.stripe.checkout.Session.retrieve',return_value=self.session()),patch('paiement.line_totals.stripe.checkout.Session.list_line_items',return_value={'data':rows,'has_more':False}):
            record=submit(self.order.pk,{0:1},uuid.uuid4())
        self.assertEqual(record.amount,5000)
        self.order.refresh_from_db();self.assertEqual(self.order.paid_line_totals,[5000,10000])

    def test_missing_legacy_detail_blocks_approximate_refund(self):
        self.order.status='paid';self.order.total_paid=15600;self.order.return_shipping_amount=600;self.order.save()
        with patch('paiement.line_totals.stripe.checkout.Session.retrieve',side_effect=stripe.APIConnectionError('timeout')):
            with self.assertRaisesMessage(ReturnError,'détail exact des remises'):
                submit(self.order.pk,{0:1},uuid.uuid4())
        self.assertFalse(self.order.returns.exists())

    def test_historical_incorrect_declaration_cannot_start_a_refund(self):
        self.pay()
        old=ReturnRequest.objects.create(order=self.order,submission_id=uuid.uuid4(),amount=7500)
        ReturnLine.objects.create(request=old,item_index=0,name='Produit 0',size='M',color='Beige',quantity=1,unit_amount=10000,amount=7500)
        with patch('retours.refunds.stripe.Refund.create') as create:
            with self.assertRaisesMessage(ReturnError,'ne correspond pas aux remises'):
                initiate(old.pk,None)
        create.assert_not_called();old.refresh_from_db()
        self.assertEqual(old.amount,7500);self.assertEqual(old.refund_state,'none')

    def test_exact_discount_is_the_amount_sent_to_stripe(self):
        self.pay()
        record=submit(self.order.pk,{0:1},uuid.uuid4())
        refund={'id':'re_exact','amount':5000,'currency':'eur','payment_intent':'pi_discount',
            'metadata':{'return_id':str(record.pk)},'status':'succeeded'}
        with patch('retours.refunds.stripe.checkout.Session.retrieve',return_value=self.session()),patch('retours.refunds.stripe.Refund.list',return_value={'data':[],'has_more':False}),patch('retours.refunds.stripe.Refund.create',return_value=refund) as create:
            initiate(record.pk,None)
        self.assertEqual(create.call_args.kwargs['amount'],5000)
        self.assertEqual(create.call_args.kwargs['idempotency_key'],f'return-{record.pk}')

    def test_fully_discounted_checkout_is_confirmed_without_a_bank_charge(self):
        session=self.session((0,0),shipping=0)
        session['payment_status']='no_payment_required'
        with patch('paiement.line_totals.stripe.checkout.Session.list_line_items',return_value={
            'data':[self.row(0,0),self.row(1,0)],'has_more':False}):
            self.assertTrue(settle(session))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status,'paid');self.assertEqual(self.order.total_paid,0)
        self.assertEqual(self.order.paid_line_totals,[0,0])
        record=submit(self.order.pk,{0:1},uuid.uuid4())
        self.assertEqual(record.amount,0)
        with patch('retours.refunds.stripe.Refund.create') as create:
            initiate(record.pk,None)
        create.assert_not_called()
        record.refresh_from_db();self.assertEqual(record.refund_state,'succeeded')
        from retours.models import ReturnMail
        self.assertTrue(ReturnMail.objects.filter(key=f'refund-{record.pk}').exists())

    def test_no_payment_required_is_not_accepted_for_nonzero_or_incomplete_checkout(self):
        for changes in ({'amount_total':1}, {'status':'open'}):
            session=self.session((0,0),shipping=0)
            session.update(payment_status='no_payment_required',**changes)
            settle(session)
            self.order.refresh_from_db();self.assertEqual(self.order.status,'pending')
