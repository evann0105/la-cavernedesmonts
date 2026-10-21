"""Merchant shipping settings and exact, destination-specific server quotes."""
from decimal import Decimal
import json

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from connexion.models import EmailVerification
from connexion.test_helpers import verify_test_user
from .models import ShippingConfiguration, ShippingCountry, ShippingZone
from .shipping import ShippingUnavailable, configuration, quote, ready


def create_managed_shipping(**changes):
    """Explicit test rates; never seed invented carrier or tariffs into runtime."""
    values = {
        'enabled': True, 'free_shipping_enabled': True,
        'free_shipping_threshold': Decimal('99.00'),
        'preparation_days_min': 1, 'preparation_days_max': 2,
        'carrier_name': 'Transporteur de test', 'service_name': 'Livraison standard de test',
        'carrier_website': 'https://transporteur.example.test', 'public_notice': '',
    }
    values.update(changes)
    policy = ShippingConfiguration.objects.create(pk=1, **values)
    france = ShippingZone.objects.create(configuration=policy, title='France de test',
        price=Decimal('6.00'), delivery_days_min=2, delivery_days_max=4,
        free_shipping_eligible=True, active=True)
    belgium = ShippingZone.objects.create(configuration=policy, title='Belgique de test',
        price=Decimal('12.00'), delivery_days_min=3, delivery_days_max=5,
        free_shipping_eligible=False, active=True)
    ShippingCountry.objects.create(zone=france, code='FR')
    ShippingCountry.objects.create(zone=belgium, code='BE')
    return policy, france, belgium


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True, EMAIL_DELIVERY_ENABLED=False)
class ShippingSettingsTests(TestCase):
    backend = 'connexion.backends.EmailOrUsernameBackend'

    def setUp(self):
        self.manager = get_user_model().objects.create_user(
            'shipping-settings-manager', email='shipping-manager@example.test', is_staff=True)
        self.permission = Permission.objects.get(content_type__app_label='core', codename='manage_catalog')
        self.manager.user_permissions.add(self.permission)
        verify_test_user(self.manager)
        self.client.force_login(self.manager, backend=self.backend)

    def url(self, section='overview', zone=None):
        return reverse('boutique:shipping_' + section, args=[zone.pk] if zone else None)

    def policy_data(self, **changes):
        current = configuration()
        fields = ('enabled', 'free_shipping_enabled', 'free_shipping_threshold',
                  'preparation_days_min', 'preparation_days_max', 'public_notice')
        data = {'revision': str(current['revision'])}
        for field in fields:
            value = current.get(field)
            data[field] = ('on' if value else '') if isinstance(value, bool) else ('' if value is None else str(value))
        data.update(changes)
        return data

    def carrier_data(self, **changes):
        current = configuration()
        data = {field: current.get(field, '') for field in ('carrier_name', 'service_name', 'carrier_website')}
        data['revision'] = str(current['revision'])
        data.update(changes)
        return data

    def zone_data(self, zone=None, **changes):
        data = {
            'configuration_revision': str(configuration()['revision']),
            'revision': str(zone.revision if zone else 0),
            'title': zone.title if zone else 'Nouvelle destination',
            'price': str(zone.price) if zone and zone.price is not None else '',
            'delivery_days_min': zone.delivery_days_min if zone else '',
            'delivery_days_max': zone.delivery_days_max if zone else '',
            'free_shipping_eligible': 'on' if zone and zone.free_shipping_eligible else '',
            'active': 'on' if zone and zone.active else '',
            'countries': list(ShippingCountry.objects.filter(zone=zone).values_list('code', flat=True)) if zone else [],
        }
        data.update(changes)
        return data

    def values(self):
        return (list(ShippingConfiguration.objects.order_by('pk').values()),
                list(ShippingZone.objects.order_by('pk').values()),
                list(ShippingCountry.objects.order_by('pk').values()))

    def assert_invalid(self, url, data):
        before, logs = self.values(), LogEntry.objects.count()
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors)
        self.assertEqual(self.values(), before)
        self.assertEqual(LogEntry.objects.count(), logs)
        return response

    def test_public_readers_and_all_empty_settings_pages_never_create_shipping_rows(self):
        self.assertFalse(ShippingConfiguration.objects.exists())
        self.assertFalse(configuration()['managed'])
        for section in ('overview', 'policy', 'carrier', 'zone_add'):
            response = self.client.get(self.url(section))
            self.assertEqual(response.status_code, 200)
            self.assertIn('no-store', response.headers['Cache-Control'])
        for path in ('/', '/panier/', '/conditions-generales-de-vente/'):
            self.assertEqual(Client().get(path).status_code, 200)
        self.assertFalse(ShippingConfiguration.objects.exists())
        self.assertFalse(ShippingZone.objects.exists())
        self.assertFalse(ShippingCountry.objects.exists())
        self.assertFalse(LogEntry.objects.exists())

    def test_first_policy_save_stays_draft_and_does_not_invent_carrier_or_rates(self):
        response = self.client.post(self.url('policy'), self.policy_data(
            enabled='', free_shipping_enabled='on', free_shipping_threshold='99',
            preparation_days_min='', preparation_days_max='', public_notice='À préparer'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ShippingConfiguration.objects.count(), 1)
        current = configuration()
        self.assertTrue(current['managed'])
        self.assertFalse(current['ready'])
        self.assertFalse(current['carrier_name'])
        self.assertFalse(current['zones'])
        self.assertEqual(current['revision'], 1)
        self.assertFalse(ready())
        with self.assertRaises(ShippingUnavailable):
            quote(8000, 'FR')

    def test_permissions_exclude_customers_plain_staff_nonstaff_and_returns_only_staff(self):
        policy, zone, _ = create_managed_shipping()
        endpoints = [self.url(section) for section in ('overview', 'policy', 'carrier', 'zone_add')]
        endpoints.append(self.url('zone_edit', zone))
        before = self.values()
        for url in endpoints:
            self.assertEqual(Client().get(url).status_code, 302)
            self.assertEqual(Client().post(url, {}).status_code, 302)
        roles = [('ordinary-shipping', False, None), ('staff-shipping', True, None),
                 ('nonstaff-shipping', False, self.permission),
                 ('returns-shipping', True, Permission.objects.get(
                     content_type__app_label='retours', codename='manage_returns'))]
        for username, staff, permission in roles:
            user = get_user_model().objects.create_user(username, email=username + '@example.test', is_staff=staff)
            if permission:
                user.user_permissions.add(permission)
            verify_test_user(user)
            browser = Client()
            browser.force_login(user, backend=self.backend)
            for url in endpoints:
                with self.subTest(role=username, url=url):
                    self.assertEqual(browser.get(url).status_code, 403)
                    self.assertEqual(browser.post(url, {}).status_code, 403)
        self.assertEqual(self.values(), before)

    def test_manager_requires_confirmation_outside_local_mode_and_get_never_changes_existing_data(self):
        _, zone, _ = create_managed_shipping()
        before = self.values()
        EmailVerification.objects.filter(user=self.manager).delete()
        with override_settings(LOCAL_SKIP_EMAIL_VERIFICATION=False):
            for section in ('overview', 'policy', 'carrier', 'zone_add'):
                self.assertRedirects(self.client.get(self.url(section)), reverse('connexion:verify_email'),
                                     fetch_redirect_response=False)
            self.assertRedirects(self.client.post(self.url('zone_edit', zone), self.zone_data(zone)),
                                 reverse('connexion:verify_email'), fetch_redirect_response=False)
        for section in ('overview', 'policy', 'carrier', 'zone_add'):
            self.assertEqual(self.client.get(self.url(section)).status_code, 200)
            self.assertEqual(self.client.put(self.url(section), {}).status_code, 405)
        self.assertEqual(self.client.get(self.url('zone_edit', zone)).status_code, 200)
        self.assertEqual(self.values(), before)

    def test_all_mutating_forms_require_csrf(self):
        _, zone, _ = create_managed_shipping()
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.manager, backend=self.backend)
        submissions = [(self.url('policy'), self.policy_data()), (self.url('carrier'), self.carrier_data()),
                       (self.url('zone_add'), self.zone_data(countries=['DE'])),
                       (self.url('zone_edit', zone), self.zone_data(zone))]
        before = self.values()
        for url, data in submissions:
            self.assertEqual(strict.post(url, data).status_code, 403)
        self.assertEqual(self.values(), before)
        self.assertEqual(strict.get(self.url('policy')).status_code, 200)
        self.assertEqual(strict.post(self.url('policy'), self.policy_data(public_notice='Jeton accepté'),
            HTTP_X_CSRFTOKEN=strict.cookies['csrftoken'].value).status_code, 302)

    def test_policy_carrier_and_zone_forms_do_not_overwrite_other_sections(self):
        policy, zone, other = create_managed_shipping()
        self.assertEqual(self.client.post(self.url('carrier'), self.carrier_data(
            carrier_name='Transporteur corrigé', free_shipping_threshold='1')).status_code, 302)
        self.assertEqual(self.client.post(self.url('policy'), self.policy_data(
            free_shipping_threshold='125.50', carrier_name='INJECTED')).status_code, 302)
        zone.refresh_from_db()
        self.assertEqual(self.client.post(self.url('zone_edit', zone), self.zone_data(
            zone, price='7.25', carrier_name='INJECTED', free_shipping_threshold='1')).status_code, 302)
        policy.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(policy.carrier_name, 'Transporteur corrigé')
        self.assertEqual(policy.free_shipping_threshold, Decimal('125.50'))
        self.assertEqual(other.price, Decimal('12.00'))
        self.assertEqual(quote(8000, 'FR')['amount'], 725)
        self.assertEqual(policy.updated_by_id, self.manager.pk)
        self.assertTrue(LogEntry.objects.filter(user=self.manager).exists())

    def test_stale_global_or_zone_revisions_cannot_overwrite_new_settings(self):
        _, zone, _ = create_managed_shipping()
        stale_policy = self.policy_data(public_notice='Ancien onglet')
        stale_zone = self.zone_data(zone, price='3')
        self.client.post(self.url('carrier'), self.carrier_data(service_name='Nouveau service'))
        before = self.values()
        self.assertEqual(self.client.post(self.url('policy'), stale_policy).status_code, 409)
        self.assertEqual(self.client.post(self.url('zone_edit', zone), stale_zone).status_code, 409)
        self.assertEqual(self.values(), before)
        zone.refresh_from_db()
        outdated_revision = zone.revision
        self.client.post(self.url('zone_edit', zone), self.zone_data(zone, price='7'))
        before = self.values()
        data = self.zone_data(zone, price='2', revision=str(outdated_revision))
        self.assertEqual(self.client.post(self.url('zone_edit', zone), data).status_code, 409)
        self.assertEqual(self.values(), before)

    def test_invalid_money_days_and_carrier_url_leave_database_unchanged(self):
        _, zone, _ = create_managed_shipping()
        for value in ('NaN', 'Infinity', '-1', '1.001', 'not-a-price'):
            with self.subTest(value=value):
                self.assert_invalid(self.url('policy'), self.policy_data(free_shipping_threshold=value))
                self.assert_invalid(self.url('zone_edit', zone), self.zone_data(zone, price=value))
        for changes in ({'preparation_days_min': 5, 'preparation_days_max': 2},
                        {'preparation_days_min': -1}, {'preparation_days_max': 61}):
            self.assert_invalid(self.url('policy'), self.policy_data(**changes))
        for changes in ({'delivery_days_min': 8, 'delivery_days_max': 3},
                        {'delivery_days_min': -1}, {'delivery_days_max': 61}, {'price': ''}, {'countries': []}):
            self.assert_invalid(self.url('zone_edit', zone), self.zone_data(zone, **changes))
        self.assert_invalid(self.url('carrier'), self.carrier_data(carrier_website='javascript:alert(1)'))

    def test_zero_threshold_and_explicit_zero_rate_are_valid(self):
        _, zone, _ = create_managed_shipping()
        self.assertEqual(self.client.post(self.url('policy'), self.policy_data(free_shipping_threshold='0')).status_code, 302)
        self.assertEqual(quote(1, 'FR')['amount'], 0)
        zone.refresh_from_db()
        self.assertEqual(self.client.post(self.url('zone_edit', zone), self.zone_data(
            zone, price='0', free_shipping_eligible='')).status_code, 302)
        self.assertEqual(quote(1, 'FR')['amount'], 0)

    def test_duplicate_or_unknown_countries_are_rejected_without_reassigning_existing_country(self):
        _, france, _ = create_managed_shipping()
        for codes in (['FR'], ['XX']):
            self.assert_invalid(self.url('zone_add'), self.zone_data(
                title='Autre zone', countries=codes, price='5', delivery_days_min=2,
                delivery_days_max=4, active='on'))
        self.assertEqual(ShippingCountry.objects.get(code='FR').zone_id, france.pk)

    def test_missing_revision_and_repeated_first_submission_do_not_overwrite_policy(self):
        first = self.policy_data(enabled='', free_shipping_enabled='on', free_shipping_threshold='99',
                                 preparation_days_min='', preparation_days_max='')
        missing = dict(first)
        del missing['revision']
        self.assertEqual(self.client.post(self.url('policy'), missing).status_code, 200)
        self.assertFalse(ShippingConfiguration.objects.exists())
        self.assertEqual(self.client.post(self.url('policy'), first).status_code, 302)
        before = self.values()
        self.assertEqual(self.client.post(self.url('policy'), first).status_code, 409)
        self.assertEqual(self.values(), before)


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True)
class ManagedShippingQuoteTests(TestCase):
    def setUp(self):
        self.policy, self.france, self.belgium = create_managed_shipping()

    def test_quote_is_json_snapshot_of_country_amount_days_and_revisions(self):
        result = quote(8000, 'FR')
        self.assertTrue(result['managed'])
        self.assertEqual(result['version'], 1)
        self.assertEqual(result['country'], 'FR')
        self.assertEqual(result['amount'], 600)
        self.assertEqual(result['threshold'], 9900)
        self.assertEqual(result['config_revision'], self.policy.revision)
        self.assertEqual(result['zone_revision'], self.france.revision)
        self.assertEqual(result['delivery_min_days'], 3)
        self.assertEqual(result['delivery_max_days'], 6)
        self.assertEqual(json.loads(json.dumps(result)), result)

    def test_threshold_boundaries_and_destination_eligibility_are_exact_before_codes(self):
        for subtotal, expected in ((9899, 600), (9900, 0), (9901, 0)):
            with self.subTest(subtotal=subtotal):
                self.assertEqual(quote(subtotal, 'FR')['amount'], expected)
                self.assertEqual(quote(subtotal, 'BE')['amount'], 1200)

    def test_disabled_gratuity_keeps_paid_rate_even_above_threshold(self):
        self.policy.free_shipping_enabled = False
        self.policy.save(update_fields=['free_shipping_enabled'])
        self.assertEqual(quote(20000, 'FR')['amount'], 600)

    def test_unserved_or_inactive_destination_and_incomplete_policy_cannot_quote(self):
        for code in ('', 'DE', 'XX'):
            with self.subTest(code=code), self.assertRaises(ShippingUnavailable):
                quote(8000, code)
        self.france.active = False
        self.france.save(update_fields=['active'])
        with self.assertRaises(ShippingUnavailable):
            quote(8000, 'FR')
        self.policy.enabled = False
        self.policy.save(update_fields=['enabled'])
        self.assertFalse(ready())
        with self.assertRaises(ShippingUnavailable):
            quote(8000, 'BE')

    def test_new_carrier_is_not_falsely_inferred_from_a_paid_zone(self):
        self.policy.carrier_name = ''
        self.policy.save(update_fields=['carrier_name'])
        self.assertFalse(ready())
        with self.assertRaises(ShippingUnavailable):
            quote(8000, 'FR')
