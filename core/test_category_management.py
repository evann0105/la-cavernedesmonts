"""Seller-controlled categories keep links stable and navigation reversible."""
from importlib import import_module
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Permission
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import resolve, reverse

from connexion.models import EmailVerification
from connexion.test_helpers import verify_test_user
from .catalog_forms import CatalogProductForm
from .category_navigation import category_navigation
from .category_services import (CategoryConflict, CategoryError, category_order_snapshot,
    move_category, save_category, set_category_visibility)
from .models import Category, Product


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=False, EMAIL_DELIVERY_ENABLED=False)
class CategoryManagementTests(TestCase):
    backend = 'connexion.backends.EmailOrUsernameBackend'

    def setUp(self):
        # Data migrations seed a gift rubric; isolate this suite's ordered set.
        Category.objects.all().delete()
        self.manager = get_user_model().objects.create_user('categories-manager',
            email='categories@example.test', is_staff=True)
        self.manager.user_permissions.add(Permission.objects.get(
            content_type__app_label='core', codename='manage_catalog'))
        verify_test_user(self.manager)
        self.client.force_login(self.manager, backend=self.backend)
        self.women = Category.objects.create(name='Femmes', slug='femmes', menu_label='Femme',
            position=10, show_in_menu=True)
        self.men = Category.objects.create(name='Hommes', slug='hommes', menu_label='Homme',
            position=20, show_in_menu=True)
        self.hidden = Category.objects.create(name='Nouveautés', slug='nouveautes', position=30)

    def url(self, name, pk=None):
        return reverse('core:category_' + name, kwargs={'pk': pk} if pk else None)

    def values(self, category=None, **changes):
        result = {'name': 'Accessoires de randonnée', 'menu_label': 'Randonnée',
                  'show_in_menu': True, 'revision': 0}
        if category:
            result.update({key: getattr(category, key) for key in result})
        result.update(changes)
        return result

    def snapshot(self, actor=None):
        return category_order_snapshot(list(Category.objects.all()), actor or self.manager)

    def rows(self):
        return list(Category.objects.order_by('pk').values())

    def navbar(self):
        request = RequestFactory().get('/produits/', {'categorie': 'nouveautes'})
        SessionMiddleware(lambda response: response).process_request(request)
        request.user = AnonymousUser()
        request.resolver_match = resolve(request.path)
        context = {'request': request, 'user': request.user, 'cart_count': 0, **category_navigation(request)}
        return render_to_string('partials/navbar.html', context, request=request)

    def test_create_and_edit_without_changing_shared_urls_or_products(self):
        created = save_category(None, self.values(name='  Accessoires   de randonnée  '), self.manager)
        self.assertEqual((created.name, created.slug, created.position, created.revision),
                         ('Accessoires de randonnée', 'accessoires-de-randonnee', 40, 1))
        self.assertTrue(created.show_in_menu)
        product = Product.objects.create(name='Accessoire test', price='20.00', category=created)
        product.collections.add(self.women, created)
        saved = save_category(created.pk, self.values(created, name='Sacs et accessoires', menu_label='Sacs'), self.manager)
        self.assertEqual(saved.slug, 'accessoires-de-randonnee')
        self.assertEqual(saved.revision, 2)
        product.refresh_from_db()
        self.assertEqual(product.category_id, created.pk)
        self.assertSetEqual(set(product.collections.values_list('pk', flat=True)), {created.pk, self.women.pk})
        self.assertEqual(LogEntry.objects.filter(user=self.manager,
            content_type__app_label='core', content_type__model='category', object_id=str(created.pk)).count(), 2)

    def test_new_form_is_hidden_from_menu_by_default_and_slug_is_automatic(self):
        response = self.client.get(self.url('create'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['form']['show_in_menu'].value())
        self.assertNotContains(response, 'name="slug"')
        response = self.client.post(self.url('create'), {'name': 'Pour la maison', 'menu_label': '', 'revision': 0})
        self.assertEqual(response.status_code, 302)
        row = Category.objects.get(slug='pour-la-maison')
        self.assertFalse(row.show_in_menu)
        self.assertEqual(row.navigation_label, 'Pour la maison')

    def test_unique_names_and_reserved_or_colliding_slugs(self):
        before = self.rows()
        with self.assertRaises(CategoryError):
            save_category(None, self.values(name='  fEMMES  '), self.manager)
        self.assertEqual(self.rows(), before)
        first = save_category(None, self.values(name='Été'), self.manager)
        second = save_category(None, self.values(name='Ete'), self.manager)
        virtual = save_category(None, self.values(name='Cadeaux bébé'), self.manager)
        self.assertEqual((first.slug, second.slug, virtual.slug), ('ete', 'ete-2', 'cadeaux-bebe-2'))

    def test_invalid_inputs_are_rejected_without_writes(self):
        before = self.rows()
        cases = [self.values(name=''), self.values(name='x' * 121), self.values(menu_label='x' * 41),
                 self.values(show_in_menu='0'), self.values(revision=True), self.values(slug='different')]
        for values in cases:
            with self.subTest(values=values), self.assertRaises(CategoryError):
                save_category(None, values, self.manager)
        self.assertEqual(self.rows(), before)

    def test_public_menu_uses_only_visible_categories_in_merchant_order(self):
        html = self.navbar()
        desktop, mobile = html.split('<nav id="mobile-nav"', 1)
        self.assertLess(desktop.index('categorie=femmes'), desktop.index('categorie=hommes'))
        for fragment in (desktop, mobile):
            self.assertIn('categorie=femmes', fragment)
            self.assertIn('categorie=hommes', fragment)
            self.assertNotIn('categorie=nouveautes', fragment)
        set_category_visibility(self.hidden.pk, 1, True, self.manager)
        visible = self.navbar()
        self.assertEqual(visible.count('categorie=nouveautes'), 2)
        self.assertEqual(visible.count('aria-current="page"'), 2)

    def test_hiding_every_rubric_never_restores_a_fixed_fallback_menu(self):
        set_category_visibility(self.women.pk, 1, False, self.manager)
        set_category_visibility(self.men.pk, 1, False, self.manager)
        html = self.navbar()
        self.assertNotIn('categorie=', html)
        self.assertIn('Notre boutique', html)
        self.assertIn('Toute la collection', html)

    def test_hidden_category_remains_accessible_in_public_catalog_and_product_form(self):
        product = Product.objects.create(name='Polaire dans une rubrique masquée', price='25.00', category=self.hidden)
        response = self.client.get(reverse('core:product_list'), {'categorie': self.hidden.slug})
        self.assertContains(response, product.name)
        self.assertIn(self.hidden, list(CatalogProductForm().fields['category'].queryset))
        self.assertIn(self.hidden, list(CatalogProductForm().fields['collections'].queryset))
        self.assertEqual(response.context['selected_category'], self.hidden)

    def test_menu_overflow_keeps_every_link_and_escapes_merchant_labels(self):
        for number in range(5):
            Category.objects.create(name=f'Collection {number}', slug=f'collection-{number}',
                menu_label='<script>alert(1)</script>' if number == 4 else '', show_in_menu=True,
                position=40 + number)
        data = category_navigation(RequestFactory().get('/'))
        self.assertEqual(len(data['navigation_main_categories']), 4)
        self.assertEqual(len(data['navigation_more_categories']), 3)
        html = self.navbar()
        self.assertIn('class="category-menu-more"', html)
        self.assertEqual(html.count('categorie=collection-4'), 2)
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', html)

    def test_default_menu_labels_still_use_translation_and_custom_labels_are_kept(self):
        with patch('django.utils.translation.gettext', side_effect=lambda value: 'Traduit ' + value) as translate:
            self.assertEqual(self.women.navigation_label, 'Traduit Femme')
            self.hidden.menu_label = 'Nouveautés du moment'
            self.assertEqual(self.hidden.navigation_label, 'Nouveautés du moment')
            translate.assert_called_once_with('Femme')

    def test_move_updates_catalog_form_order_and_preserves_menu_visibility(self):
        move_category(self.hidden.pk, 'up', self.snapshot(), self.manager)
        self.assertEqual(list(Category.objects.values_list('pk', flat=True)), [self.women.pk, self.hidden.pk, self.men.pk])
        self.assertEqual(list(CatalogProductForm().fields['category'].queryset.values_list('pk', flat=True)),
                         [self.women.pk, self.hidden.pk, self.men.pk])
        self.hidden.refresh_from_db()
        self.assertFalse(self.hidden.show_in_menu)
        self.assertEqual(list(Category.objects.values_list('position', flat=True)), [1, 2, 3])

    def test_stale_edit_visibility_and_order_do_not_overwrite_current_changes(self):
        snapshot = self.snapshot()
        save_category(self.women.pk, self.values(self.women, name='La collection Femme'), self.manager)
        before = self.rows()
        for operation in [lambda: save_category(self.women.pk, self.values(self.women), self.manager),
                          lambda: set_category_visibility(self.women.pk, 1, False, self.manager),
                          lambda: move_category(self.men.pk, 'up', snapshot, self.manager)]:
            with self.assertRaises(CategoryConflict):
                operation()
        self.assertEqual(self.rows(), before)
        response = self.client.post(self.url('edit', self.women.pk), self.values(self.women))
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, 'Recharger le formulaire', status_code=409)
        self.assertEqual(self.rows(), before)

    def test_order_refuses_categories_added_removed_or_revised_since_the_page_opened(self):
        old = self.snapshot()
        created = save_category(None, self.values(), self.manager)
        with self.assertRaises(CategoryConflict):
            move_category(self.men.pk, 'up', old, self.manager)
        snapshot = self.snapshot()
        created.delete()
        with self.assertRaises(CategoryConflict):
            move_category(self.men.pk, 'up', snapshot, self.manager)

    def test_order_rejects_tampered_expired_or_other_managers_snapshot(self):
        snapshot = self.snapshot()
        other = get_user_model().objects.create_user('categories-other-manager', is_staff=True, email='other@example.test')
        other.user_permissions.add(Permission.objects.get(content_type__app_label='core', codename='manage_catalog'))
        verify_test_user(other)
        before = self.rows()
        with self.assertRaises(CategoryError):
            move_category(self.men.pk, 'up', snapshot + 'bad', self.manager)
        with self.assertRaises(CategoryError):
            move_category(self.men.pk, 'up', snapshot, other)
        with patch('django.core.signing.time.time', return_value=1):
            expired = self.snapshot()
        with self.assertRaises(CategoryConflict):
            move_category(self.men.pk, 'up', expired, self.manager)
        self.assertEqual(self.rows(), before)

    def test_move_boundaries_and_visibility_types_are_rejected_without_changes(self):
        before = self.rows()
        for pk, direction in [(self.women.pk, 'up'), (self.hidden.pk, 'down'), (self.men.pk, 'other'), (True, 'up')]:
            with self.assertRaises(CategoryError):
                move_category(pk, direction, self.snapshot(), self.manager)
        for visibility in ('0', 0, None):
            with self.assertRaises(CategoryError):
                set_category_visibility(self.women.pk, 1, visibility, self.manager)
        self.assertEqual(self.rows(), before)

    def test_audit_failure_rolls_back_the_entire_move_or_create(self):
        before = self.rows()
        snapshot = self.snapshot()
        with patch('django.contrib.admin.models.LogEntryManager.log_actions', side_effect=RuntimeError('Audit unavailable')):
            with self.assertRaises(RuntimeError):
                move_category(self.men.pk, 'up', snapshot, self.manager)
            self.assertEqual(self.rows(), before)
            with self.assertRaises(RuntimeError):
                save_category(None, self.values(), self.manager)
        self.assertEqual(self.rows(), before)

    def test_list_counts_products_once_even_when_main_and_complementary_categories_match(self):
        product = Product.objects.create(name='Polaire à plusieurs rubriques', price='30.00', category=self.women)
        product.collections.add(self.women, self.hidden)
        Product.objects.create(name='Polaire dans la même rubrique', price='40.00', category=self.women)
        response = self.client.get(self.url('list'))
        self.assertEqual(response.status_code, 200)
        counts = {row.pk: row.product_count for row in response.context['categories']}
        self.assertEqual(counts, {self.women.pk: 2, self.men.pk: 0, self.hidden.pk: 1})
        self.assertContains(response, 'form="category-move-form"')
        self.assertContains(response, 'Masquer dans le menu')
        self.assertContains(response, 'Afficher dans le menu')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('noindex', response['X-Robots-Tag'])

    def test_post_routes_move_and_toggle_then_reject_stale_requests(self):
        response = self.client.post(self.url('move'), {'move': f'{self.men.pk}:up', 'snapshot': self.snapshot()})
        self.assertEqual(response.status_code, 302)
        self.hidden.refresh_from_db()
        values = {'revision': self.hidden.revision, 'show_in_menu': '1'}
        response = self.client.post(self.url('visibility', self.hidden.pk), values)
        self.assertEqual(response.status_code, 302)
        self.hidden.refresh_from_db()
        self.assertTrue(self.hidden.show_in_menu)
        response = self.client.post(self.url('visibility', self.hidden.pk), values)
        self.assertEqual(response.status_code, 409)

    def test_mutations_are_post_only_csrf_protected_and_do_not_accept_missing_versions(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.manager, backend=self.backend)
        for name, pk, values in [('move', None, {'move': f'{self.men.pk}:up', 'snapshot': self.snapshot()}),
                                 ('visibility', self.hidden.pk, {'revision': 1, 'show_in_menu': '1'})]:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(self.url(name, pk)).status_code, 405)
                self.assertEqual(csrf_client.post(self.url(name, pk), values).status_code, 403)
                self.assertEqual(self.client.post(self.url(name, pk), {}).status_code, 400)
        self.assertEqual(csrf_client.post(self.url('create'), self.values()).status_code, 403)
        self.assertEqual(self.client.post(self.url('create'), {'name': 'Invalide'}).status_code, 400)

    def test_authorization_is_enforced_in_views_and_services(self):
        urls = [self.url('list'), self.url('create'), self.url('edit', self.women.pk),
                self.url('move'), self.url('visibility', self.hidden.pk)]
        self.client.logout()
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 302)
        for user in [get_user_model().objects.create_user('categories-customer'),
                     get_user_model().objects.create_user('categories-staff', is_staff=True)]:
            self.client.force_login(user, backend=self.backend)
            for url in urls:
                self.assertEqual(self.client.post(url, {}).status_code, 403)
            with self.assertRaises(PermissionDenied):
                save_category(None, self.values(), user)
            with self.assertRaises(PermissionDenied):
                set_category_visibility(self.women.pk, 1, False, user)
        EmailVerification.objects.filter(user=self.manager).delete()
        self.client.force_login(self.manager, backend=self.backend)
        self.assertRedirects(self.client.get(self.url('list')), reverse('connexion:verify_email'), fetch_redirect_response=False)
        with self.assertRaises(PermissionDenied):
            save_category(None, self.values(), self.manager)

    def test_migration_keeps_the_original_menu_and_hides_other_existing_categories(self):
        children = Category.objects.create(name='Enfants', slug='enfants')
        gifts = Category.objects.create(name='Petits bonheurs', slug='accessoires')
        migration = import_module('core.migrations.0019_category_management')
        migration.preserve_current_menu(apps, SimpleNamespace(connection=SimpleNamespace(alias='default')))
        self.assertEqual(list(Category.objects.filter(show_in_menu=True).values_list('slug', 'menu_label')),
                         [('femmes', 'Femme'), ('hommes', 'Homme'), ('enfants', 'Enfant'), ('accessoires', 'Petits bonheurs')])
        self.hidden.refresh_from_db()
        self.assertFalse(self.hidden.show_in_menu)
        self.assertEqual(Category.objects.count(), 5)
        self.assertEqual(Category.objects.get(pk=children.pk).position, 30)
        self.assertEqual(Category.objects.get(pk=gifts.pk).position, 40)

    def test_fresh_catalog_import_initializes_menu_and_reimport_keeps_merchant_changes(self):
        Category.objects.all().delete()
        with patch('core.management.commands.import_catalogue.call_command'):
            call_command('import_catalogue', stdout=StringIO(), verbosity=0)
            self.assertEqual(list(Category.objects.filter(show_in_menu=True).values_list('slug', 'menu_label')),
                             [('femmes', 'Femme'), ('hommes', 'Homme'), ('enfants', 'Enfant'), ('accessoires', 'Petits bonheurs')])
            women = Category.objects.get(slug='femmes')
            save_category(women.pk, self.values(women, name='La collection féminine', menu_label='Pour elle', show_in_menu=False), self.manager)
            move_category(women.pk, 'down', self.snapshot(), self.manager)
            before = self.rows()
            call_command('import_catalogue', stdout=StringIO(), verbosity=0)
        self.assertEqual(self.rows(), before)
