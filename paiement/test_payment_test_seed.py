"""Test fixtures must never reset paid-test history or touch the ordinary shop."""
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from avoirs.models import BenefitMail, BenefitRedemption, CustomerBenefit
from avoirs.services import BenefitError, quote as benefit_quote
from boutique.models import ShippingConfiguration, ShippingZone, ShopConfiguration
from boutique.shipping import quote as shipping_quote, ready as shipping_ready
from connexion.models import EmailVerification
from core.models import Category, Product
from espace.models import Address
from stocks.models import Variant
from .management.commands.seed_payment_test import ACCOUNTS
from .models import Order


class PaymentTestSeedTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix='payment-seed-tests-')
        self.addCleanup(self.directory.cleanup)
        self.private = Path(self.directory.name)
        self.key_file = self.private / 'data.key'
        self.key_file.write_bytes(Fernet.generate_key())
        self.settings = override_settings(
            PAYMENT_TEST_DIRECTORY=self.private,
            DATA_ENCRYPTION_KEY_FILE=str(self.key_file),
            PAYMENT_TEST_MODE=False,
            PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
        )
        self.settings.enable()
        self.addCleanup(self.settings.disable)

    def seed(self):
        output = StringIO()
        # The safety helper has its own exhaustive settings/database tests. This
        # mock allows fixtures to be exercised inside Django's disposable test DB.
        with patch('paiement.management.commands.seed_payment_test.assert_isolated_test_environment', return_value=True):
            call_command('seed_payment_test', stdout=output)
        return output.getvalue()

    def test_normal_shop_is_refused_before_any_database_or_credentials_write(self):
        with self.assertNumQueries(0), self.assertRaisesMessage(CommandError, 'base séparée'):
            call_command('seed_payment_test', stdout=StringIO())
        self.assertFalse((self.private / 'accounts.json').exists())

    def test_a_flag_alone_cannot_enable_seeding(self):
        with override_settings(PAYMENT_TEST_MODE=True, PAYMENT_TEST_ISOLATED=False):
            with self.assertNumQueries(0), self.assertRaisesMessage(CommandError, 'environnement de test séparé'):
                call_command('seed_payment_test', stdout=StringIO())
        self.assertFalse((self.private / 'accounts.json').exists())

    def test_seed_creates_private_fictive_accounts_catalogue_and_shipping(self):
        output = self.seed()
        credentials_file = self.private / 'accounts.json'
        credentials = json.loads(credentials_file.read_text())
        self.assertEqual(stat.S_IMODE(credentials_file.stat().st_mode), 0o600)
        self.assertEqual(get_user_model().objects.count(), 2)
        for role, identity in ACCOUNTS.items():
            user = get_user_model().objects.get(username=identity['username'])
            self.assertEqual(user.email, identity['email'])
            self.assertEqual(user.is_superuser, role == 'admin')
            self.assertTrue(user.check_password(credentials[role]['password']))
            self.assertNotIn(credentials[role]['password'], output)
            self.assertEqual(EmailVerification.objects.get(user=user).verified_email, user.email)
        self.assertEqual(Address.objects.count(), 1)
        self.assertEqual(Category.objects.get(slug='essais-paiement').show_in_menu, True)
        self.assertEqual(Product.objects.count(), 3)
        self.assertEqual(set(Product.objects.values_list('price', flat=True)), {
            Decimal('80'), Decimal('35'), Decimal('110'),
        })
        self.assertEqual(Variant.objects.count(), 7)
        self.assertFalse(Variant.objects.exclude(quantity=20, reserved=0).exists())
        for product in Product.objects.all():
            self.assertTrue((Path(__file__).resolve().parents[1] / 'core' / 'static' / product.static_image).is_file())
        self.assertTrue(shipping_ready())
        self.assertEqual(shipping_quote(8000, 'FR')['amount'], 690)
        self.assertEqual(shipping_quote(9900, 'FR')['amount'], 0)
        self.assertEqual(ShopConfiguration.objects.get().business_name, 'BOUTIQUE FICTIVE — ESSAIS')
        self.assertFalse(BenefitMail.objects.exists())
        self.assertFalse(Order.objects.exists())

    def test_codes_are_personal_with_real_discount_and_minimum_validation(self):
        self.seed()
        client = get_user_model().objects.get(username='client-test')
        admin = get_user_model().objects.get(username='evann-test')
        percent = CustomerBenefit.objects.get(code='TEST15')
        credit = CustomerBenefit.objects.get(code='TEST10')
        self.assertEqual(benefit_quote(percent, client, 8000)['discount_amount'], 1200)
        self.assertEqual(benefit_quote(credit, client, 8000)['discount_amount'], 1000)
        with self.assertRaisesMessage(BenefitError, 'au moins 70,00'):
            benefit_quote(percent, client, 3500)
        with self.assertRaises(BenefitError):
            benefit_quote(percent, admin, 8000)
        self.assertTrue(percent.starts_at < timezone.now() < percent.ends_at)

    def test_second_seed_preserves_consumed_codes_stock_prices_and_credentials(self):
        self.seed()
        credentials_before = (self.private / 'accounts.json').read_bytes()
        client = get_user_model().objects.get(username='client-test')
        password_before = client.password
        code = CustomerBenefit.objects.get(code='TEST15')
        original_end = code.ends_at
        order = Order.objects.create(session_key='seed-preservation', user=client,
                                     status='paid', paid_at=timezone.now(), items=[], subtotal=8000)
        redemption = BenefitRedemption.objects.create(benefit=code, order=order,
            state='used', used_at=timezone.now(), discount_amount=1200, snapshot={})
        variant = Variant.objects.first()
        variant.quantity = 14
        variant.save(update_fields=['quantity'])
        product = variant.product
        product.price = Decimal('75.00')
        product.save(update_fields=['price'])
        shipping = ShippingConfiguration.objects.get()
        shipping.free_shipping_threshold = Decimal('125.00')
        shipping.save(update_fields=['free_shipping_threshold'])
        self.seed()
        variant.refresh_from_db()
        product.refresh_from_db()
        code.refresh_from_db()
        redemption.refresh_from_db()
        client.refresh_from_db()
        shipping.refresh_from_db()
        self.assertEqual((variant.quantity, product.price), (14, Decimal('75.00')))
        self.assertEqual((code.status, redemption.state, code.ends_at), ('used', 'used', original_end))
        self.assertEqual(shipping.free_shipping_threshold, Decimal('125.00'))
        self.assertEqual(client.password, password_before)
        self.assertEqual((self.private / 'accounts.json').read_bytes(), credentials_before)
        self.assertEqual((Product.objects.count(), Variant.objects.count(), CustomerBenefit.objects.count()), (3, 7, 2))
        self.assertEqual((ShippingZone.objects.count(), Address.objects.count()), (1, 1))

    def test_existing_account_with_different_recipient_aborts_all_database_changes(self):
        get_user_model().objects.create_user(username='client-test', email='another@example.test')
        with self.assertRaisesMessage(CommandError, 'autre adresse'):
            self.seed()
        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertFalse(Product.objects.exists())
        self.assertFalse(ShopConfiguration.objects.exists())

    def test_nonprivate_credentials_file_is_refused_without_database_changes(self):
        credentials = self.private / 'accounts.json'
        credentials.write_text('{}')
        credentials.chmod(0o644)
        with self.assertNumQueries(0), self.assertRaisesMessage(CommandError, 'droits 600'):
            self.seed()

    def test_credentials_symlink_is_never_followed(self):
        target = self.private / 'other.json'
        target.write_text('do not change')
        (self.private / 'accounts.json').symlink_to(target)
        with self.assertNumQueries(0), self.assertRaisesMessage(CommandError, 'droits 600'):
            self.seed()
        self.assertEqual(target.read_text(), 'do not change')
