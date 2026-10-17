from copy import deepcopy
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from paiement.models import Order
from paiement.stock_checkout import settle
from retours.access import order_token
from .models import Invoice, InvoiceSequence
from .profile import capture_profile
from .test_services import LEGAL, INVOICING


@override_settings(SHOP_LEGAL=LEGAL, SHOP_INVOICING=INVOICING, DEBUG=True,
                   LOCAL_SKIP_EMAIL_VERIFICATION=True, EMAIL_DELIVERY_ENABLED=False)
class PrintableDocumentTests(TestCase):
    def setUp(self):
        from django.utils import timezone
        users = get_user_model()
        self.owner = users.objects.create_user('document-owner', email='client@example.test')
        self.other = users.objects.create_user('other-document-owner', email='autre@example.test')
        self.manager = users.objects.create_superuser('document-manager', email='gestion@example.test', password='test-only')
        self.billing = {'name':'Client Exemple', 'address':{'line1':'12 rue du Test','postal_code':'69001','city':'Lyon','country':'FR'}}
        self.order = Order.objects.create(user=self.owner, status='paid', paid_at=timezone.now(),
            session_key='private-test-session', customer_email=self.owner.email,
            items=[{'name':'Polaire commandée', 'quantity':2, 'unit_amount':3000},
                   {'name':'Écharpe commandée', 'quantity':1,'unit_amount':2000}],
            subtotal=8000, paid_line_totals=[3000,2000], total_paid=5600,
            return_shipping_amount=600, billing_details=self.billing,
            shipping_details=deepcopy(self.billing), invoice_profile=capture_profile())

    def url(self, kind):
        return reverse('facturation:'+kind, args=[self.order.pk])

    def test_anonymous_and_another_account_cannot_read_documents_even_with_same_email(self):
        self.order.customer_email = self.other.email
        self.order.save(update_fields=['customer_email'])
        for kind in ('purchase_order','invoice','delivery_slip'):
            self.assertEqual(self.client.get(self.url(kind)).status_code,404)
        self.client.force_login(self.other)
        for kind in ('purchase_order','invoice','delivery_slip'):
            self.assertEqual(self.client.get(self.url(kind)).status_code,404)

    def test_owner_can_print_without_issuing_an_invoice_on_get(self):
        self.client.force_login(self.owner)
        response = self.client.get(self.url('purchase_order'))
        self.assertContains(response,'Bon de commande')
        self.assertContains(response,'Client Exemple')
        self.assertContains(response,'Polaire commandée')
        self.assertContains(response,'56,00')
        self.assertIn('private',response['Cache-Control'])
        self.assertIn('no-store',response['Cache-Control'])
        self.assertEqual(response['Referrer-Policy'],'no-referrer')
        preview = self.client.get(self.url('invoice'))
        self.assertContains(preview,'APERÇU — DOCUMENT NON COMPTABLE')
        self.assertNotContains(preview,'Émettre la facture définitive')
        self.assertFalse(Invoice.objects.exists())
        self.assertFalse(InvoiceSequence.objects.exists())
        self.assertContains(self.client.get(reverse('espace:order',args=[self.order.pk])),self.url('purchase_order'))

    def test_guest_signed_access_and_original_session_are_supported_without_email_lookup(self):
        signed = self.url('purchase_order')+'?token='+order_token(self.order)
        response = self.client.get(signed)
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'?token=')
        self.assertEqual(self.client.get(self.url('invoice')+'?token=invalid').status_code,404)
        session = self.client.session
        session.save()
        self.order.session_key=session.session_key
        self.order.save(update_fields=['session_key'])
        self.assertEqual(self.client.get(self.url('invoice')).status_code,200)

    def test_staff_confirmation_required_and_reprinting_preserves_snapshot(self):
        self.client.force_login(self.manager)
        issue_url=self.url('issue')
        self.assertEqual(self.client.get(issue_url).status_code,405)
        self.client.post(issue_url,{})
        self.assertFalse(Invoice.objects.exists())
        self.client.post(issue_url,{'confirm_issue':'yes'})
        invoice=Invoice.objects.get(order=self.order)
        self.order.items[0]['name']='Nom modifié après émission'
        self.order.save(update_fields=['items'])
        self.client.post(issue_url,{'confirm_issue':'yes'})
        response=self.client.get(self.url('invoice'))
        self.assertContains(response,invoice.number)
        self.assertContains(response,'Polaire commandée')
        self.assertNotContains(response,'Nom modifié après émission')
        self.assertNotContains(response,'APERÇU — DOCUMENT NON COMPTABLE')
        self.assertEqual(Invoice.objects.count(),1)

    def test_missing_fiscal_history_and_simulations_remain_non_accounting_previews(self):
        self.client.force_login(self.manager)
        self.order.invoice_profile={}
        self.order.status='demo'
        self.order.save(update_fields=['invoice_profile','status'])
        self.client.post(self.url('issue'),{'confirm_issue':'yes'})
        response=self.client.get(self.url('invoice'))
        self.assertContains(response,'SIMULATION — aucun paiement réel')
        self.assertContains(response,'profil fiscal')
        self.assertFalse(Invoice.objects.exists())

    def test_customer_cannot_issue_and_csrf_is_required_for_management(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(self.url('issue'),{'confirm_issue':'yes'}).status_code,403)
        manager_client=Client(enforce_csrf_checks=True)
        manager_client.force_login(self.manager)
        self.assertEqual(manager_client.post(self.url('issue'),{'confirm_issue':'yes'}).status_code,403)
        self.assertFalse(Invoice.objects.exists())

    def test_management_documents_are_limited_to_authorised_managers(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse('facturation:management')).status_code,403)
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(reverse('facturation:management')+'?q='+str(self.order.pk)[:8]),'Commandes et factures')

    def test_delivery_slip_uses_shipping_and_ordered_quantities_without_prices_or_fiscal_setup(self):
        self.order.invoice_profile = {}
        self.order.billing_details = {'name':'Facturation privée différente'}
        self.order.items[0].update(size='M', color='Rouge')
        self.order.save(update_fields=['invoice_profile','billing_details','items'])
        self.client.force_login(self.owner)
        response = self.client.get(self.url('delivery_slip'))
        self.assertContains(response, 'Bon de livraison')
        self.assertContains(response, 'Client Exemple')
        self.assertContains(response, '12 rue du Test')
        self.assertContains(response, 'Polaire commandée')
        self.assertContains(response, 'Rouge')
        self.assertContains(response, '3 articles')
        self.assertNotContains(response, 'Facturation privée différente')
        self.assertNotContains(response, self.owner.email)
        self.assertNotContains(response, '56,00')
        self.assertNotContains(response, 'Prix unitaire')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')
        self.assertFalse(Invoice.objects.exists())
        self.assertFalse(InvoiceSequence.objects.exists())
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'paid')

    def test_delivery_slip_manager_guest_access_and_document_links(self):
        signed = self.url('delivery_slip')+'?token='+order_token(self.order)
        self.assertContains(self.client.get(signed), 'Bon de livraison')
        self.assertEqual(self.client.get(self.url('delivery_slip')+'?token=invalid').status_code, 404)
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(self.url('delivery_slip')), 'Bon de livraison')
        self.assertContains(self.client.get(self.url('purchase_order')), self.url('delivery_slip'))
        self.assertContains(self.client.get(reverse('facturation:management')), self.url('delivery_slip'))

    def test_delivery_slip_missing_shipping_is_explicit_and_demo_is_not_an_actual_dispatch(self):
        self.order.shipping_details = {}
        self.order.status = 'demo'
        self.order.save(update_fields=['shipping_details','status'])
        self.client.force_login(self.owner)
        response = self.client.get(self.url('delivery_slip'))
        self.assertContains(response, 'Adresse de livraison incomplète')
        self.assertContains(response, 'aucun envoi réel')
        self.assertNotContains(response, '12 rue du Test')

    def test_unpaid_and_expired_orders_have_no_delivery_slip(self):
        self.client.force_login(self.owner)
        for status in ('pending', 'expired'):
            with self.subTest(status=status):
                self.order.status = status
                self.order.save(update_fields=['status'])
                self.assertEqual(self.client.get(self.url('delivery_slip')).status_code, 404)

    def test_incomplete_order_lines_are_not_silently_presented_as_a_complete_packing_list(self):
        self.order.items.append({'name':'Ligne invalide', 'quantity':-1})
        self.order.save(update_fields=['items'])
        self.client.force_login(self.owner)
        response = self.client.get(self.url('delivery_slip'))
        self.assertContains(response, 'Le détail des articles est incomplet')
        self.assertContains(response, 'Total à vérifier')
        self.assertNotContains(response, 'Ligne invalide')

    def test_documents_keep_distinct_roles_even_for_a_demo_with_no_fiscal_history(self):
        self.order.status='demo'
        self.order.invoice_profile={}
        self.order.save(update_fields=['status','invoice_profile'])
        self.client.force_login(self.owner)
        purchase=self.client.get(self.url('purchase_order'))
        invoice=self.client.get(self.url('invoice'))
        delivery=self.client.get(self.url('delivery_slip'))
        self.assertContains(purchase, 'Le récapitulatif de votre achat')
        self.assertContains(purchase, 'Articles commandés')
        self.assertNotContains(purchase, 'Informations de facturation')
        self.assertContains(invoice, 'La préparation de votre facture')
        self.assertContains(invoice, 'Non attribué — aperçu')
        self.assertContains(invoice, 'Sans valeur comptable')
        self.assertContains(invoice, 'TVA — régime à confirmer')
        self.assertNotContains(invoice, 'TVA 20.00 %')
        self.assertContains(delivery, 'Le contenu à joindre au colis')
        self.assertContains(delivery, 'Destinataire du colis')
        self.assertContains(delivery, 'À cocher après contrôle')
        self.assertNotContains(delivery, '56,00')
        self.assertFalse(Invoice.objects.exists())
        self.assertFalse(InvoiceSequence.objects.exists())

    def test_valid_profile_preview_is_unissued_without_falsely_claiming_unknown_tax(self):
        self.client.force_login(self.owner)
        response=self.client.get(self.url('invoice'))
        self.assertContains(response, 'Facture définitive non encore émise')
        self.assertContains(response, 'Total HT')
        self.assertContains(response, 'TVA 20.00 %')
        self.assertNotContains(response, 'Le régime fiscal n’étant pas confirmé')
        self.assertContains(response, 'Non attribué — aperçu')
        self.assertFalse(Invoice.objects.exists())

    @patch('paiement.stock_checkout.finish_locked')
    def test_stripe_billing_is_captured_without_automatic_fiscal_emission(self, finish):
        self.order.status='pending'
        self.order.stripe_session_id='cs_billing'
        self.order.save()
        session={'id':'cs_billing','client_reference_id':str(self.order.pk),
            'metadata':{'order_id':str(self.order.pk)},'currency':'eur',
            'payment_status':'paid','amount_subtotal':8000,'amount_total':8600,
            'total_details':{'amount_shipping':600,'amount_discount':0},
            'customer_details':{**self.billing,'email':self.owner.email,'phone':'UNNECESSARY_PHONE'},
        }
        self.assertTrue(settle(session))
        self.order.refresh_from_db()
        self.assertEqual(self.order.billing_details,self.billing)
        self.assertNotIn('phone',self.order.billing_details)
        self.assertFalse(Invoice.objects.exists())
