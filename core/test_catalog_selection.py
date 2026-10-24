from decimal import Decimal
from html import unescape
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.urls import reverse
from django_otp.plugins.otp_totp.models import TOTPDevice

from connexion.test_helpers import verify_test_user
from core.catalog_bulk import MAX_PRODUCTS
from core.models import Category, Product
from stocks.models import Variant


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class CatalogSelectionIntegrationTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user('selection-manager', is_staff=True)
        self.manager.groups.add(Group.objects.get(name='Gestion du catalogue'))
        verify_test_user(self.manager)
        self.device = TOTPDevice.objects.create(user=self.manager, name='default', confirmed=True)
        self.category = Category.objects.create(name='Femmes', slug='femmes-selection')
        self.other_category = Category.objects.create(name='Hommes', slug='hommes-selection')
        self.list_url = reverse('core:catalog_list')
        self.results_url = reverse('core:catalog_selection_results')
        self.bulk_url = reverse('core:catalog_bulk')
        self.login_manager()

    def login_manager(self):
        self.client.force_login(self.manager, backend='connexion.backends.EmailOrUsernameBackend')
        session = self.client.session
        session['otp_device_id'] = self.device.persistent_id
        session.save()

    def product(self, name='Polaire', **values):
        defaults = {'name': name, 'price': '40.00', 'category': self.category, 'is_published': False,
                    'description': 'Une description.', 'static_image': 'products/fixture.webp'}
        defaults.update(values)
        return Product.objects.create(**defaults)

    def batch(self, count, prefix='Article', **values):
        defaults = {'category': self.category, 'price': '40.00', 'is_published': False, 'brand': ''}
        defaults.update(values)
        return Product.objects.bulk_create([
            Product(name=f'{prefix} {index:04d}', slug=f'selection-{prefix.lower()}-{index}', **defaults)
            for index in range(count)
        ])

    def test_results_endpoint_is_restricted_to_verified_catalogue_managers(self):
        self.product()
        self.client.logout()
        self.assertEqual(self.client.get(self.results_url).status_code, 302)
        for username, staff in [('selection-customer', False), ('selection-staff', True)]:
            user = get_user_model().objects.create_user(username, is_staff=staff)
            verify_test_user(user)
            self.client.force_login(user, backend='connexion.backends.EmailOrUsernameBackend')
            response = self.client.get(self.results_url)
            self.assertEqual(response.status_code, 403)
            self.assertNotContains(response, '"ids"', status_code=403)
        self.login_manager()
        response = self.client.get(self.results_url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])

    def test_results_is_get_only_and_does_not_change_products(self):
        product = self.product()
        response = self.client.post(self.results_url, {'selected_products': [product.pk]})
        self.assertEqual(response.status_code, 405)
        self.assertEqual(self.client.get(self.results_url).json(), {'ids': [str(product.pk)], 'count': 1})
        self.assertEqual(Product.objects.count(), 1)
        product.refresh_from_db()
        self.assertFalse(product.is_published)
        self.assertEqual(product.price, Decimal('40.00'))

    def test_all_matching_results_include_later_pages_and_preserve_cumulative_filters(self):
        matching = self.batch(25, prefix='Polaire')
        complementary = self.product('Polaire complémentaire', category=self.other_category)
        complementary.collections.add(self.category)
        # The same primary and complementary category must not repeat an ID.
        matching[0].collections.add(self.category, self.other_category)
        self.product('Pantalon')
        self.product('Polaire homme', category=self.other_category)
        self.product('Polaire publiée', is_published=True)
        self.product('Polaire avec marque', brand='Peak Mountain')
        filters = {'q': 'Polaire', 'categorie': self.category.slug, 'statut': 'draft',
                   'attention': 'missing_brand', 'tri': 'name', 'page': '2'}
        response = self.client.get(self.results_url, filters)
        self.assertEqual(response.status_code, 200)
        ids = response.json()['ids']
        self.assertEqual(response.json()['count'], 26)
        self.assertEqual(len(set(ids)), 26)
        self.assertEqual(ids, [str(product.pk) for product in matching] + [str(complementary.pk)])
        page = self.client.get(self.list_url, filters)
        self.assertEqual(page.context['page_obj'].number, 2)
        self.assertEqual(page.context['page_obj'].paginator.count, 26)
        self.assertEqual(page.context['selectable_count'], 26)
        self.assertEqual([str(product.pk) for product in page.context['page_obj']], ids[20:])

    def test_archived_duplicates_are_not_selectable_even_when_explicitly_filtered(self):
        source = self.product('Polaire source')
        archive = self.product('Polaire doublon', duplicate_of=source)
        response = self.client.get(self.results_url)
        self.assertEqual(response.json(), {'ids': [str(source.pk)], 'count': 1})
        self.assertEqual(self.client.get(self.results_url, {'statut': 'duplicate'}).json(), {'ids': [], 'count': 0})
        listing = self.client.get(self.list_url, {'statut': 'duplicate'})
        self.assertEqual(listing.context['page_obj'].paginator.count, 1)
        self.assertEqual(listing.context['selectable_count'], 0)
        self.assertNotContains(listing, f'name="selected_products" value="{archive.pk}"')

    def test_selection_limit_accepts_exactly_500_but_never_returns_a_partial_overflow(self):
        self.assertEqual(MAX_PRODUCTS, 500)
        products = self.batch(MAX_PRODUCTS)
        accepted = self.client.get(self.results_url)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()['count'], MAX_PRODUCTS)
        self.assertCountEqual(accepted.json()['ids'], [str(product.pk) for product in products])
        self.product('Article supplémentaire')
        rejected = self.client.get(self.results_url)
        self.assertEqual(rejected.status_code, 400)
        self.assertIn('500', rejected.json()['error'])
        self.assertNotIn('ids', rejected.json())
        self.assertNotIn('count', rejected.json())

    def test_limit_is_applied_after_filters_not_to_the_complete_catalogue(self):
        self.batch(MAX_PRODUCTS + 1, category=self.other_category)
        selected = self.product('Polaire sélectionnée')
        response = self.client.get(self.results_url, {'categorie': self.category.slug})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'ids': [str(selected.pk)], 'count': 1})

    def test_list_count_sort_and_pagination_keep_the_same_filter_parameters(self):
        products = self.batch(25, prefix='Polaire')
        low_price = products[-1]
        Variant.objects.create(product=low_price, size='Junior', color='Vert', quantity=2, price='20.00')
        self.product('Hors recherche', category=self.other_category)
        filters = {'q': 'Polaire', 'categorie': self.category.slug, 'statut': 'draft',
                   'attention': 'missing_brand', 'tri': 'price'}
        first = self.client.get(self.list_url, filters)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.context['page_obj'].paginator.count, 25)
        self.assertEqual(first.context['selectable_count'], 25)
        self.assertEqual(first.context['page_obj'][0].pk, low_price.pk)
        self.assertEqual(len(first.context['page_obj']), 20)
        self.assertEqual(first.context['filter_query'], urlencode(filters))
        self.assertEqual(first.context['selection_results_url'], self.results_url + '?' + urlencode(filters))
        self.assertContains(first, '25 produits trouvés')
        self.assertIn('?' + urlencode(filters) + '&page=2', unescape(first.content.decode()))
        second = self.client.get(self.list_url, {**filters, 'page': 2})
        self.assertEqual(len(second.context['page_obj']), 5)
        self.assertEqual(second.context['page_obj'].number, 2)
        second_ids = {product.pk for product in second.context['page_obj']}
        self.assertFalse(second_ids.intersection(product.pk for product in first.context['page_obj']))
        self.assertIn('?' + urlencode(filters) + '&page=1', unescape(second.content.decode()))

    def test_row_links_retain_the_filtered_page_and_row_anchor(self):
        products = self.batch(21, prefix='Polaire')
        filters = {'q': 'Polaire', 'statut': 'draft', 'tri': 'name', 'page': 2}
        response = self.client.get(self.list_url, filters)
        return_to = self.list_url + '?' + urlencode(filters)
        self.assertEqual(response.context['return_to'], return_to)
        product = response.context['page_obj'][0]
        self.assertEqual(product.pk, products[-1].pk)
        self.assertEqual(product.catalog_return_to, return_to + f'#catalog-product-{product.pk}')
        self.assertContains(response, f'id="catalog-product-{product.pk}"')
        self.assertContains(response, 'data-catalog-remember')

    def test_rows_and_filters_explain_unknown_stock_without_calling_it_a_rupture(self):
        unknown = self.product('Stock inconnu', main_image=None, static_image='', brand='')
        empty = self.product('Stock épuisé', brand='Peak Mountain')
        Variant.objects.create(product=empty, size='M', color='Vert', quantity=0)
        response = self.client.get(self.list_url, {'attention': 'missing_stock'})
        rows = list(response.context['page_obj'])
        self.assertEqual([row.pk for row in rows], [unknown.pk])
        self.assertTrue(rows[0].needs_photo)
        self.assertTrue(rows[0].needs_brand)
        self.assertTrue(rows[0].needs_stock)
        self.assertFalse(rows[0].out_of_stock)
        self.assertContains(response, 'Photo manquante')
        self.assertContains(response, 'Marque manquante')
        self.assertContains(response, 'Stock à renseigner')
        self.assertNotContains(response, 'class="catalog-stock-empty"')
        depleted = self.client.get(self.list_url, {'attention': 'out_of_stock'})
        self.assertEqual([row.pk for row in depleted.context['page_obj']], [empty.pk])
        self.assertContains(depleted, 'class="catalog-stock-empty"')

    def test_successful_bulk_edit_removes_only_its_ids_from_selection_once_on_return(self):
        products = self.batch(21, prefix='Polaire')
        selected = [str(products[0].pk), str(products[-1].pk)]
        return_to = self.list_url + '?q=Polaire&statut=draft&tri=name&page=2'
        started = self.client.post(self.bulk_url, {'action': 'start', 'selected_products': selected, 'return_to': return_to})
        self.assertEqual(started.status_code, 200)
        self.assertEqual(started.context['return_to'], return_to)
        preview = self.client.post(self.bulk_url, {
            'action': 'preview', 'selected_products': selected, 'return_to': return_to,
            'selection_snapshot': started.context['selection_snapshot'],
            'change_brand': '1', 'brand': 'Peak Mountain',
        })
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.context['step'], 'preview')
        applied = self.client.post(self.bulk_url, {'action': 'apply', 'confirmation': preview.context['confirmation'], 'return_to': return_to})
        self.assertRedirects(applied, return_to, fetch_redirect_response=False)
        self.assertEqual(self.client.session['catalogue_applied_products'], selected)
        self.assertEqual(Product.objects.filter(brand='Peak Mountain').count(), 2)
        self.assertEqual(Product.objects.exclude(pk__in=selected).filter(brand='').count(), 19)
        returned = self.client.get(return_to)
        self.assertEqual(returned.context['selection_applied'], selected)
        self.assertEqual(returned.context['page_obj'].number, 2)
        self.assertNotIn('catalogue_applied_products', self.client.session)
        self.assertEqual(self.client.get(return_to).context['selection_applied'], [])

    def test_failed_bulk_confirmation_keeps_selection_and_does_not_clear_it(self):
        product = self.product()
        started = self.client.post(self.bulk_url, {'action': 'start', 'selected_products': [product.pk]})
        preview = self.client.post(self.bulk_url, {
            'action': 'preview', 'selected_products': [product.pk],
            'selection_snapshot': started.context['selection_snapshot'], 'change_brand': '1', 'brand': 'Peak Mountain',
        })
        Product.objects.filter(pk=product.pk).update(brand='Modification entre-temps')
        rejected = self.client.post(self.bulk_url, {'action': 'apply', 'confirmation': preview.context['confirmation']})
        self.assertEqual(rejected.status_code, 409)
        self.assertNotIn('catalogue_applied_products', self.client.session)
        product.refresh_from_db()
        self.assertEqual(product.brand, 'Modification entre-temps')
