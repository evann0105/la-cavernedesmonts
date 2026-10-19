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
            [color for color in response.context['product_colors'] if color['key'] != '__main_photo__'],
            [{'key': 'rouge vif', 'name': 'ROUGE VIF', 'image': photo.image.url, 'state': 'available'}],
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

    def test_color_size_lists_save_only_the_five_declared_pairs(self):
        pairs = [('M', 'Rouge', 5), ('L', 'Rouge', 2), ('XL', 'Rouge', 1),
                 ('S', 'Vert', 3), ('M', 'Vert', 4)]
        values = {
            'stock-TOTAL_FORMS': str(len(pairs)), 'colors-TOTAL_FORMS': '2',
            'colors-0-name': 'Rouge', 'colors-0-sizes': 'M, L, XL',
            'colors-0-image': color_photo('rouge.png'),
            'colors-1-name': 'Vert', 'colors-1-sizes': 'S, M',
            'colors-1-image': color_photo('vert.png'),
        }
        for index, (size, color, quantity) in enumerate(pairs):
            values.update({
                f'stock-{index}-size': size, f'stock-{index}-color': color,
                f'stock-{index}-quantity': str(quantity),
            })
        response = self.client.post(reverse('core:catalog_add'), self.product_data(
            name='Listes de tailles par couleur', main_image=color_photo(), **values,
        ))
        self.assertEqual(response.status_code, 302)
        product = Product.objects.get(name='Listes de tailles par couleur')
        self.assertEqual(set(product.stock_variants.values_list('size', 'color', 'quantity')), set(pairs))
        self.assertFalse(product.stock_variants.filter(size='S', color='Rouge').exists())
        self.assertFalse(product.stock_variants.filter(size__in=['L', 'XL'], color='Vert').exists())

    def test_color_size_list_without_quantities_rejects_the_whole_product(self):
        for total in ['0', '1']:
            with self.subTest(stock_rows=total):
                response = self.client.post(reverse('core:catalog_add'), self.product_data(
                    name='Tailles sans quantités', main_image=color_photo(), **{
                        'stock-TOTAL_FORMS': total, 'colors-TOTAL_FORMS': '1',
                        'colors-0-name': 'Rouge', 'colors-0-sizes': 'M, L, XL',
                        'colors-0-image': color_photo('rouge.png'),
                    },
                ))
                self.assertEqual(response.status_code, 200)
                self.assertIn('quantit', response.content.decode().lower())
                self.assertFalse(Product.objects.filter(name='Tailles sans quantités').exists())
                self.assertFalse(Variant.objects.exists())
                self.assertFalse(ProductColor.objects.exists())

    def test_color_size_list_cannot_use_another_color_or_a_deleted_stock_row(self):
        for changes in [
            {'stock-0-color': 'Vert'},
            {'stock-0-color': 'Rouge', 'stock-0-DELETE': 'on'},
        ]:
            with self.subTest(changes=changes):
                response = self.client.post(reverse('core:catalog_add'), self.product_data(
                    name='Association couleur invalide', main_image=color_photo(), **{
                        'stock-TOTAL_FORMS': '1', 'stock-0-size': 'M',
                        'colors-TOTAL_FORMS': '1', 'colors-0-name': 'Rouge',
                        'colors-0-sizes': 'M', 'colors-0-image': color_photo('rouge.png'),
                        **changes,
                    },
                ))
                self.assertEqual(response.status_code, 200)
                self.assertIn('quantit', response.content.decode().lower())
                self.assertFalse(Product.objects.filter(name='Association couleur invalide').exists())
                self.assertFalse(Variant.objects.exists())

    def test_color_size_list_normalizes_spaces_and_deduplicates_case_insensitively(self):
        from .catalog_forms import ProductColorForm
        form = ProductColorForm(
            data={'name': 'Rouge', 'sizes': ' M , m, 6   mois, S/M,, 6 mois ,'},
            files={'image': color_photo()}, instance=ProductColor(product=self.product),
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['sizes'], 'M, 6 mois, S/M')
        self.assertEqual(form.size_options, ['M', '6 mois', 'S/M'])

    def test_color_size_list_rejects_a_label_longer_than_stock_allows(self):
        from .catalog_forms import ProductColorForm
        for size, valid in [('A' * 50, True), ('A' * 51, False)]:
            with self.subTest(length=len(size)):
                form = ProductColorForm(
                    data={'name': 'Rouge', 'sizes': size}, files={'image': color_photo()},
                    instance=ProductColor(product=self.product),
                )
                self.assertEqual(form.is_valid(), valid, form.errors)
                if not valid:
                    self.assertIn('sizes', form.errors)

    def test_color_size_list_accepts_existing_and_new_sizes_without_rewriting_stock_or_orders(self):
        existing = Variant.objects.create(
            product=self.product, size=' M ', color=' Rouge ', quantity=7, reserved=2,
        )
        photo = ProductColor.objects.create(product=self.product, name='Rouge', image=color_photo())
        items = [{
            'variant': existing.pk, 'product': self.product.pk, 'name': self.product.name,
            'size': ' M ', 'color': ' Rouge ', 'unit_amount': 3000, 'quantity': 1,
        }]
        order = Order.objects.create(session_key='old-csv-sizes', items=items, subtotal=3000, status='demo')
        response = self.client.post(
            reverse('core:catalog_edit', args=[self.product.pk]), self.product_data(**{
                'stock-TOTAL_FORMS': '1', 'stock-0-size': 'L',
                'stock-0-color': 'Rouge', 'stock-0-quantity': '3',
                'colors-TOTAL_FORMS': '1', 'colors-INITIAL_FORMS': '1',
                'colors-0-id': photo.pk, 'colors-0-product': self.product.pk,
                'colors-0-name': 'Rouge', 'colors-0-sizes': 'M, L',
            }),
        )
        self.assertEqual(response.status_code, 302)
        existing.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual((existing.size, existing.color, existing.quantity, existing.reserved),
                         (' M ', ' Rouge ', 7, 2))
        self.assertEqual(order.items, items)
        self.assertEqual(self.product.stock_variants.count(), 2)
        self.assertEqual(self.product.stock_variants.get(size='L', color='Rouge').quantity, 3)

    def test_legacy_single_variant_size_rejects_csv_but_accepts_combined_slash_label(self):
        response = self.client.post(reverse('core:catalog_add'), self.product_data(
            name='CSV dans une seule variante', is_published='', **{
                'stock-TOTAL_FORMS': '1', 'stock-0-size': 'M, L, XL',
            },
        ))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Product.objects.filter(name='CSV dans une seule variante').exists())
        self.assertIn('taille', response.content.decode().lower())
        response = self.client.post(reverse('core:catalog_add'), self.product_data(
            name='Taille combinée valide', is_published='', **{
                'stock-TOTAL_FORMS': '1', 'stock-0-size': 'S/M',
            },
        ))
        self.assertEqual(response.status_code, 302)
        product = Product.objects.get(name='Taille combinée valide')
        self.assertEqual(list(product.stock_variants.values_list('size', 'color', 'quantity')),
                         [('S/M', 'Rouge', 5)])

    def build_options(self):
        from .product_options import build_product_options
        variants = list(self.product.stock_variants.all())
        photos = list(self.product.color_photos.all())
        with self.assertNumQueries(0):
            options = build_product_options(self.product, variants, photos)
        return options, variants

    def test_color_states_use_real_available_stock_and_preserve_unknown_stock(self):
        Variant.objects.create(product=self.product, size='M', color='Rouge', quantity=0)
        Variant.objects.create(product=self.product, size='L', color='Rouge', quantity=None)
        Variant.objects.create(product=self.product, size='S', color='Noir', quantity=0)
        Variant.objects.create(product=self.product, size='M', color='Noir', quantity=2, reserved=2)
        Variant.objects.create(product=self.product, size='S', color='Vert', quantity=3, reserved=2)
        ProductColor.objects.create(product=self.product, name='Violet', image=color_photo())
        before = list(self.product.stock_variants.values_list('pk', 'quantity', 'reserved'))
        options, variants = self.build_options()
        states = {color['key']: color['state'] for color in options['colors']}
        self.assertEqual(
            {key: state for key, state in states.items() if key != '__main_photo__'},
            {'rouge': 'unknown', 'noir': 'out', 'vert': 'available', 'violet': 'unknown'},
        )
        self.assertEqual(
            {size['name']: size['available'] for size in options['sizes']},
            {'L': False, 'M': False, 'S': True},
        )
        self.assertEqual({item.size_key for item in variants}, {'l', 'm', 's'})
        self.assertEqual({item.color_key for item in variants}, {'rouge', 'noir', 'vert'})
        self.assertEqual(
            list(self.product.stock_variants.values_list('pk', 'quantity', 'reserved')), before,
        )

    def test_explicit_main_color_uses_main_photo_without_duplicating_color_or_stock(self):
        self.product.main_color = 'Rouge'
        self.product.save(update_fields=['main_color'])
        Variant.objects.create(product=self.product, size='M', color=' rouge ', quantity=2)
        Variant.objects.create(product=self.product, size='L', color='ROUGE', quantity=0)
        options, _ = self.build_options()
        self.assertEqual(len(options['colors']), 1)
        self.assertEqual(
            {key: options['colors'][0][key] for key in ('key', 'image', 'state')},
            {'key': 'rouge', 'image': self.product.image_url, 'state': 'available'},
        )
        self.assertEqual(self.product.stock_variants.count(), 2)

    def test_explicit_color_photo_takes_priority_over_main_photo_for_the_same_color(self):
        self.product.main_color = 'ROUGE'
        self.product.save(update_fields=['main_color'])
        Variant.objects.create(product=self.product, size='M', color='Rouge', quantity=1)
        photo = ProductColor.objects.create(
            product=self.product, name=' Rouge ', image=color_photo('rouge-detail.png'),
        )
        options, _ = self.build_options()
        red = [color for color in options['colors'] if color['key'] == 'rouge']
        self.assertEqual(len(red), 1)
        self.assertEqual((red[0]['image'], red[0]['state']), (photo.image.url, 'available'))
        self.assertEqual(self.product.stock_variants.count(), 1)

    def test_main_color_without_variants_remains_unknown_and_does_not_create_stock(self):
        self.product.main_color = 'Beige'
        self.product.save(update_fields=['main_color'])
        options, variants = self.build_options()
        self.assertEqual(options['sizes'], [])
        self.assertEqual(variants, [])
        self.assertEqual(len(options['colors']), 1)
        self.assertEqual(
            {key: options['colors'][0][key] for key in ('key', 'image', 'state')},
            {'key': 'beige', 'image': self.product.image_url, 'state': 'unknown'},
        )
        self.assertFalse(self.product.stock_variants.exists())

    def test_orphan_main_photo_has_a_generic_unknown_option_without_inferred_stock(self):
        self.product.name = 'Polaire beige de test'
        self.product.save(update_fields=['name'])
        red = Variant.objects.create(product=self.product, size='M', color='Rouge', quantity=4)
        ProductColor.objects.create(product=self.product, name='Rouge', image=color_photo())
        options, _ = self.build_options()
        origin = options['colors'][0]
        self.assertEqual(
            {key: origin[key] for key in ('key', 'name', 'image', 'state')},
            {'key': '__main_photo__', 'name': 'Coloris d’origine',
             'image': self.product.image_url, 'state': 'unknown'},
        )
        self.assertNotIn('beige', {color['key'] for color in options['colors']})
        self.assertEqual([size['key'] for size in options['sizes']], ['m'])
        self.assertEqual(list(self.product.stock_variants.values_list('pk', flat=True)), [red.pk])

    def test_multiple_unpictured_variant_colors_keep_access_to_the_original_photo(self):
        Variant.objects.create(product=self.product, size='M', color='Rouge', quantity=1)
        Variant.objects.create(product=self.product, size='L', color='Bleu', quantity=0)
        options, _ = self.build_options()
        self.assertEqual(options['colors'][0]['key'], '__main_photo__')
        self.assertEqual(options['colors'][0]['state'], 'unknown')
        self.assertEqual(
            {color['key']: color['state'] for color in options['colors']},
            {'__main_photo__': 'unknown', 'rouge': 'available', 'bleu': 'out'},
        )
        self.assertEqual(self.product.stock_variants.count(), 2)

    def test_legacy_product_without_color_information_keeps_its_existing_selector(self):
        options, variants = self.build_options()
        self.assertEqual(options, {'sizes': [], 'colors': []})
        self.assertEqual(variants, [])
        self.assertFalse(self.product.stock_variants.exists())

    def test_creation_records_original_color_and_its_sizes_without_copying_main_photo(self):
        response = self.client.post(reverse('core:catalog_add'), self.product_data(
            name='Origine beige', main_image=color_photo('beige-main.png'), **{
                'original-name': ' Beige ', 'original-sizes': 'M, L',
                'colors-TOTAL_FORMS': '0', 'stock-TOTAL_FORMS': '2',
                'stock-0-size': 'M', 'stock-0-color': 'Beige', 'stock-0-quantity': '2',
                'stock-1-size': 'L', 'stock-1-color': 'Beige', 'stock-1-quantity': '0',
            },
        ))
        self.assertEqual(response.status_code, 302)
        product = Product.objects.get(name='Origine beige')
        self.assertRedirects(response, reverse('core:product_detail', args=[product.slug]))
        self.assertEqual(product.main_color, 'Beige')
        self.assertTrue(product.main_image)
        self.assertFalse(product.color_photos.exists())
        self.assertEqual(
            set(product.stock_variants.values_list('size', 'color', 'quantity')),
            {('M', 'Beige', 2), ('L', 'Beige', 0)},
        )
        page = Client().get(reverse('core:product_detail', args=[product.slug]))
        beige = [color for color in page.context['product_colors'] if color['key'] == 'beige']
        self.assertEqual(len(beige), 1)
        self.assertEqual((beige[0]['image'], beige[0]['state']), (product.image_url, 'available'))

    def test_older_product_post_without_original_metadata_preserves_main_color(self):
        self.product.main_color = 'Beige'
        self.product.save(update_fields=['main_color'])
        response = self.client.post(
            reverse('core:catalog_edit', args=[self.product.pk]),
            self.product_data(price='39.95', **{'stock-TOTAL_FORMS': '0'}),
        )
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.main_color, 'Beige')
        self.assertEqual(str(self.product.price), '39.95')
        self.assertFalse(self.product.color_photos.exists())

    def test_original_sizes_require_a_name_and_quantities_before_saving_anything(self):
        for original, error_field in [
            ({'original-sizes': 'M'}, 'name'),
            ({'original-name': 'Beige', 'original-sizes': 'M, L'}, 'sizes'),
        ]:
            with self.subTest(original=original):
                response = self.client.post(reverse('core:catalog_add'), self.product_data(
                    name='Origine incomplète', main_image=color_photo(), **{
                        'stock-TOTAL_FORMS': '0', 'colors-TOTAL_FORMS': '0', **original,
                    },
                ))
                self.assertEqual(response.status_code, 200)
                self.assertIn(error_field, response.context['original_color_form'].errors)
                self.assertFalse(Product.objects.filter(name='Origine incomplète').exists())
                self.assertFalse(Variant.objects.exists())
                self.assertFalse(ProductColor.objects.exists())

    def test_original_card_shows_only_its_existing_stock_and_adding_size_preserves_history(self):
        self.product.main_color = 'Beige'
        self.product.save(update_fields=['main_color'])
        beige = Variant.objects.create(
            product=self.product, size='M', color=' Beige ', quantity=5, reserved=1, revision=9,
        )
        black = Variant.objects.create(product=self.product, size='S', color='Noir', quantity=6)
        items = [{
            'variant': beige.pk, 'product': self.product.pk, 'name': self.product.name,
            'size': 'M', 'color': ' Beige ', 'unit_amount': 3000, 'quantity': 1,
        }]
        order = Order.objects.create(session_key='original-color-history', items=items, subtotal=3000, status='demo')
        edit_url = reverse('core:catalog_edit', args=[self.product.pk])
        page = self.client.get(edit_url)
        self.assertEqual(page.status_code, 200)
        original = page.context['original_color_form']
        self.assertEqual(original.initial['name'], 'Beige')
        self.assertEqual(original.original_image_url, self.product.image_url)
        self.assertEqual([(variant.pk, variant.size, variant.available) for variant in original.existing_stock],
                         [(beige.pk, 'M', 4)])
        response = self.client.post(edit_url, self.product_data(**{
            'original-name': 'Beige', 'original-sizes': 'M, L',
            'stock-TOTAL_FORMS': '1', 'stock-0-size': 'L',
            'stock-0-color': 'Beige', 'stock-0-quantity': '2',
            'colors-TOTAL_FORMS': '0',
        }))
        self.assertEqual(response.status_code, 302)
        beige.refresh_from_db()
        black.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual((beige.color, beige.quantity, beige.reserved, beige.revision), (' Beige ', 5, 1, 9))
        self.assertEqual((black.quantity, black.reserved), (6, 0))
        self.assertEqual(order.items, items)
        self.assertEqual(self.product.stock_variants.get(size='L', color='Beige').quantity, 2)
        self.assertEqual(self.product.stock_variants.count(), 3)
        self.assertFalse(self.product.color_photos.exists())

    def test_identified_color_without_inventory_cannot_be_ordered_as_a_legacy_size(self):
        for main_color, with_photo in [('Beige', False), ('', True)]:
            with self.subTest(main_color=main_color, with_photo=with_photo):
                self.product.main_color = main_color
                self.product.save(update_fields=['main_color'])
                if with_photo:
                    ProductColor.objects.create(product=self.product, name='Rouge', image=color_photo())
                buyer = Client()
                response = buyer.post(self.cart_url(), {'size': 'M', 'quantity': 1})
                self.assertEqual(response.status_code, 302)
                self.assertFalse(buyer.session.get('cart'))
                self.assertFalse(self.product.stock_variants.exists())
