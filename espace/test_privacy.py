import json
import uuid
from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from connexion.models import SignupQuota
from connexion.test_helpers import verify_test_user
from avoirs.models import BenefitMail, BenefitRedemption, CustomerBenefit
from facturation.models import Invoice
from paiement.models import Order
from retours.models import ReturnLine, ReturnRequest, ReturnShipment
from .models import Address, CreditNote, Voucher
from .privacy import retention_review, subject_export


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True)
class PrivacyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.user = users.objects.create_user(username='privacy-client', email='client@example.test',
                                             password='ComptePrive2026!', first_name='Camille')
        cls.other = users.objects.create_user(username='other-client', email='autre@example.test',
                                              password='AutreCompte2026!')
        cls.order = Order.objects.create(user=cls.user, session_key='NEVER_EXPORT_SESSION',
            stripe_session_id='NEVER_EXPORT_STRIPE_SESSION', customer_email=cls.user.email,
            status='paid', subtotal=4200, total_paid=4200,
            checkout_payload={'secret': 'NEVER_EXPORT_CHECKOUT_PAYLOAD'},
            items=[{'name': 'Veste', 'quantity': 1, 'unit_amount': 4200, 'secret': 'NEVER_EXPORT_ITEM_SECRET'}],
            shipping_details={'name': 'Camille', 'address': {'line1': 'Adresse du client', 'country': 'FR'}},
            billing_details={'name': 'Camille', 'address': {'line1': 'Adresse de facturation', 'country': 'FR'}})
        # Matching e-mails must never be treated as proof of order ownership.
        cls.invoice = Invoice.objects.create(order=cls.order, number='WEB-2026-PRIVACY',
            issued_at=timezone.now(), snapshot={'client': {'nom': 'Camille', 'adresse': 'Adresse de facturation'}},
            issued_by_identifier='NEVER_EXPORT_STAFF_IDENTIFIER')
        cls.foreign = Order.objects.create(user=cls.other, session_key='other', customer_email=cls.user.email,
            status='paid', subtotal=4300, items=[{'name': 'AUTRE_COMMANDE_PRIVEE', 'quantity': 1}])
        cls.guest = Order.objects.create(session_key='guest', customer_email=cls.user.email,
            status='paid', subtotal=4400, items=[{'name': 'ACHAT_INVITE_NON_RATTACHE', 'quantity': 1}])
        Address.objects.create(user=cls.user, recipient='Camille', line1='Adresse personnelle',
                               postal_code='01410', city='Lélex')
        Address.objects.create(user=cls.other, recipient='AUTRE_DESTINATAIRE', line1='AUTRE_ADRESSE',
                               postal_code='75001', city='Paris')
        cls.record = ReturnRequest.objects.create(order=cls.order, submission_id=uuid.uuid4(), amount=4200,
                                                  reason='Taille trop petite')
        ReturnLine.objects.create(request=cls.record, item_index=0, name='Veste', quantity=1, amount=4200)
        cls.shipment = ReturnShipment.objects.create(request=cls.record, submission_id=uuid.uuid4(),
            carrier='Transporteur', tracking_number='SUIVI_CLIENT', has_proof=True, proof='NEVER_EXPORT_PROOF_BLOB')
        CreditNote.objects.create(order=cls.order, reference='AV-CLIENT', amount='42.00', reason='Retour')
        Voucher.objects.create(user=cls.user, code='BON-CLIENT', description='Conditions du bon')

    def test_export_only_linked_records_and_never_secrets(self):
        data = subject_export(self.user)
        self.assertEqual([o['reference'] for o in data['commandes']], [str(self.order.pk)])
        self.assertEqual(data['adresses'][0]['destinataire'], 'Camille')
        self.assertEqual(data['commandes'][0]['facturation']['address']['line1'], 'Adresse de facturation')
        self.assertEqual(data['commandes'][0]['facture_emise']['numero'], 'WEB-2026-PRIVACY')
        self.assertEqual(data['commandes'][0]['facture_emise']['document']['client']['nom'], 'Camille')
        self.assertEqual(data['commandes'][0]['retours'][0]['expeditions'][0]['suivi'], 'SUIVI_CLIENT')
        self.assertTrue(data['commandes'][0]['retours'][0]['expeditions'][0]['justificatif_disponible'])
        self.assertEqual(data['avoirs_enregistres'][0]['reference'], 'AV-CLIENT')
        self.assertEqual(data['bons_de_reduction'][0]['code'], 'BON-CLIENT')
        text = str(data)
        for forbidden in ('NEVER_EXPORT_', 'AUTRE_ADRESSE', 'AUTRE_COMMANDE_PRIVEE', 'ACHAT_INVITE_NON_RATTACHE', self.user.password):
            self.assertNotIn(forbidden, text)

    def test_password_confirmation_download_and_no_cache(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse('espace:privacy_account'))
        self.assertContains(page, 'Télécharger mes données')
        self.assertIn('private', page['Cache-Control'])
        self.assertIn('no-store', page['Cache-Control'])
        response = self.client.post(reverse('espace:privacy_export'), {
            'current_password': 'ComptePrive2026!', 'user_id': self.other.pk,
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(json.loads(response.content)['compte']['email'], self.user.email)
        self.assertNotContains(response, 'AUTRE_COMMANDE_PRIVEE')
        self.assertEqual(self.client.get(reverse('espace:privacy_export')).status_code, 405)

    def test_export_requires_login_email_verification_password_and_csrf(self):
        url = reverse('espace:privacy_export')
        self.assertEqual(self.client.post(url, {'current_password': 'ComptePrive2026!'}).status_code, 302)
        self.client.force_login(self.user)
        invalid = self.client.post(url, {'current_password': 'mauvais'})
        self.assertEqual(invalid.status_code, 400)
        self.assertNotIn('Content-Disposition', invalid)
        with override_settings(LOCAL_SKIP_EMAIL_VERIFICATION=False):
            self.assertRedirects(self.client.post(url, {'current_password': 'ComptePrive2026!'}),
                                 reverse('connexion:verify_email'), fetch_redirect_response=False)
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.user)
        protected.get(reverse('espace:privacy_account'))
        self.assertEqual(protected.post(url, {'current_password': 'ComptePrive2026!'}).status_code, 403)

    def test_password_attempt_budget_shared_by_sessions(self):
        first, second = Client(), Client()
        first.force_login(self.user)
        second.force_login(self.user)
        url = reverse('espace:privacy_export')
        for i in range(5):
            client = first if i % 2 else second
            self.assertEqual(client.post(url, {'current_password': 'mauvais'}).status_code, 400)
        response = first.post(url, {'current_password': 'ComptePrive2026!'})
        self.assertEqual(response.status_code, 429)
        self.assertNotIn('Content-Disposition', response)

    def test_retention_audit_is_read_only_and_outputs_no_personal_identifiers(self):
        now = timezone.now()
        Session.objects.create(session_key='EXPIRED_PRIVATE_SESSION', session_data='private',
                               expire_date=now - timedelta(days=1))
        SignupQuota.objects.create(key='a' * 64, expires_at=now - timedelta(days=3), attempts=1)
        Order.objects.filter(pk=self.order.pk).update(created_at=now - timedelta(days=4000))
        before = (Session.objects.count(), SignupQuota.objects.count(), Order.objects.count(),
                  ReturnRequest.objects.count(), ReturnShipment.objects.count(), get_user_model().objects.count())
        output = StringIO()
        call_command('privacy_audit', '--json', stdout=output)
        report = json.loads(output.getvalue())
        self.assertEqual(report['modifications'], 0)
        self.assertEqual(report['mode'], 'lecture seule')
        categories = {entry['code']: entry['nombre'] for entry in report['categories']}
        self.assertEqual(categories['expired_sessions'], 1)
        self.assertEqual(categories['expired_security_quotas'], 1)
        self.assertEqual(categories['old_paid_orders_review'], 1)
        self.assertEqual(before, (Session.objects.count(), SignupQuota.objects.count(), Order.objects.count(),
                                  ReturnRequest.objects.count(), ReturnShipment.objects.count(), get_user_model().objects.count()))
        for private in (self.user.email, self.user.username, str(self.order.pk), 'EXPIRED_PRIVATE_SESSION', 'SUIVI_CLIENT'):
            self.assertNotIn(private, output.getvalue())

    def test_recent_order_return_or_shipment_excludes_account_from_inactivity_review(self):
        now = timezone.now()
        get_user_model().objects.filter(pk__in=[self.user.pk, self.other.pk]).update(
            date_joined=now - timedelta(days=900), last_login=now - timedelta(days=900))
        result = {e['code']: e['nombre'] for e in retention_review(now)['categories']}
        self.assertEqual(result['inactive_accounts_review'], 0)
        Order.objects.filter(user=self.other).update(created_at=now - timedelta(days=900))
        result = {e['code']: e['nombre'] for e in retention_review(now)['categories']}
        self.assertEqual(result['inactive_accounts_review'], 1)

    def test_public_privacy_explains_export_and_remaining_validation(self):
        response = self.client.get(reverse('espace:privacy'))
        self.assertContains(response, reverse('espace:privacy_account'))
        self.assertContains(response, 'Dix ans à compter de la clôture')
        self.assertContains(response, 'aucune suppression automatique générale')
        self.assertContains(response, 'N’en envoyez pas spontanément')

    def test_customer_benefits_export_only_linked_account_records_and_safe_usage(self):
        benefit = CustomerBenefit.objects.create(user=self.user, recipient_email=self.user.email,
            label='Avoir personnel', kind='credit', value='15.00')
        BenefitRedemption.objects.create(benefit=benefit, order=self.order, state='used',
            discount_amount=1500, used_at=timezone.now(),
            snapshot={'recipient_email_hash': 'NEVER_EXPORT_BENEFIT_HASH', 'private': 'NEVER_EXPORT_BENEFIT_SNAPSHOT'},
            stripe_coupon_id='NEVER_EXPORT_STRIPE_COUPON')
        # Even a malformed relation must not disclose somebody else's order.
        BenefitRedemption.objects.create(benefit=benefit, order=self.foreign, state='released',
            discount_amount=1500, snapshot={'private': 'NEVER_EXPORT_FOREIGN_SNAPSHOT'})
        BenefitMail.objects.create(benefit=benefit, key='NEVER_EXPORT_MAIL_KEY',
            payload={'secret': 'NEVER_EXPORT_MAIL_PAYLOAD', 'recipient': self.user.email})
        foreign = CustomerBenefit.objects.create(user=self.other, recipient_email=self.user.email,
            label='AUTRE_AVOIR_PRIVE', kind='amount', value='5.00')
        unclaimed = CustomerBenefit.objects.create(recipient_email=self.user.email,
            label='AVOIR_NON_RATTACHE', kind='amount', value='5.00')
        data = subject_export(self.user)
        self.assertEqual(len(data['avoirs_et_bons_clients']), 1)
        exported = data['avoirs_et_bons_clients'][0]
        self.assertEqual(exported['code'], benefit.code)
        self.assertEqual(str(exported['minimum_articles_euros']), '15.00')
        self.assertEqual(exported['utilisations'][0]['commande'], str(self.order.pk))
        self.assertEqual(exported['utilisations'][0]['remise_centimes'], 1500)
        self.assertEqual(len(exported['utilisations']), 1)
        for private in ('NEVER_EXPORT_', foreign.code, unclaimed.code, 'AUTRE_AVOIR_PRIVE', 'AVOIR_NON_RATTACHE'):
            self.assertNotIn(private, str(data))

    def test_outstanding_benefit_and_recent_usage_exclude_account_from_inactivity_review(self):
        now = timezone.now()
        old = now - timedelta(days=1100)
        customer = get_user_model().objects.create_user(username='old-benefit-client',
            email='old-benefit@example.test', password='ComptePrive2026!')
        get_user_model().objects.filter(pk=customer.pk).update(date_joined=old, last_login=old)

        def count():
            return next(entry['nombre'] for entry in retention_review(now)['categories']
                        if entry['code'] == 'inactive_accounts_review')

        self.assertEqual(count(), 1)

        benefit = CustomerBenefit.objects.create(user=customer, recipient_email=customer.email,
            label='Avoir conservé', kind='credit', value='15.00')
        CustomerBenefit.objects.filter(pk=benefit.pk).update(created_at=old, updated_at=old)
        self.assertEqual(count(), 0)
        CustomerBenefit.objects.filter(pk=benefit.pk).update(ends_at=old + timedelta(days=5))
        self.assertEqual(count(), 1)
        order = Order.objects.create(user=customer, session_key='old-benefit', items=[], subtotal=3000,
            status='pending', reservation_expires_at=old)
        Order.objects.filter(pk=order.pk).update(created_at=old)
        redemption = BenefitRedemption.objects.create(benefit=benefit, order=order,
            state='held', discount_amount=1500)
        BenefitRedemption.objects.filter(pk=redemption.pk).update(created_at=old)
        # An elapsed date never proves that a provider-side payment has failed.
        self.assertEqual(count(), 0)
        BenefitRedemption.objects.filter(pk=redemption.pk).update(state='used', used_at=now)
        self.assertEqual(count(), 0)
        BenefitRedemption.objects.filter(pk=redemption.pk).update(used_at=old)
        self.assertEqual(count(), 1)

    def test_order_export_keeps_own_historical_benefit_terms_after_reassignment(self):
        benefit = CustomerBenefit.objects.create(user=self.other, recipient_email=self.other.email,
            label='NEW_CONDITIONS_FOR_OTHER', kind='percent', value='40.00')
        BenefitRedemption.objects.create(benefit=benefit, order=self.order, state='released',
            discount_amount=500, snapshot={
                'code': benefit.code, 'label': 'Anciennes conditions personnelles', 'kind': 'credit',
                'value': '5.00', 'effective_minimum': '5.00',
                'recipient_email_hash': 'NEVER_EXPORT_BENEFIT_HASH',
                'user_id': self.user.pk, 'private': 'NEVER_EXPORT_INTERNAL_SNAPSHOT',
            })
        data = subject_export(self.user)
        self.assertEqual(data['avoirs_et_bons_clients'], [])
        historical = data['commandes'][0]['avantage_client']
        self.assertEqual(historical['code'], benefit.code)
        self.assertEqual(historical['intitule'], 'Anciennes conditions personnelles')
        self.assertEqual(historical['valeur'], '5.00')
        self.assertEqual(historical['remise_centimes'], 500)
        self.assertNotIn('NEW_CONDITIONS_FOR_OTHER', str(data))
        self.assertNotIn('NEVER_EXPORT_', str(data))

    def test_verified_recipient_can_export_email_only_grant_sent_before_registration(self):
        benefit = CustomerBenefit.objects.create(recipient_email='future-privacy@example.test',
            label='Attention avant inscription', kind='credit', value='15.00')
        customer = get_user_model().objects.create_user(username='future-privacy-client',
            email='future-privacy@example.test', password='ComptePrive2026!')
        self.assertEqual(subject_export(customer)['avoirs_et_bons_clients'], [])
        verify_test_user(customer)
        foreign = CustomerBenefit.objects.create(user=self.other, recipient_email=customer.email,
            label='NEVER_EXPORT_OTHER_ACCOUNT_BENEFIT', kind='amount', value='5.00')
        deleted_account = get_user_model().objects.create_user(username='deleted-privacy-client',
            email='deleted-privacy@example.test', password='ComptePrive2026!')
        historical = CustomerBenefit.objects.create(user=deleted_account, bound_user_id=deleted_account.pk,
            recipient_email=customer.email, label='NEVER_EXPORT_DELETED_ACCOUNT_BENEFIT', kind='amount', value='5.00')
        deleted_account.delete()
        data = subject_export(customer)
        self.assertEqual([record['code'] for record in data['avoirs_et_bons_clients']], [benefit.code])
        self.assertNotIn(foreign.code, str(data))
        self.assertNotIn(historical.code, str(data))
        self.assertNotIn('NEVER_EXPORT_', str(data))
        self.assertNotIn('recipient_email_hash', str(data))
        self.assertNotIn('bound_user_id', str(data))
        benefit.refresh_from_db()
        self.assertIsNone(benefit.user_id)
        self.assertIsNone(benefit.bound_user_id)

    def test_benefit_mail_retention_review_is_read_only_and_contains_no_payload(self):
        now = timezone.now()
        benefit = CustomerBenefit.objects.create(user=self.user, recipient_email=self.user.email,
            label='Avoir personnel', kind='amount', value='5.00')
        mail = BenefitMail.objects.create(benefit=benefit, key='NEVER_EXPORT_MAIL_KEY',
            payload={'secret': 'NEVER_EXPORT_MAIL_PAYLOAD'}, sent_at=now - timedelta(days=100))
        report = retention_review(now)
        count = next(entry['nombre'] for entry in report['categories']
                     if entry['code'] == 'sent_benefit_mails_review')
        self.assertEqual(count, 1)
        self.assertEqual(report['modifications'], 0)
        self.assertTrue(BenefitMail.objects.filter(pk=mail.pk).exists())
        for private in (benefit.code, self.user.email, 'NEVER_EXPORT_'):
            self.assertNotIn(private, str(report))
