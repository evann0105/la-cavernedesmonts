"""Shop settings edited by the seller, with historical purchase records preserved."""
from copy import deepcopy
import json
import uuid

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import resolve, reverse
from django.utils import timezone

from connexion.models import EmailVerification
from connexion.test_helpers import verify_test_user
from .configuration import configuration
from .models import ShopConfiguration


LEGACY = {
    'business_name': 'Élodie FOLOPPE', 'legal_form': 'EI',
    'siren': '525 114 914', 'siret': '525 114 914 00019',
    'registration': '525 114 914 RCS Bourg-en-Bresse',
    'address': '225 rue des Monts-Jura, 01410 Lélex, France',
    'phone': '04 50 40 46 17', 'email': 'legacy@example.test',
}


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True,
                   SHOP_LEGAL=LEGACY, RETURN_ADDRESS='Ancien dépôt\n01410 Lélex',
                   EMAIL_DELIVERY_ENABLED=False, EMAIL_TEST_MODE=False,
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ShopConfigurationTests(TestCase):
    backend = 'connexion.backends.EmailOrUsernameBackend'
    fields = {
        'contacts': ('phone', 'email'),
        'store': ('store_address_line1', 'store_address_line2', 'store_postal_code',
                  'store_city', 'store_country'),
        'returns': ('returns_same_as_store', 'return_address'),
        'company': ('business_name', 'legal_form', 'siren', 'siret', 'registration',
                    'publisher', 'business_same_as_store', 'business_address'),
    }

    def setUp(self):
        self.manager = get_user_model().objects.create_user(
            'configuration-manager', email='manager@example.test', is_staff=True)
        self.catalog_permission = Permission.objects.get(
            content_type__app_label='core', codename='manage_catalog')
        self.manager.user_permissions.add(self.catalog_permission)
        verify_test_user(self.manager)
        self.client.force_login(self.manager, backend=self.backend)

    def url(self, section=None):
        return reverse('boutique:settings' + ('_' + section if section else ''))

    def seed(self, **changes):
        values = {
            'phone': '04 50 40 46 17', 'email': 'initial@example.test',
            'store_address_line1': '10 rue du Magasin', 'store_address_line2': 'Bâtiment A',
            'store_postal_code': '01410', 'store_city': 'Lélex', 'store_country': 'France',
            'business_name': 'Élodie FOLOPPE', 'legal_form': 'EI',
            'siren': '525114914', 'siret': '52511491400019',
            'registration': '525114914 RCS Bourg-en-Bresse', 'publisher': 'Élodie FOLOPPE',
            'business_address': '40 rue du Siège\n69001 Lyon\nFrance',
            'business_same_as_store': False,
            'return_address': '20 rue du Dépôt\n69002 Lyon\nFrance',
            'returns_same_as_store': False,
        }
        values.update(changes)
        record = ShopConfiguration(pk=1, **values)
        record.full_clean()
        record.save()
        return record

    def data(self, section, **changes):
        current = configuration()
        payload = {'revision': str(current['revision'])}
        for field in self.fields[section]:
            source = field + '_custom' if field in ('business_address', 'return_address') else field
            value = current.get(source, current.get(field, ''))
            payload[field] = ('on' if value else '') if isinstance(value, bool) else value
        payload.update(changes)
        return payload

    def save_page(self, section, **changes):
        response = self.client.post(self.url(section), self.data(section, **changes))
        self.assertEqual(response.status_code, 302)
        return response

    def values(self):
        return list(ShopConfiguration.objects.order_by('pk').values())

    def assert_invalid(self, section, **changes):
        before, logs = self.values(), LogEntry.objects.count()
        response = self.client.post(self.url(section), self.data(section, **changes))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.values(), before)
        self.assertEqual(LogEntry.objects.count(), logs)
        return response

    def test_legacy_fallback_and_all_get_pages_do_not_create_configuration(self):
        self.assertFalse(ShopConfiguration.objects.exists())
        current = configuration()
        self.assertEqual(current['revision'], 0)
        self.assertEqual(current['email'], LEGACY['email'])
        self.assertEqual(current['phone'], LEGACY['phone'])
        self.assertFalse(ShopConfiguration.objects.exists())
        for section in (None, *self.fields):
            with self.subTest(section=section):
                response = self.client.get(self.url(section))
                self.assertEqual(response.status_code, 200)
                self.assertIn('no-store', response.headers['Cache-Control'])
        for name in ('core:location', 'espace:contact', 'espace:legal_notice'):
            self.assertEqual(Client().get(reverse(name)).status_code, 200)
        self.assertFalse(ShopConfiguration.objects.exists())
        self.assertFalse(LogEntry.objects.exists())

    def test_first_save_persists_legacy_values_without_resetting_other_sections(self):
        original = configuration()
        self.save_page('contacts', phone='06 12 34 56 78', email='nouveau@example.test')
        self.assertEqual(ShopConfiguration.objects.count(), 1)
        record = ShopConfiguration.objects.get(pk=1)
        self.assertEqual(record.revision, 1)
        self.assertEqual(record.updated_by_id, self.manager.pk)
        current = configuration()
        self.assertEqual(current['email'], 'nouveau@example.test')
        for field in ('business_name', 'siren', 'siret', 'store_address', 'return_address'):
            with self.subTest(field=field):
                self.assertEqual(current[field], original[field])

    def test_anonymous_customer_staff_and_returns_only_roles_cannot_change_settings(self):
        for section in (None, *self.fields):
            response = Client().get(self.url(section))
            self.assertEqual(response.status_code, 302)
            self.assertIn('next=', response.url)
        roles = [('ordinary', False, None), ('staff-only', True, None),
                 ('nonstaff-with-permission', False, self.catalog_permission),
                 ('returns-only', True, Permission.objects.get(
                     content_type__app_label='retours', codename='manage_returns'))]
        for username, staff, permission in roles:
            user = get_user_model().objects.create_user(username, email=username + '@example.test', is_staff=staff)
            if permission:
                user.user_permissions.add(permission)
            verify_test_user(user)
            browser = Client()
            browser.force_login(user, backend=self.backend)
            for section in self.fields:
                with self.subTest(role=username, section=section):
                    self.assertEqual(browser.get(self.url(section)).status_code, 403)
                    self.assertEqual(browser.post(self.url(section), self.data(section)).status_code, 403)
            self.assertEqual(browser.get(self.url()).status_code, 403)
        self.assertFalse(ShopConfiguration.objects.exists())

    def test_manager_needs_real_confirmation_when_either_local_flag_is_disabled(self):
        EmailVerification.objects.filter(user=self.manager).delete()
        for flags in ({'DEBUG': False, 'LOCAL_SKIP_EMAIL_VERIFICATION': True},
                      {'DEBUG': True, 'LOCAL_SKIP_EMAIL_VERIFICATION': False}):
            with self.subTest(flags=flags), override_settings(**flags):
                for section in self.fields:
                    for response in (self.client.get(self.url(section)),
                                     self.client.post(self.url(section), self.data(section))):
                        self.assertRedirects(response, reverse('connexion:verify_email'),
                                             fetch_redirect_response=False)
        self.assertFalse(ShopConfiguration.objects.exists())

    def test_local_manager_can_save_without_fabricating_email_confirmation(self):
        EmailVerification.objects.filter(user=self.manager).delete()
        self.save_page('contacts', email='essais@example.test')
        self.assertEqual(configuration()['email'], 'essais@example.test')
        self.assertFalse(EmailVerification.objects.filter(user=self.manager).exists())

    def test_all_forms_require_csrf_and_accept_the_form_token(self):
        self.seed()
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.manager, backend=self.backend)
        for section in self.fields:
            with self.subTest(section=section):
                before = self.values()
                self.assertEqual(strict.post(self.url(section), self.data(section)).status_code, 403)
                self.assertEqual(self.values(), before)
                self.assertEqual(strict.get(self.url(section)).status_code, 200)
                token = strict.cookies['csrftoken'].value
                response = strict.post(self.url(section), self.data(section), HTTP_X_CSRFTOKEN=token)
                self.assertEqual(response.status_code, 302)

    def test_get_and_unsupported_methods_do_not_change_saved_values(self):
        self.seed()
        before, logs = self.values(), LogEntry.objects.count()
        for section in (None, *self.fields):
            self.assertEqual(self.client.get(self.url(section)).status_code, 200)
            self.assertEqual(self.client.put(self.url(section), {}).status_code, 405)
            self.assertEqual(self.client.delete(self.url(section)).status_code, 405)
        self.assertEqual(self.values(), before)
        self.assertEqual(LogEntry.objects.count(), logs)

    def test_independent_pages_ignore_foreign_fields_and_record_actor_and_revision(self):
        record = self.seed()
        original_revision = record.revision
        self.save_page('contacts', phone='06 12 34 56 78', email='clientele@example.test',
                       business_name='INJECTED COMPANY', return_address='INJECTED RETURN', revision=str(original_revision))
        self.save_page('store', store_address_line1='12 avenue du Magasin', store_city='Mijoux',
                       email='INJECTED@example.test')
        self.save_page('returns', return_address='15 rue des Retours\n69003 Lyon\nFrance',
                       business_name='INJECTED COMPANY')
        self.save_page('company', business_name='Élodie FOLOPPE modifiée', phone='01 00 00 00 00')
        record.refresh_from_db()
        self.assertEqual(record.email, 'clientele@example.test')
        self.assertEqual(record.phone, '06 12 34 56 78')
        self.assertEqual(record.store_city, 'Mijoux')
        self.assertEqual(record.return_address, '15 rue des Retours\n69003 Lyon\nFrance')
        self.assertEqual(record.business_name, 'Élodie FOLOPPE modifiée')
        self.assertEqual(record.revision, original_revision + 4)
        self.assertEqual(record.updated_by_id, self.manager.pk)
        entries = LogEntry.objects.filter(user=self.manager, action_flag=CHANGE,
            object_id='1', content_type=ContentType.objects.get_for_model(ShopConfiguration))
        self.assertEqual(entries.count(), 4)

    def test_stale_revision_from_another_settings_page_cannot_overwrite_new_values(self):
        self.seed()
        stale_company = self.data('company', business_name='Ancien onglet')
        self.save_page('contacts', email='adresse-recente@example.test')
        before, logs = self.values(), LogEntry.objects.count()
        response = self.client.post(self.url('company'), stale_company)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.values(), before)
        self.assertEqual(LogEntry.objects.count(), logs)
        self.assertEqual(configuration()['email'], 'adresse-recente@example.test')

    def test_missing_revision_and_duplicate_initial_submissions_do_not_overwrite(self):
        first = self.data('contacts', email='premier@example.test')
        missing = dict(first)
        del missing['revision']
        self.assertEqual(self.client.post(self.url('contacts'), missing).status_code, 200)
        self.assertFalse(ShopConfiguration.objects.exists())
        self.assertEqual(self.client.post(self.url('contacts'), first).status_code, 302)
        before = self.values()
        self.assertEqual(self.client.post(self.url('contacts'), first).status_code, 409)
        self.assertEqual(self.values(), before)
        self.assertEqual(ShopConfiguration.objects.count(), 1)

    def test_invalid_contact_formats_never_change_saved_configuration(self):
        self.seed()
        for changes in ({'email': 'sans-arobase'}, {'email': 'a@example.test\r\nBcc:other@example.test'},
                        {'phone': 'appelez demain'}, {'phone': '123'}, {'email': ''}, {'phone': ''}):
            with self.subTest(changes=changes):
                self.assert_invalid('contacts', **changes)

    def test_company_identifiers_and_required_fields_are_validated(self):
        self.seed()
        for changes in ({'siren': '123'}, {'siren': 'abcdefghi'}, {'siret': '525114914'},
                        {'siret': '12345678900019'}, {'business_name': ''}, {'legal_form': ''},
                        {'registration': ''}):
            with self.subTest(changes=changes):
                self.assert_invalid('company', **changes)

    def test_spaced_identifiers_are_accepted_and_long_individual_form_is_displayed_as_ei(self):
        from espace.legal import profile
        self.seed()
        self.save_page('company', siren='525 114 914', siret='525 114 914 00019',
                       legal_form='Entreprise individuelle')
        record = ShopConfiguration.objects.get(pk=1)
        self.assertEqual(''.join(record.siren.split()), '525114914')
        self.assertEqual(''.join(record.siret.split()), '52511491400019')
        self.assertEqual(profile()['legal_form'], 'EI')

    def test_linked_addresses_follow_store_changes_and_custom_values_are_retained(self):
        record = self.seed()
        old_business, old_returns = record.business_address, record.return_address
        self.save_page('company', business_same_as_store='on', business_address='ignored ' * 2000)
        self.save_page('returns', returns_same_as_store='on', return_address='ignored ' * 2000)
        self.save_page('store', store_address_line1='8 rue du Nouveau Magasin', store_address_line2='',
                       store_postal_code='39200', store_city='Saint-Claude')
        current = configuration()
        self.assertEqual(current['business_address'], current['store_address'])
        self.assertEqual(current['return_address'], current['store_address'])
        self.assertIn('Saint-Claude', current['store_address'])
        record.refresh_from_db()
        self.assertEqual(record.business_address, old_business)
        self.assertEqual(record.return_address, old_returns)
        self.save_page('company', business_same_as_store='')
        self.save_page('returns', returns_same_as_store='')
        self.assertEqual(configuration()['business_address'], old_business)
        self.assertEqual(configuration()['return_address'], old_returns)

    def test_independent_addresses_are_required_when_not_linked_to_store(self):
        self.seed()
        self.assert_invalid('company', business_same_as_store='', business_address='')
        self.assert_invalid('returns', returns_same_as_store='', return_address='')
        self.assert_invalid('store', store_address_line1='')
        self.assert_invalid('store', store_city='')
        self.assert_invalid('store', store_postal_code='ABC', store_country='France')

    def test_saved_contacts_appear_on_public_pages_and_legal_identity_keeps_own_address(self):
        self.seed()
        self.save_page('contacts', phone='06 12 34 56 78', email='nouveau-contact@example.test')
        visitor = Client()
        for name in ('accueil:home', 'core:location', 'espace:contact', 'espace:legal_notice'):
            with self.subTest(page=name):
                response = visitor.get(reverse(name))
                self.assertContains(response, '06 12 34 56 78')
                self.assertContains(response, 'nouveau-contact@example.test')
                self.assertNotContains(response, LEGACY['phone'])
                self.assertNotContains(response, 'href="tel:+33450404617"')
                self.assertNotContains(response, 'href="mailto:contact@la-cavernedesmonts.fr"')
        self.assertContains(visitor.get(reverse('core:location')), '10 rue du Magasin')
        self.assertContains(visitor.get(reverse('espace:legal_notice')), '40 rue du Siège')

    def test_emails_render_current_public_contacts_without_http_request_or_smtp_change(self):
        from django.conf import settings
        from courriels.messages import build_message
        self.seed(phone='06 12 34 56 78', email='mails-contact@example.test')
        sender = settings.DEFAULT_FROM_EMAIL
        message = build_message('contact', 'client@example.test', 'https://boutique.example.test/')
        self.assertIn('06 12 34 56 78', message.body)
        self.assertIn('mails-contact@example.test', message.body)
        self.assertIn('mails-contact@example.test', message.alternatives[0].content)
        self.assertEqual(message.from_email, sender)
        self.assertEqual(message.to, ['client@example.test'])

    def test_return_pages_and_queued_acknowledgement_use_distinct_return_address(self):
        from paiement.models import Order
        from retours.mail import return_notice
        from retours.models import ReturnLine, ReturnMail, ReturnRequest
        self.seed()
        order = Order.objects.create(session_key='configuration-return', customer_email='client@example.test',
            status='paid', subtotal=3600, total_paid=3600, paid_line_totals=[3600], return_shipping_amount=0,
            items=[{'name': 'Polaire', 'size': 'M', 'color': 'Vert', 'quantity': 1, 'unit_amount': 3600}])
        record = ReturnRequest.objects.create(order=order, submission_id=uuid.uuid4(), amount=3600)
        ReturnLine.objects.create(request=record, item_index=0, name='Polaire', size='M', color='Vert',
                                  quantity=1, amount=3600, unit_amount=3600)
        return_notice(record, 'return_requested', 'Déclaration de rétractation')
        queued = ReturnMail.objects.get(request=record)
        original_details = queued.payload['details']
        self.assertIn(configuration()['return_address'], original_details)
        self.assertNotIn('40 rue du Siège', original_details)
        self.assertEqual(queued.payload['recipient'], order.customer_email)
        self.assertContains(Client().get(reverse('retours:entry')), '20 rue du Dépôt')
        self.save_page('returns', return_address='30 rue du Nouveau Dépôt\n69003 Lyon\nFrance')
        queued.refresh_from_db()
        self.assertEqual(queued.payload['details'], original_details)
        self.assertContains(Client().get(reverse('retours:entry')), '30 rue du Nouveau Dépôt')

    @override_settings(SITE_URL='https://boutique.example.test', SEO_INDEXING_ENABLED=True)
    def test_structured_store_data_uses_physical_store_instead_of_legal_or_return_address(self):
        from core.seo import page_seo
        self.seed()
        request = RequestFactory().get('/')
        request.resolver_match = resolve('/')
        with override_settings(DEBUG=False):
            graph = json.loads(page_seo(request)['json_ld'])['@graph']
        store = next(entry for entry in graph if entry['@type'] == 'ClothingStore')
        self.assertIsInstance(store['address'], dict)
        self.assertIn('10 rue du Magasin', store['address']['streetAddress'])
        self.assertEqual(store['address']['postalCode'], '01410')
        self.assertEqual(store['address']['addressLocality'], 'Lélex')
        self.assertNotIn('40 rue du Siège', json.dumps(store['address'], ensure_ascii=False))
        self.assertNotIn('20 rue du Dépôt', json.dumps(store['address'], ensure_ascii=False))

    @override_settings(SHOP_LEGAL={**LEGACY, 'vat_notice': 'TVA non applicable, article 293 B du CGI.'},
                       SHOP_INVOICING={'ready': True, 'numbering_confirmed': True,
                                       'issue_policy_confirmed': True, 'vat_mode': 'franchise'})
    def test_company_changes_do_not_rewrite_purchase_terms_fiscal_profile_or_issued_invoice(self):
        from espace.legal import contract_snapshot
        from facturation.delivery import build_delivery_slip
        from facturation.profile import capture_profile
        from facturation.services import build_preview, issue
        from paiement.models import Order
        self.seed()
        self.manager.user_permissions.add(Permission.objects.get(
            content_type__app_label='retours', codename='manage_returns'))
        original_terms = contract_snapshot()
        original_profile = capture_profile()
        self.assertTrue(original_profile)
        buyer = {'name': 'Client Exemple', 'address': {
            'line1': '2 rue Client', 'postal_code': '69001', 'city': 'Lyon', 'country': 'FR'}}
        order = Order.objects.create(session_key='configuration-history', status='paid', paid_at=timezone.now(),
            customer_email='client@example.test', billing_details=buyer, shipping_details=buyer,
            subtotal=3600, total_paid=4200, return_shipping_amount=600, paid_line_totals=[3600],
            items=[{'name': 'Polaire achetée', 'size': 'M', 'color': 'Vert', 'quantity': 1, 'unit_amount': 3600}],
            invoice_profile=original_profile, terms_snapshot=original_terms)
        invoice = issue(order.pk, self.manager)
        original_invoice = deepcopy(invoice.snapshot)
        self.save_page('company', business_name='Nouvelle identité après achat',
                       business_address='99 rue du Siège modifié\n69004 Lyon\nFrance')
        self.save_page('contacts', email='contact-apres-achat@example.test')
        order.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual(order.terms_snapshot, original_terms)
        self.assertEqual(order.invoice_profile, original_profile)
        self.assertEqual(invoice.snapshot, original_invoice)
        self.assertEqual(issue(order.pk, self.manager).pk, invoice.pk)
        self.assertEqual(build_preview(order)['seller'], original_profile['seller'])
        self.assertEqual(build_delivery_slip(order)['seller'], original_profile['seller'])
        self.assertNotEqual(contract_snapshot()['text'], original_terms['text'])
        self.assertIn('Nouvelle identité après achat', contract_snapshot()['text'])

    @override_settings(SHOP_LEGAL={}, SHOP_INVOICING={}, LEGAL_DOCUMENTS_APPROVED=False)
    def test_contact_and_company_edits_do_not_invent_tax_or_open_sales(self):
        from espace.legal import profile, sales_ready
        self.seed()
        self.save_page('company', publisher='Élodie FOLOPPE')
        self.save_page('contacts', email='coordonnees@example.test')
        current = profile()
        self.assertEqual(current['vat_notice'], '')
        self.assertFalse(current.get('vat_number'))
        self.assertFalse(current['mediator_name'])
        self.assertFalse(current['host_name'])
        self.assertFalse(sales_ready())
