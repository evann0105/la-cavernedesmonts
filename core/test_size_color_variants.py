"""Size and color choices are real stock pairs, never a Cartesian product."""
import tempfile
from io import BytesIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from connexion.test_helpers import verify_test_user
from paiement.models import Order
from stocks.models import Reservation, Variant
from stocks.reservations import StockUnavailable, reserve
from .models import Category, Product, ProductColor


def color_photo(name='variant.png'):
    stream = BytesIO()
    Image.new('RGB', (40, 50), 'red').save(stream, 'PNG')
    return SimpleUploadedFile(name, stream.getvalue(), content_type='image/png')


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class SizeColorVariantTests(TestCase):
    def setUp(self):
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        media_override = override_settings(MEDIA_ROOT=media.name)
        media_override.enable()
        self.addCleanup(media_override.disable)
        self.manager = get_user_model().objects.create_user('variant-manager', is_staff=True)
        self.manager.groups.add(Group.objects.get(name='Gestion du catalogue'))
        verify_test_user(self.manager)
        self.client.force_login(self.manager, backend='connexion.backends.EmailOrUsernameBackend')
        self.category = Category.objects.create(name='Déclinaisons', slug='declinaisons-test')
        self.product = Product.objects.create(
            name='Polaire à déclinaisons', price='30.00', category=self.category,
            description='Une polaire à déclinaisons.', sizes='S, M, L, XL',
            static_image='core/img/slide1.png',
        )

    def product_data(self, **overrides):
        data = {
            'name': 'Polaire à déclinaisons', 'price': '30.00',
            'description': 'Une polaire à déclinaisons.', 'category': self.category.pk,
            'sizes': 'S, M, L, XL', 'is_published': 'on',
            'include_imported_gallery': 'on',
            'photos-TOTAL_FORMS': '0', 'photos-INITIAL_FORMS': '0',
            'photos-MIN_NUM_FORMS': '0', 'photos-MAX_NUM_FORMS': '50',
            'colors-TOTAL_FORMS': '0', 'colors-INITIAL_FORMS': '0',
            'colors-MIN_NUM_FORMS': '0', 'colors-MAX_NUM_FORMS': '30',
            'stock-TOTAL_FORMS': '3', 'stock-INITIAL_FORMS': '0',
            'stock-MIN_NUM_FORMS': '0', 'stock-MAX_NUM_FORMS': '50',
            'stock-0-size': 'M', 'stock-0-color': 'Rouge', 'stock-0-quantity': '5',
            'stock-1-size': 'M', 'stock-1-color': 'Noir', 'stock-1-quantity': '2',
            'stock-2-size': 'L', 'stock-2-color': 'Bleu', 'stock-2-quantity': '3',
        }
        data.update(overrides)
        return data

    def detail_url(self):
        return reverse('core:product_detail', args=[self.product.slug])

    def cart_url(self):
        return reverse('core:cart_add', args=[self.product.slug])

    def seed_pairs(self):
        return [
            Variant.objects.create(product=self.product, size='M', color='Rouge', quantity=5),
            Variant.objects.create(product=self.product, size='M', color='Noir', quantity=2),
            Variant.objects.create(product=self.product, size='L', color='Bleu', quantity=3),
        ]

    def test_creation_saves_only_explicit_size_color_pairs(self):
        response = self.client.post(reverse('core:catalog_add'), self.product_data(
            name='Trois paires exactement', main_image=color_photo(),
        ))
        self.assertEqual(response.status_code, 302)
        product = Product.objects.get(name='Trois paires exactement')
        self.assertRedirects(response, reverse('core:product_detail', args=[product.slug]))
        self.assertEqual(
            set(product.stock_variants.values_list('size', 'color', 'quantity')),
            {('M', 'Rouge', 5), ('M', 'Noir', 2), ('L', 'Bleu', 3)},
        )
        self.assertFalse(product.stock_variants.filter(size='L', color='Rouge').exists())
        self.assertFalse(product.stock_variants.filter(size='M', color='Bleu').exists())

    def test_edit_adds_explicit_pairs_without_replacing_existing_stock(self):
        previous = Variant.objects.create(
            product=self.product, size='S', color='Vert', quantity=7, reserved=2,
        )
        response = self.client.post(
            reverse('core:catalog_edit', args=[self.product.pk]), self.product_data(),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.product.stock_variants.count(), 4)
        self.assertEqual(
            set(self.product.stock_variants.exclude(pk=previous.pk)
                .values_list('size', 'color', 'quantity')),
            {('M', 'Rouge', 5), ('M', 'Noir', 2), ('L', 'Bleu', 3)},
        )
        previous.refresh_from_db()
        self.assertEqual((previous.quantity, previous.reserved), (7, 2))

    def test_duplicate_or_invalid_pair_rejects_the_whole_creation(self):
        for changes in [
            {'stock-2-size': ' m ', 'stock-2-color': ' ROUGE '},
            {'stock-2-quantity': '-1'},
        ]:
            with self.subTest(changes=changes):
                response = self.client.post(reverse('core:catalog_add'), self.product_data(
                    name='Paires invalides', is_published='', **changes,
                ))
                self.assertEqual(response.status_code, 200)
                self.assertFalse(Product.objects.filter(name='Paires invalides').exists())
                self.assertFalse(Variant.objects.exists())

    def test_existing_pair_duplicate_rejects_new_pairs_and_product_edits(self):
        existing = Variant.objects.create(
            product=self.product, size='M', color='Rouge', quantity=5,
        )
        response = self.client.post(
            reverse('core:catalog_edit', args=[self.product.pk]),
            self.product_data(price='99.00', **{'stock-0-size': ' m ', 'stock-0-color': ' ROUGE '}),
        )
        self.assertEqual(response.status_code, 200)
        self.product.refresh_from_db()
        existing.refresh_from_db()
        self.assertEqual(str(self.product.price), '30.00')
        self.assertEqual(existing.quantity, 5)
        self.assertEqual(self.product.stock_variants.count(), 1)

    def test_public_size_choices_come_from_actual_pairs_not_legacy_sizes(self):
        pairs = self.seed_pairs()
        response = Client().get(self.detail_url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context['product_sizes'],
            [{'key': 'l', 'name': 'L', 'available': True},
             {'key': 'm', 'name': 'M', 'available': True}],
        )
        self.assertEqual(
            {(item.pk, item.size_key, item.color_key) for item in response.context['variants']},
            {(pairs[0].pk, 'm', 'rouge'), (pairs[1].pk, 'm', 'noir'),
             (pairs[2].pk, 'l', 'bleu')},
        )
        self.assertFalse(self.product.stock_variants.filter(size__in=['S', 'XL']).exists())

    def test_unavailable_sizes_include_zero_unknown_and_fully_reserved_stock(self):
        Variant.objects.create(product=self.product, size='M', color='Rouge', quantity=0)
        Variant.objects.create(product=self.product, size='L', color='Bleu', quantity=None)
        Variant.objects.create(product=self.product, size='S', color='Noir', quantity=2, reserved=2)
        Variant.objects.create(product=self.product, size='XL', color='Vert', quantity=3, reserved=2)
        response = Client().get(self.detail_url())
        self.assertEqual(
            {row['name']: row['available'] for row in response.context['product_sizes']},
            {'M': False, 'L': False, 'S': False, 'XL': True},
        )
        self.assertTrue(response.context['has_available_variant'])

    def test_color_photo_matches_normalized_color_across_sizes(self):
        Variant.objects.create(product=self.product, size=' M ', color=' Rouge   vif ', quantity=1)
        Variant.objects.create(product=self.product, size='L', color='rouge vif', quantity=2)
        photo = ProductColor.objects.create(
            product=self.product, name='ROUGE VIF', image=color_photo('rouge-vif.png'),
        )
        response = Client().get(self.detail_url())
        self.assertEqual(
            response.context['product_colors'],
            [{'key': 'rouge vif', 'name': 'ROUGE VIF', 'image': photo.image.url}],
        )
        self.assertEqual({variant.color_key for variant in response.context['variants']}, {'rouge vif'})
        self.assertEqual({variant.size_key for variant in response.context['variants']}, {'m', 'l'})

    def test_pair_requires_explicit_owned_variant_and_ignores_forged_labels(self):
        red, _, _ = self.seed_pairs()
        other = Product.objects.create(name='Autre produit', price=30)
        foreign = Variant.objects.create(product=other, size='L', color='Rouge', quantity=5)
        buyer = Client()
        for payload in [
            {'size': 'L', 'color': 'Rouge'},
            {'variant': foreign.pk},
            {'variant': foreign.pk + 10000},
        ]:
            with self.subTest(payload=payload):
                self.assertEqual(buyer.post(self.cart_url(), payload).status_code, 302)
                self.assertFalse(buyer.session.get('cart'))
        buyer.post(self.cart_url(), {
            'variant': red.pk, 'size': 'L', 'color': 'Bleu', 'quantity': '2',
        })
        self.assertEqual(buyer.session['cart'], {
            f'variant:{red.pk}': {
                'product': self.product.pk, 'variant': red.pk,
                'size': 'M', 'color': 'Rouge', 'quantity': 2,
            },
        })

    def test_no_other_color_can_make_an_unavailable_pair_orderable(self):
        red, black, blue = self.seed_pairs()
        red.quantity = 0
        red.save(update_fields=['quantity'])
        buyer = Client()
        buyer.post(self.cart_url(), {'variant': red.pk, 'quantity': 1})
        self.assertFalse(buyer.session.get('cart'))
        buyer.post(self.cart_url(), {'variant': blue.pk, 'quantity': 1})
        self.assertEqual(set(buyer.session['cart']), {f'variant:{blue.pk}'})
        black.refresh_from_db()
        self.assertEqual((black.quantity, black.reserved), (2, 0))

    def test_checkout_reserves_only_the_exact_pair_and_rejects_forged_pair_snapshot(self):
        red, black, blue = self.seed_pairs()
        snapshot = [{
            'variant': red.pk, 'product': self.product.pk, 'name': self.product.name,
            'size': 'M', 'color': 'Rouge', 'unit_amount': 3000, 'quantity': 2,
        }]
        order = reserve('actual-pair', None, snapshot)
        for variant, expected in [(red, (5, 2)), (black, (2, 0)), (blue, (3, 0))]:
            variant.refresh_from_db()
            self.assertEqual((variant.quantity, variant.reserved), expected)
        self.assertEqual(order.reservations.get().variant_id, red.pk)
        forged = [dict(snapshot[0], size='L')]
        with self.assertRaises(StockUnavailable):
            reserve('forged-pair', None, forged)
        red.refresh_from_db()
        self.assertEqual(red.reserved, 2)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(Reservation.objects.count(), 1)

    def test_color_photo_alone_never_creates_stock_for_legacy_sizes(self):
        response = self.client.post(reverse('core:catalog_add'), self.product_data(
            name='Photo rouge sans stock', main_image=color_photo(), **{
                'stock-TOTAL_FORMS': '0',
                'colors-TOTAL_FORMS': '1', 'colors-0-name': 'Rouge',
                'colors-0-image': color_photo('rouge.png'),
            },
        ))
        self.assertEqual(response.status_code, 302)
        product = Product.objects.get(name='Photo rouge sans stock')
        self.assertEqual(list(product.color_photos.values_list('name', flat=True)), ['Rouge'])
        self.assertFalse(product.stock_variants.exists())

    def test_color_cards_save_their_own_sizes_without_cross_color_stock(self):
        response = self.client.post(reverse('core:catalog_add'), self.product_data(
            name='Cartes rouge et bleu', main_image=color_photo(), **{
                'stock-TOTAL_FORMS': '2',
                'stock-0-size': 'M', 'stock-0-color': 'Rouge', 'stock-0-quantity': '5',
                'stock-1-size': 'L', 'stock-1-color': 'Bleu', 'stock-1-quantity': '3',
                'colors-TOTAL_FORMS': '2',
                'colors-0-name': 'Rouge', 'colors-0-image': color_photo('rouge.png'),
                'colors-1-name': 'Bleu', 'colors-1-image': color_photo('bleu.png'),
            },
        ))
        self.assertEqual(response.status_code, 302)
        product = Product.objects.get(name='Cartes rouge et bleu')
        self.assertEqual(set(product.color_photos.values_list('name', flat=True)), {'Rouge', 'Bleu'})
        self.assertEqual(
            set(product.stock_variants.values_list('size', 'color', 'quantity')),
            {('M', 'Rouge', 5), ('L', 'Bleu', 3)},
        )

    def test_existing_pair_with_repeated_spaces_blocks_normalized_duplicate_atomically(self):
        variant = Variant.objects.create(
            product=self.product, size=' 6   mois ', color=' Rouge   vif ',
            quantity=4, reserved=1, revision=7,
        )
        response = self.client.post(
            reverse('core:catalog_edit', args=[self.product.pk]),
            self.product_data(price='99.00', **{
                'stock-TOTAL_FORMS': '2',
                'stock-0-size': '6 mois', 'stock-0-color': 'rouge vif',
                'stock-1-size': 'L', 'stock-1-color': 'Bleu',
            }),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'existent déjà')
        self.product.refresh_from_db()
        variant.refresh_from_db()
        self.assertEqual(str(self.product.price), '30.00')
        self.assertEqual(self.product.stock_variants.count(), 1)
        self.assertEqual((variant.quantity, variant.reserved, variant.revision), (4, 1, 7))

    def test_removing_photo_preserves_all_existing_size_color_stock(self):
        pairs = self.seed_pairs()
        photo = ProductColor.objects.create(product=self.product, name='Rouge', image=color_photo())
        before = list(self.product.stock_variants.values_list('pk', 'size', 'color', 'quantity', 'reserved'))
        response = self.client.post(
            reverse('core:catalog_edit', args=[self.product.pk]),
            self.product_data(**{
                'stock-TOTAL_FORMS': '0', 'colors-TOTAL_FORMS': '1',
                'colors-INITIAL_FORMS': '1', 'colors-0-id': photo.pk,
                'colors-0-product': self.product.pk, 'colors-0-name': 'Rouge',
                'colors-0-DELETE': 'on',
            }),
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.product.color_photos.exists())
        self.assertEqual(
            list(self.product.stock_variants.values_list('pk', 'size', 'color', 'quantity', 'reserved')),
            before,
        )
        page = Client().get(self.detail_url())
        self.assertEqual({variant.pk for variant in page.context['variants']}, {pair.pk for pair in pairs})

    def test_renaming_photo_preserves_stock_identity_and_historical_order_labels(self):
        red, _, _ = self.seed_pairs()
        photo = ProductColor.objects.create(product=self.product, name='Rouge', image=color_photo())
        items = [{
            'variant': red.pk, 'product': self.product.pk, 'name': self.product.name,
            'size': 'M', 'color': 'Rouge', 'unit_amount': 3000, 'quantity': 1,
        }]
        order = Order.objects.create(session_key='historical-photo-label', items=items, subtotal=3000, status='demo')
        before = list(self.product.stock_variants.values_list('pk', 'size', 'color', 'quantity', 'reserved'))
        response = self.client.post(
            reverse('core:catalog_edit', args=[self.product.pk]),
            self.product_data(**{
                'stock-TOTAL_FORMS': '0', 'colors-TOTAL_FORMS': '1',
                'colors-INITIAL_FORMS': '1', 'colors-0-id': photo.pk,
                'colors-0-product': self.product.pk, 'colors-0-name': 'Bordeaux',
            }),
        )
        self.assertEqual(response.status_code, 302)
        photo.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(photo.name, 'Bordeaux')
        self.assertEqual(order.items, items)
        self.assertEqual(
            list(self.product.stock_variants.values_list('pk', 'size', 'color', 'quantity', 'reserved')),
            before,
        )

    def test_edit_shows_existing_sizes_inside_their_own_color_card(self):
        red, black, blue = self.seed_pairs()
        red.reserved = 2
        red.save(update_fields=['reserved'])
        red_photo = ProductColor.objects.create(product=self.product, name='ROUGE', image=color_photo())
        blue_photo = ProductColor.objects.create(product=self.product, name='Bleu', image=color_photo())
        response = self.client.get(reverse('core:catalog_edit', args=[self.product.pk]))
        self.assertEqual(response.status_code, 200)
        forms_by_photo = {form.instance.pk: form for form in response.context['colors'].forms if form.instance.pk}
        self.assertEqual([variant.pk for variant in forms_by_photo[red_photo.pk].existing_stock], [red.pk])
        self.assertEqual(forms_by_photo[red_photo.pk].existing_stock[0].available, 3)
        self.assertEqual([variant.pk for variant in forms_by_photo[blue_photo.pk].existing_stock], [blue.pk])
        self.assertNotIn(black.pk, [variant.pk for form in forms_by_photo.values() for variant in form.existing_stock])
