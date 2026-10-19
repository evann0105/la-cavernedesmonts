from decimal import Decimal

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from connexion.test_helpers import verify_test_user
from core.models import Category, Product
from stocks.forms import InitialVariantForm, VariantForm
from stocks.models import Variant


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class AdminVariantPriceTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user('price-manager', is_staff=True)
        self.manager.groups.add(Group.objects.get(name='Gestion du catalogue'))
        verify_test_user(self.manager)
        self.client.force_login(self.manager, backend='connexion.backends.EmailOrUsernameBackend')
        self.category = Category.objects.create(name='Prix tests', slug='prix-tests')
        self.product = Product.objects.create(name='Polaire tarifs', price='30.00', category=self.category, description='Une polaire.', static_image='core/img/slide1.png', main_color='Vert', is_published=False)
        self.variant = Variant.objects.create(product=self.product, size='Junior', color='Vert', quantity=8, reserved=2, price='20.00')
        self.product.refresh_from_db()
        self.variant.refresh_from_db()

    def data(self, **overrides):
        values = {
            'name': self.product.name, 'price': '30.00', 'description': self.product.description,
            'category': self.category.pk, 'sizes': '',
            'photos-TOTAL_FORMS': '0', 'photos-INITIAL_FORMS': '0',
            'colors-TOTAL_FORMS': '0', 'colors-INITIAL_FORMS': '0',
            'stock-TOTAL_FORMS': '0', 'stock-INITIAL_FORMS': '0',
        }
        values.update(overrides)
        return values

    def prices(self, variant_price, **overrides):
        return self.data(**{
            'variant_prices-TOTAL_FORMS': '1', 'variant_prices-INITIAL_FORMS': '1',
            'variant_prices-0-variant_id': str(self.variant.pk),
            'variant_prices-0-revision': str(self.variant.revision),
            'variant_prices-0-price': variant_price, **overrides,
        })

    def edit(self, values):
        return self.client.post(reverse('core:catalog_edit', args=[self.product.pk]), values)

    def test_effective_price_uses_override_and_falls_back_on_general_price(self):
        self.assertEqual(self.variant.effective_price, Decimal('20.00'))
        self.variant.price = None
        self.assertEqual(self.variant.effective_price, Decimal('30.00'))

    def test_zero_and_negative_variant_prices_are_rejected_by_database(self):
        for price in ['0.00', '-1.00']:
            with self.subTest(price=price), self.assertRaises(IntegrityError), transaction.atomic():
                Variant.objects.create(product=self.product, size=price, color='Bleu', quantity=1, price=price)

    def test_initial_price_validation_accepts_localized_decimal_and_rejects_zero(self):
        good = InitialVariantForm({'size': 'M', 'color': 'Vert', 'quantity': '2', 'price': '19,90'})
        self.assertTrue(good.is_valid(), good.errors)
        self.assertEqual(good.cleaned_data['price'], Decimal('19.90'))
        bad = InitialVariantForm({'size': 'M', 'color': 'Vert', 'quantity': '2', 'price': '0'})
        self.assertFalse(bad.is_valid())
        self.assertIn('price', bad.errors)

    def test_price_only_edit_preserves_quantities_and_updates_history(self):
        response = self.edit(self.prices('17,50'))
        self.assertEqual(response.status_code, 302)
        self.variant.refresh_from_db()
        self.assertEqual((self.variant.price, self.variant.quantity, self.variant.reserved, self.variant.revision), (Decimal('17.50'), 8, 2, 1))
        self.assertTrue(LogEntry.objects.filter(object_id=str(self.variant.pk), change_message__contains='20.00 € → 17.50 €').exists())

    def test_blank_price_explicitly_restores_general_price(self):
        response = self.edit(self.prices(''))
        self.assertEqual(response.status_code, 302)
        self.variant.refresh_from_db()
        self.assertIsNone(self.variant.price)
        self.assertEqual(self.variant.effective_price, Decimal('30.00'))

    def test_legacy_product_post_preserves_existing_price(self):
        response = self.edit(self.data(name='Polaire conservée'))
        self.assertEqual(response.status_code, 302)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.price, Decimal('20.00'))

    def test_missing_price_key_in_existing_form_preserves_price(self):
        values = self.prices('')
        del values['variant_prices-0-price']
        response = self.edit(values)
        self.assertEqual(response.status_code, 302)
        self.variant.refresh_from_db()
        self.assertEqual((self.variant.price, self.variant.revision), (Decimal('20.00'), 0))

    def test_legacy_stock_variant_post_preserves_existing_price(self):
        response = self.client.post(reverse('stocks:edit', args=[self.product.pk, self.variant.pk]), {
            'size': self.variant.size, 'color': self.variant.color, 'reference': '',
            'quantity': '8', 'revision': '0',
        })
        self.assertEqual(response.status_code, 302)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.price, Decimal('20.00'))

    def test_stock_variant_editor_can_set_independent_price(self):
        form = VariantForm({'size': 'Junior', 'color': 'Vert', 'reference': '', 'quantity': '8', 'revision': '0', 'price': '18,90'}, instance=self.variant)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['price'], Decimal('18.90'))

    def test_invalid_price_rejects_product_and_variant_changes_atomically(self):
        response = self.edit(self.prices('0', price='99.00', name='Changement refusé'))
        self.assertEqual(response.status_code, 200)
        self.product.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual((self.product.name, self.product.price, self.variant.price), ('Polaire tarifs', Decimal('30.00'), Decimal('20.00')))

    def test_stale_revision_rejects_all_edits(self):
        Variant.objects.filter(pk=self.variant.pk).update(revision=1)
        response = self.edit(self.prices('19', price='99.00'))
        self.assertEqual(response.status_code, 200)
        self.product.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual((self.product.price, self.variant.price, self.variant.revision), (Decimal('30.00'), Decimal('20.00'), 1))

    def test_foreign_variant_identifier_cannot_be_modified(self):
        other = Product.objects.create(name='Autre article', price='50.00')
        foreign = Variant.objects.create(product=other, size='M', color='Vert', quantity=1, price='45.00')
        response = self.edit(self.prices('1', **{'variant_prices-0-variant_id': str(foreign.pk)}))
        self.assertEqual(response.status_code, 200)
        foreign.refresh_from_db()
        self.assertEqual(foreign.price, Decimal('45.00'))

    def test_duplicate_identifier_is_rejected(self):
        values = self.prices('19', **{
            'variant_prices-TOTAL_FORMS': '2', 'variant_prices-INITIAL_FORMS': '2',
            'variant_prices-1-variant_id': str(self.variant.pk),
            'variant_prices-1-revision': '0', 'variant_prices-1-price': '18',
        })
        response = self.edit(values)
        self.assertEqual(response.status_code, 200)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.price, Decimal('20.00'))

    def test_unused_extra_price_form_does_not_interrupt_saving(self):
        response = self.edit(self.prices('19', **{'variant_prices-TOTAL_FORMS': '2'}))
        self.assertEqual(response.status_code, 302)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.price, Decimal('19.00'))

    def test_color_price_is_backend_fallback_for_new_size_only(self):
        response = self.edit(self.data(**{
            'original-name': 'Vert', 'original-color_price': '24,90',
            'stock-TOTAL_FORMS': '1', 'stock-0-size': 'Ado',
            'stock-0-color': 'Vert', 'stock-0-quantity': '3', 'stock-0-price': '',
        }))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.product.stock_variants.get(size='Ado').price, Decimal('24.90'))
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.price, Decimal('20.00'))

    def test_individual_size_price_overrides_color_prefill(self):
        response = self.edit(self.data(**{
            'original-name': 'Vert', 'original-color_price': '24,90',
            'stock-TOTAL_FORMS': '2', 'stock-0-size': 'Ado',
            'stock-0-color': 'Vert', 'stock-0-quantity': '3', 'stock-0-price': '23,50',
            'stock-1-size': 'M', 'stock-1-color': 'Rouge', 'stock-1-quantity': '4', 'stock-1-price': '',
        }))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.product.stock_variants.get(size='Ado').price, Decimal('23.50'))
        self.assertIsNone(self.product.stock_variants.get(size='M').price)

    def test_edit_form_includes_existing_price_under_color_and_inherits_general_when_blank(self):
        response = self.client.get(reverse('core:catalog_edit', args=[self.product.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Prix pour ce coloris (€)')
        self.assertContains(response, f'name="variant_prices-0-variant_id" value="{self.variant.pk}"')
        self.assertContains(response, 'name="variant_prices-0-price"')
        self.assertEqual(len(response.context['original_color_form'].price_forms), 1)
