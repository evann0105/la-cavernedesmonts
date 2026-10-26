"""HTTP boundaries for the private mail monitor, settings and stock alerts."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from avoirs.mail import enqueue as enqueue_benefit
from avoirs.models import CustomerBenefit, BenefitMail
from connexion.test_helpers import verify_test_user
from core.models import Product
from paiement.models import Order
from retours.mail import enqueue as enqueue_return
from retours.models import ReturnMail
from stocks.models import Variant
from .models import AlertEvent, AlertNotice, AlertSettings, StockWatch
from .services import enqueue_paid_order


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=False,
    EMAIL_DELIVERY_ENABLED=True, EMAIL_TEST_MODE=False,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    SITE_URL='http://testserver', ALLOWED_HOSTS=['testserver'])
class AlertViewsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.customer = User.objects.create_user('alerts-customer', email='customer@example.test')
        self.catalog = User.objects.create_user('alerts-catalog', email='catalog@example.test', is_staff=True)
        self.returns = User.objects.create_user('alerts-returns', email='returns@example.test', is_staff=True)
        self.seller = User.objects.create_user('alerts-seller', email='seller@example.test', is_staff=True)
        catalog_permission = Permission.objects.get(content_type__app_label='core', codename='manage_catalog')
        returns_permission = Permission.objects.get(content_type__app_label='retours', codename='manage_returns')
        self.catalog.user_permissions.add(catalog_permission)
        self.returns.user_permissions.add(returns_permission)
        self.seller.user_permissions.add(catalog_permission, returns_permission)
        for user in (self.customer, self.catalog, self.returns, self.seller):
            verify_test_user(user)
        self.product = Product.objects.create(name='Polaire Écureuil', price=Decimal('80'), is_published=True)
        self.variant = Variant.objects.create(product=self.product, size='M', color='Vert', quantity=5, reserved=3)
        self.order = Order.objects.create(user=self.customer, session_key='alert-http-test', status='paid',
            paid_at=timezone.now(), subtotal=8000, total_paid=8000, items=[], customer_email=self.customer.email)
        enqueue_return(self.order, 'http-order', 'order', 'PRIVATE_BODY https://example.test/?token=PRIVATE_TOKEN')
        self.transactional = ReturnMail.objects.get(key='http-order')

    def login(self, user, client=None):
        (client or self.client).force_login(user, backend='connexion.backends.EmailOrUsernameBackend')

    def detail(self, source, notice):
        return reverse('alertes:email_detail', args=[source, notice.pk])

    def config_data(self, **changes):
        result = {'revision': 0, 'recipient': 'seller@example.test', 'stock_threshold': 3,
                  'order_notifications_enabled': 'on', 'stock_notifications_enabled': ''}
        result.update(changes)
        return result

    def paid_notice(self):
        AlertSettings.objects.get_or_create(pk=1, defaults={
            'recipient': 'seller@example.test', 'order_notifications_enabled': True})
        return enqueue_paid_order(self.order)

    def legacy_notice(self):
        payload = dict(self.transactional.payload)
        payload.pop('recipient', None)
        self.transactional.payload = payload
        self.transactional.save(update_fields=['payload'])
        return self.transactional

    def test_role_matrix_for_lists_settings_and_stock_rules(self):
        urls = [reverse('alertes:emails'), reverse('alertes:settings'),
                reverse('alertes:stocks'), reverse('alertes:stock_rule', args=[self.variant.pk])]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 302)
        for user, expected in ((self.customer, [403, 403, 403, 403]),
                               (self.catalog, [403, 403, 200, 200]),
                               (self.returns, [200, 403, 403, 403]),
                               (self.seller, [200, 200, 200, 200])):
            self.login(user)
            for url, status in zip(urls, expected):
                with self.subTest(user=user.username, url=url):
                    self.assertEqual(self.client.get(url).status_code, status)
        self.assertFalse(AlertSettings.objects.exists())
        self.assertFalse(StockWatch.objects.exists())

    def test_unverified_staff_cannot_read_or_write_alert_pages(self):
        self.seller.email_verification.delete()
        self.login(self.seller)
        for url in (reverse('alertes:emails'), reverse('alertes:settings'), reverse('alertes:stocks')):
            with self.subTest(url=url):
                self.assertRedirects(self.client.get(url), reverse('connexion:verify_email'), fetch_redirect_response=False)
                self.assertEqual(self.client.post(url, self.config_data()).status_code, 302)
        self.assertFalse(AlertSettings.objects.exists())

    def test_returns_only_staff_never_sees_private_seller_recipient(self):
        notice = self.paid_notice()
        self.login(self.returns)
        response = self.client.get(reverse('alertes:emails'), {'etat': 'all'})
        self.assertContains(response, 'customer@example.test')
        self.assertNotContains(response, 'seller@example.test')
        self.assertNotContains(response, reverse('alertes:settings'))
        self.assertEqual(self.client.get(self.detail('alert', notice)).status_code, 404)
        self.assertEqual(self.client.post(self.detail('alert', notice), {'attempts': 0}).status_code, 404)
        self.assertEqual(len(mail.outbox), 0)

    def test_gets_are_private_read_only_and_do_not_expose_message_secrets(self):
        benefit = CustomerBenefit.objects.create(user=self.customer, bound_user_id=self.customer.pk,
            recipient_email=self.customer.email, label='Geste commercial', value=Decimal('15'))
        benefit_notice = enqueue_benefit(benefit)
        self.login(self.seller)
        before = (list(ReturnMail.objects.values()), list(BenefitMail.objects.values()))
        urls = [reverse('alertes:emails') + '?etat=all', reverse('alertes:settings'),
                reverse('alertes:stocks'), reverse('alertes:stock_rule', args=[self.variant.pk]),
                self.detail('return', self.transactional), self.detail('benefit', benefit_notice)]
        for url in urls:
            response = self.client.get(url)
            with self.subTest(url=url):
                self.assertEqual(response.status_code, 200)
                self.assertIn('no-store', response['Cache-Control'])
                self.assertIn('private', response['Cache-Control'])
                self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')
                for secret in ('PRIVATE_BODY', 'PRIVATE_TOKEN', benefit.code):
                    self.assertNotContains(response, secret)
        self.assertEqual(before, (list(ReturnMail.objects.values()), list(BenefitMail.objects.values())))
        for model in (AlertSettings, AlertNotice, AlertEvent, StockWatch):
            self.assertFalse(model.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_account_navigation_follows_staff_permissions(self):
        for user, email_link, stock_link in ((self.customer, False, False), (self.catalog, False, True),
                                           (self.returns, True, False), (self.seller, True, True)):
            self.login(user)
            response = self.client.get('/mon-compte/')
            self.assertEqual(response.status_code, 200)
            for url, expected in ((reverse('alertes:emails'), email_link), (reverse('alertes:stocks'), stock_link)):
                with self.subTest(user=user.username, url=url):
                    (self.assertContains if expected else self.assertNotContains)(response, 'href="' + url + '"')

    def test_settings_invalid_recipient_and_threshold_do_not_write(self):
        self.login(self.seller)
        url = reverse('alertes:settings')
        for changes in ({'recipient': ''}, {'recipient': 'incorrect'}, {'stock_threshold': -1},
                        {'stock_threshold': 100000}, {'stock_threshold': '1.5'}, {'revision': -1}):
            with self.subTest(changes=changes):
                self.assertEqual(self.client.post(url, self.config_data(**changes)).status_code, 400)
        self.assertFalse(AlertSettings.objects.exists())
        self.assertFalse(AlertEvent.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_settings_save_normalizes_recipient_and_rejects_stale_form(self):
        self.login(self.seller)
        url = reverse('alertes:settings')
        self.assertRedirects(self.client.post(url, self.config_data(recipient=' SELLER@EXAMPLE.TEST ', stock_threshold=0)), url)
        config = AlertSettings.objects.get(pk=1)
        self.assertEqual((config.recipient, config.stock_threshold, config.revision, config.stock_generation),
                         ('seller@example.test', 0, 1, 1))
        self.assertEqual(config.updated_by_id, self.seller.pk)
        self.assertEqual(AlertEvent.objects.filter(kind='settings_changed').count(), 1)
        self.assertEqual(self.client.post(url, self.config_data(stock_threshold=9)).status_code, 409)
        config.refresh_from_db()
        self.assertEqual((config.stock_threshold, config.revision), (0, 1))
        self.assertEqual(AlertEvent.objects.count(), 1)
        self.assertFalse(AlertNotice.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_unchanged_settings_and_order_toggle_preserve_stock_generation(self):
        self.login(self.seller)
        url = reverse('alertes:settings')
        self.client.post(url, self.config_data())
        self.assertEqual(self.client.post(url, self.config_data(revision=1)).status_code, 302)
        self.assertEqual(AlertEvent.objects.count(), 1)
        self.assertEqual(self.client.post(url, self.config_data(revision=1, order_notifications_enabled='')).status_code, 302)
        config = AlertSettings.objects.get(pk=1)
        self.assertEqual((config.revision, config.stock_generation), (2, 1))
        self.assertEqual(self.client.post(url, self.config_data(revision=2, stock_threshold=99999)).status_code, 302)
        config.refresh_from_db()
        self.assertEqual((config.revision, config.stock_generation, config.stock_threshold), (3, 2, 99999))

    def test_settings_post_requires_both_roles(self):
        for user in (self.customer, self.catalog, self.returns):
            self.login(user)
            with self.subTest(user=user.username):
                self.assertEqual(self.client.post(reverse('alertes:settings'), self.config_data()).status_code, 403)
        self.assertFalse(AlertSettings.objects.exists())

    def test_stock_rule_changes_only_alert_preferences_with_revision_guard(self):
        self.login(self.catalog)
        url = reverse('alertes:stock_rule', args=[self.variant.pk])
        for threshold in (-1, 100000, '2.5'):
            self.assertEqual(self.client.post(url, {'revision': 1, 'threshold': threshold, 'enabled': 'on'}).status_code, 400)
        self.assertFalse(StockWatch.objects.exists())
        self.assertRedirects(self.client.post(url, {'revision': 1, 'threshold': 0, 'enabled': 'on'}), reverse('alertes:stocks'))
        watch = StockWatch.objects.get(variant=self.variant)
        self.assertEqual((watch.threshold, watch.revision, watch.enabled), (0, 2, True))
        self.assertEqual(self.client.post(url, {'revision': 1, 'threshold': 20}).status_code, 409)
        watch.refresh_from_db(); self.variant.refresh_from_db()
        self.assertEqual((watch.threshold, watch.revision, watch.enabled), (0, 2, True))
        self.assertEqual((self.variant.quantity, self.variant.reserved), (5, 3))
        self.assertEqual(self.client.post(url, {'revision': 2, 'threshold': ''}).status_code, 302)
        watch.refresh_from_db()
        self.assertIsNone(watch.threshold)
        self.assertFalse(watch.enabled)

    def test_stock_list_distinguishes_available_unknown_and_disabled(self):
        unknown = Variant.objects.create(product=self.product, size='L', color='Rouge', quantity=None)
        disabled = Variant.objects.create(product=self.product, size='XL', color='Bleu', quantity=0)
        StockWatch.objects.create(variant=disabled, enabled=False)
        self.login(self.catalog)
        url = reverse('alertes:stocks')
        self.assertEqual([row['variant_id'] for row in self.client.get(url).context['page_obj']], [self.variant.pk])
        response = self.client.get(url, {'etat': 'unknown'})
        self.assertEqual([row['variant_id'] for row in response.context['page_obj']], [unknown.pk])
        response = self.client.get(url, {'etat': 'disabled'})
        self.assertEqual([row['variant_id'] for row in response.context['page_obj']], [disabled.pk])
        response = self.client.get(url, {'etat': 'all', 'q': 'ecureuil vert'})
        self.assertEqual([row['variant_id'] for row in response.context['page_obj']], [self.variant.pk])
        self.assertEqual(response.context['page_obj'][0]['available'], 2)

    def test_exact_seller_retry_and_double_submit_send_once(self):
        notice = self.paid_notice()
        self.login(self.seller)
        url = self.detail('alert', notice)
        self.assertEqual(self.client.post(url, {'attempts': 0}).status_code, 302)
        self.assertEqual(self.client.post(url, {'attempts': 0}).status_code, 409)
        notice.refresh_from_db(); self.transactional.refresh_from_db()
        self.assertEqual(notice.attempts, 1)
        self.assertIsNotNone(notice.sent_at)
        self.assertIsNone(self.transactional.sent_at)
        self.assertEqual(self.transactional.attempts, 0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['seller@example.test'])
        self.assertEqual(AlertEvent.objects.filter(kind='mail_retry').count(), 1)

    def test_products_without_variants_are_counted_and_link_to_stock_entry(self):
        missing = Product.objects.create(name='Bonnet sans stock', price=Decimal('15'), is_published=True)
        Product.objects.create(name='Brouillon sans stock', price=Decimal('15'), is_published=False)
        Product.objects.create(name='Doublon sans stock', price=Decimal('15'), is_published=True, duplicate_of=missing)
        Variant.objects.create(product=self.product, size='L', color='Rouge', quantity=None)
        self.login(self.catalog)
        url = reverse('alertes:stocks')
        low = self.client.get(url)
        self.assertEqual(low.context['to_count'], 2)
        self.assertEqual(low.context['snapshot']['missing_products_count'], 1)
        self.assertFalse(any(row.get('missing_stock') for row in low.context['page_obj']))
        for state in ('unknown', 'all'):
            response = self.client.get(url, {'etat': state})
            missing_rows = [row for row in response.context['page_obj'] if row.get('missing_stock')]
            self.assertEqual([row['title'] for row in missing_rows], [missing.name])
            self.assertContains(response, 'href="' + reverse('stocks:product', args=[missing.pk]) + '"')
            self.assertNotContains(response, 'Brouillon sans stock')
            self.assertNotContains(response, 'Doublon sans stock')
        filtered = self.client.get(url, {'etat': 'unknown', 'q': 'écureuil'})
        self.assertFalse(any(row.get('missing_stock') for row in filtered.context['page_obj']))
        self.assertFalse(StockWatch.objects.exists())

    def test_exact_legacy_transactional_retry_does_not_send_other_messages(self):
        notice = self.paid_notice()
        self.login(self.returns)
        url = self.detail('return', self.transactional)
        self.assertEqual(self.client.post(url, {'attempts': 0}).status_code, 302)
        self.assertEqual(self.client.post(url, {'attempts': 0}).status_code, 409)
        notice.refresh_from_db()
        self.assertIsNone(notice.sent_at)
        self.assertEqual(notice.attempts, 0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.customer.email])

    def test_malformed_or_stale_attempts_do_not_send(self):
        notice = self.paid_notice()
        self.login(self.seller)
        for attempts in ('', -1, 9, 'invalid'):
            with self.subTest(attempts=attempts):
                self.assertEqual(self.client.post(self.detail('alert', notice), {'attempts': attempts}).status_code, 409)
        notice.refresh_from_db()
        self.assertEqual(notice.attempts, 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_changed_seller_recipient_blocks_existing_notice(self):
        notice = self.paid_notice()
        AlertSettings.objects.filter(pk=1).update(recipient='new@example.test')
        self.login(self.seller)
        url = self.detail('alert', notice)
        response = self.client.get(url)
        self.assertEqual(response.context['row']['status'], 'blocked')
        self.assertFalse(response.context['row']['can_retry'])
        self.assertEqual(self.client.post(url, {'attempts': 0}).status_code, 409)
        self.assertEqual(len(mail.outbox), 0)

    def test_failed_retry_is_reported_without_leaking_provider_error(self):
        notice = self.paid_notice()
        self.login(self.seller)
        url = self.detail('alert', notice)
        with patch('alertes.mail.EmailMultiAlternatives.send', side_effect=RuntimeError('SMTP_PRIVATE_PASSWORD')):
            response = self.client.post(url, {'attempts': 0}, follow=True)
        self.assertEqual(response.context['row']['status'], 'failed')
        self.assertContains(response, 'La tentative n’a pas abouti')
        self.assertNotContains(response, 'SMTP_PRIVATE_PASSWORD')
        notice.refresh_from_db()
        self.assertEqual(notice.attempts, 1)
        self.assertIsNone(notice.sent_at)

    @override_settings(EMAIL_TEST_MODE=True)
    def test_local_delivery_is_explicit_and_cannot_be_resent(self):
        notice = self.paid_notice()
        self.login(self.seller)
        url = self.detail('alert', notice)
        response = self.client.post(url, {'attempts': 0}, follow=True)
        self.assertEqual(response.context['row']['status'], 'sent_local')
        self.assertContains(response, 'Aucun envoi à une adresse réelle')
        with override_settings(EMAIL_TEST_MODE=False):
            self.assertEqual(self.client.post(url, {'attempts': 1}).status_code, 409)
        self.assertEqual(len(mail.outbox), 1)

    def test_old_message_requires_checkbox_and_valid_snapshot_token(self):
        notice = self.legacy_notice()
        self.login(self.returns)
        url = self.detail('return', notice)
        response = self.client.get(url)
        row = response.context['row']
        self.assertTrue(row['requires_recipient_confirmation'])
        token = row['recipient_confirmation_token']
        self.assertContains(response, 'name="confirm_recipient"')
        self.assertEqual(self.client.post(url, {'attempts': 0, 'recipient_confirmation_token': token}).status_code, 409)
        self.assertEqual(self.client.post(url, {'attempts': 0, 'confirm_recipient': 'yes',
                                              'recipient_confirmation_token': 'tampered'}).status_code, 409)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(self.client.post(url, {'attempts': 0, 'confirm_recipient': 'yes',
                                              'recipient_confirmation_token': token}).status_code, 302)
        notice.refresh_from_db()
        self.assertEqual(notice.payload['recipient'], self.customer.email)
        self.assertEqual(notice.payload['recipient_confirmed_by'], self.returns.pk)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.customer.email])

    def test_old_recipient_confirmation_rejects_address_changed_after_page_load(self):
        notice = self.legacy_notice()
        self.login(self.returns)
        url = self.detail('return', notice)
        token = self.client.get(url).context['row']['recipient_confirmation_token']
        Order.objects.filter(pk=self.order.pk).update(customer_email='changed@example.test')
        response = self.client.post(url, {'attempts': 0, 'confirm_recipient': 'yes',
                                         'recipient_confirmation_token': token})
        self.assertEqual(response.status_code, 409)
        notice.refresh_from_db()
        self.assertNotIn('recipient', notice.payload)
        self.assertEqual(notice.attempts, 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_all_mutation_endpoints_require_csrf(self):
        notice = self.paid_notice()
        protected = Client(enforce_csrf_checks=True)
        self.login(self.seller, protected)
        for url, data in ((reverse('alertes:settings'), self.config_data(revision=1)),
                          (reverse('alertes:stock_rule', args=[self.variant.pk]), {'revision': 1, 'threshold': 0}),
                          (self.detail('alert', notice), {'attempts': 0}),
                          (self.detail('return', self.transactional), {'attempts': 0})):
            with self.subTest(url=url):
                self.assertEqual(protected.post(url, data).status_code, 403)
        self.assertFalse(StockWatch.objects.exists())
        self.assertFalse(AlertEvent.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_get_only_endpoints_reject_post(self):
        self.login(self.seller)
        for name in ('alertes:emails', 'alertes:stocks'):
            self.assertEqual(self.client.post(reverse(name), {}).status_code, 405)
