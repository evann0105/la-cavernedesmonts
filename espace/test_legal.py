from unittest.mock import patch
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.core import mail
from core.models import Product
from stocks.models import Variant
from paiement.models import Order
from paiement.tests import CONFIG, SimpleNamespace
from paiement.stock_checkout import get_session, settle
from espace.legal import sales_ready, profile
from espace.legal_test_helpers import APPROVED_LEGAL_SETTINGS

class LegalPagesTests(TestCase):
    def test_public_documents_and_footer(self):
        for name in ('legal_notice','sales_terms','terms','privacy','cookies','withdrawal','accessibility','review_policy'):
            response=self.client.get(reverse('espace:'+name))
            self.assertEqual(response.status_code,200,name)
            self.assertContains(response,'Élodie FOLOPPE')
            self.assertContains(response,'Préparation de l’ouverture des ventes')
        home=self.client.get('/')
        for name in ('legal_notice','sales_terms','privacy','cookies','withdrawal','accessibility'):
            self.assertContains(home,reverse('espace:'+name))

    def test_old_terms_are_replaced_and_no_fake_information(self):
        response=self.client.get(reverse('espace:terms'))
        for obsolete in ('CIC','14 jours francs','réputés conformes','obligation de moyens','aucun échange ni remboursement'):
            self.assertNotContains(response,obsolete)
        notice=self.client.get(reverse('espace:legal_notice'))
        self.assertContains(notice,'525 114 914')
        self.assertNotContains(notice,'FR55')
        self.assertContains(notice,'à confirmer')

    def test_withdrawal_form_download_and_no_forced_reason(self):
        response=self.client.get(reverse('espace:withdrawal_form'))
        self.assertEqual(response.status_code,200)
        self.assertIn('attachment',response['Content-Disposition'])
        self.assertContains(response,'Signature du/des consommateur(s)')
        self.assertContains(self.client.get('/retours/'),'Télécharger le formulaire type')
        self.assertContains(self.client.get('/accounts/signup/'),reverse('espace:privacy'))

    def test_readiness_is_private(self):
        url=reverse('espace:legal_readiness')
        self.assertEqual(self.client.get(url).status_code,302)
        user=get_user_model().objects.create_user('ordinary',password='password-example-123')
        self.client.force_login(user,backend='connexion.backends.EmailOrUsernameBackend')
        self.assertEqual(self.client.get(url).status_code,403)

    @override_settings(**APPROVED_LEGAL_SETTINGS)
    def test_configuration_needs_all_information_and_approvals(self):
        self.assertTrue(sales_ready())
        with override_settings(SHOP_LEGAL={**profile(),'mediator_name':''}): self.assertFalse(sales_ready())
        with override_settings(SHOP_LEGAL={**profile(),'mediator_name':None}): self.assertFalse(sales_ready())
        with override_settings(SHOP_LEGAL={**profile(),'mediator_url':'javascript:alert(1)'}): self.assertFalse(sales_ready())
        with override_settings(LEGAL_DOCUMENTS_APPROVED=False): self.assertFalse(sales_ready())
        with override_settings(LEGAL_PRODUCT_INFORMATION_READY=False): self.assertFalse(sales_ready())

@override_settings(**CONFIG)
class ContractPaymentTests(TestCase):
    def setUp(self):
        product=Product.objects.create(name='Veste',price=36)
        self.variant=Variant.objects.create(product=product,size='M',color='Bleu',quantity=3)
        self.client.post('/panier/ajouter/'+product.slug+'/',{'variant':self.variant.pk,'quantity':1})

    @override_settings(LEGAL_DOCUMENTS_APPROVED=False)
    @patch('paiement.stock_checkout.stripe.checkout.Session.create')
    def test_incomplete_legal_configuration_never_reserves_or_calls_stripe(self,create):
        self.client.post('/paiement/')
        create.assert_not_called()
        self.assertFalse(Order.objects.exists())
        self.variant.refresh_from_db();self.assertEqual(self.variant.reserved,0)

    @override_settings(EMAIL_TEST_MODE=True)
    @patch('paiement.stock_checkout.stripe.checkout.Session.create')
    def test_test_mailbox_cannot_open_checkout(self,create):
        self.client.post('/paiement/')
        create.assert_not_called()

    @patch('paiement.stock_checkout.stripe.checkout.Session.create')
    @patch('paiement.stock_checkout.stripe.checkout.Session.retrieve')
    def test_stripe_acceptance_snapshot_and_durable_email(self,retrieve,create):
        create.return_value=SimpleNamespace(id='cs_contract',url='https://checkout.stripe.com/test')
        self.client.post('/paiement/')
        order=Order.objects.get()
        self.assertEqual(create.call_args.kwargs['consent_collection'],{'terms_of_service':'required'})
        original=order.terms_snapshot.copy()
        self.assertIn('Formulaire type de rétractation',original['text'])
        self.assertIsNone(order.terms_accepted_at)
        retrieve.return_value=create.return_value
        with override_settings(SHOP_LEGAL={**profile(),'business_name':'Autre identité après commande'}):get_session(order.pk)
        order.refresh_from_db();self.assertEqual(order.terms_snapshot,original)
        self.assertEqual(create.call_count,1)
        session=dict(id='cs_contract',client_reference_id=str(order.pk),metadata={'order_id':str(order.pk)},payment_status='paid',currency='eur',amount_subtotal=3600,amount_total=4200,total_details={'amount_shipping':600,'amount_discount':0},customer_details={'email':'client@example.test'},consent={'terms_of_service':'accepted'})
        with self.captureOnCommitCallbacks(execute=True): self.assertTrue(settle(session))
        order.refresh_from_db();self.assertIsNotNone(order.terms_accepted_at)
        self.assertEqual(len(mail.outbox),1)
        self.assertEqual(mail.outbox[0].attachments[0].content,original['text'])
        self.assertIn('Dont livraison : 6.00 €',mail.outbox[0].body)

    def test_receipt_uses_actual_discounts_by_line(self):
        from retours.mail import order_confirmation
        from retours.models import ReturnMail
        order=Order.objects.create(session_key='receipt',status='paid',customer_email='client@example.test',subtotal=6000,total_paid=5100,return_shipping_amount=600,paid_line_totals=[1500,3000],items=[{'name':'Article soldé','quantity':1,'unit_amount':3000},{'name':'Article plein tarif','quantity':1,'unit_amount':3000}])
        order_confirmation(order)
        text=ReturnMail.objects.get(order=order).payload['details']
        self.assertIn('Remise sur cette ligne : 15.00 € · Total payé pour cette ligne : 15.00 €',text)
        self.assertIn('Remise sur cette ligne : 0.00 € · Total payé pour cette ligne : 30.00 €',text)
        self.assertIn('Total payé : 51.00 €',text)
