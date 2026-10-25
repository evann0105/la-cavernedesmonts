"""Staff-facing catalogue history, previews and HTTP reversal protections."""
from decimal import Decimal

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from connexion.test_helpers import verify_test_user
from .catalog_bulk import apply_bulk_confirmation, prepare_bulk_preview, selection_snapshot
from .models import CatalogChange, Category, Product


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class CatalogHistoryViewTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user('history-ui-manager', is_staff=True)
        self.manager.groups.add(Group.objects.get(name='Gestion du catalogue'))
        verify_test_user(self.manager)
        self.category = Category.objects.create(name='Femmes', slug='femmes-history-ui')
        self.other_category = Category.objects.create(name='Montagne', slug='montagne-history-ui')
        self.first = Product.objects.create(
            name='Polaire rouge', price='80.00', category=self.category,
            brand='Marque initiale', is_published=False,
            description='Description rouge', static_image='products/red.webp')
        self.second = Product.objects.create(
            name='Polaire verte', price='90.00', category=self.category,
            brand='Autre marque', is_published=False,
            description='Description verte', static_image='products/green.webp')
        self.login(self.manager)

    def login(self, user, client=None):
        client = client or self.client
        device, _ = TOTPDevice.objects.get_or_create(user=user, name='default', defaults={'confirmed': True})
        client.force_login(user, backend='connexion.backends.EmailOrUsernameBackend')
        session = client.session
        session['otp_device_id'] = device.persistent_id
        session.save()

    def apply(self, changes=None, product_ids=None):
        products = list(Product.objects.filter(pk__in=product_ids or [self.first.pk, self.second.pk]).order_by('pk'))
        _, token = prepare_bulk_preview(
            self.manager, [product.pk for product in products], changes or {'brand': 'Peak Mountain'},
            selection_snapshot(self.manager, products))
        apply_bulk_confirmation(self.manager, token)
        return CatalogChange.objects.filter(kind='bulk').latest('pk')

    def url(self, name, change=None):
        return reverse('core:' + name, args=[change.pk] if change else None)

    def preview(self, change, client=None):
        response = (client or self.client).get(self.url('catalog_history_undo', change))
        self.assertEqual(response.status_code, 200)
        return response, response.context['form']['confirmation'].value()

    def test_history_and_reversal_are_reserved_to_catalogue_managers(self):
        change = self.apply()
        urls = [self.url('catalog_history'), self.url('catalog_activity'),
                self.url('catalog_history_detail', change), self.url('catalog_history_undo', change)]
        self.client.logout()
        for url in urls:
            with self.subTest(access='anonymous', url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn('next=', response.url)
        for name, is_staff in [('history-customer', False), ('history-other-staff', True)]:
            user = get_user_model().objects.create_user(name, is_staff=is_staff)
            verify_test_user(user)
            self.login(user)
            for url in urls:
                with self.subTest(access=name, url=url):
                    self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(urls[-1], {'confirmation': 'anything'}).status_code, 403)
        self.assertEqual(CatalogChange.objects.count(), 1)
        self.assertFalse(CatalogChange.objects.filter(undone_at__isnull=False).exists())

    def test_history_endpoints_are_private_read_only_pages(self):
        change = self.apply()
        for name in ('catalog_history', 'catalog_activity', 'catalog_history_detail'):
            url = self.url(name, change if name.endswith('detail') else None)
            with self.subTest(name=name):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')
                self.assertIn('private', response['Cache-Control'])
                self.assertIn('no-store', response['Cache-Control'])
                self.assertEqual(self.client.post(url).status_code, 405)
        self.assertEqual(CatalogChange.objects.count(), 1)

    def test_completed_batch_is_listed_with_actor_products_and_detail(self):
        change = self.apply({'brand': 'Peak Mountain', 'price': '70.50'})
        response = self.client.get(self.url('catalog_history'))
        self.assertContains(response, '2 articles')
        self.assertContains(response, self.manager.get_username())
        self.assertContains(response, self.first.name)
        self.assertContains(response, self.second.name)
        self.assertContains(response, self.url('catalog_history_detail', change))
        detail = self.client.get(self.url('catalog_history_detail', change))
        self.assertContains(detail, 'Avant')
        self.assertContains(detail, 'Après')
        self.assertContains(detail, '80,00 €')
        self.assertContains(detail, '90,00 €')
        self.assertContains(detail, '70,50 €')
        self.assertContains(detail, 'Marque initiale')
        self.assertContains(detail, 'Peak Mountain')
        self.assertContains(detail, self.url('catalog_history_undo', change))

    def test_preview_get_describes_reversal_without_writing(self):
        change = self.apply({'brand': 'Peak Mountain', 'price': '70.50'})
        history_count, log_count = CatalogChange.objects.count(), LogEntry.objects.count()
        before_products = list(Product.objects.values('pk', 'brand', 'price'))
        response, token = self.preview(change)
        self.assertTrue(token)
        self.assertContains(response, 'Valeur actuelle')
        self.assertContains(response, 'Valeur à rétablir')
        self.assertContains(response, 'Confirmer l’annulation')
        first_row = response.context['rows'][0]
        values = {detail['label']: (detail['before'], detail['after']) for detail in first_row['details']}
        self.assertIn(('Peak Mountain', 'Marque initiale'), values.values())
        self.assertIn(('70,50 €', '80,00 €'), values.values())
        self.assertEqual(list(Product.objects.values('pk', 'brand', 'price')), before_products)
        self.assertEqual((CatalogChange.objects.count(), LogEntry.objects.count()), (history_count, log_count))
        change.refresh_from_db()
        self.assertIsNone(change.undone_at)

    def test_confirmed_undo_redirects_and_repeated_submission_is_idempotent(self):
        change = self.apply({'brand': 'Peak Mountain', 'price': '70.50'})
        _, token = self.preview(change)
        response = self.client.post(self.url('catalog_history_undo', change), {'confirmation': token})
        self.assertRedirects(response, self.url('catalog_history_detail', change))
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertEqual((self.first.brand, self.first.price), ('Marque initiale', Decimal('80.00')))
        self.assertEqual((self.second.brand, self.second.price), ('Autre marque', Decimal('90.00')))
        detail = self.client.get(response.url)
        self.assertContains(detail, 'Modification annulée le')
        self.assertNotContains(detail, 'Préparer l’annulation')
        inverse = CatalogChange.objects.get(kind='undo')
        inverse_detail = self.client.get(self.url('catalog_history_detail', inverse))
        self.assertContains(inverse_detail, 'Voir la modification d’origine')
        self.assertNotContains(inverse_detail, 'Préparer l’annulation')
        log_count = LogEntry.objects.count()
        repeated = self.client.post(self.url('catalog_history_undo', change), {'confirmation': token}, follow=True)
        self.assertContains(repeated, 'Cette modification avait déjà été annulée.')
        self.assertEqual(CatalogChange.objects.filter(kind='undo').count(), 1)
        self.assertEqual(LogEntry.objects.count(), log_count)

    def test_undo_post_requires_a_valid_csrf_token(self):
        change = self.apply()
        client = Client(enforce_csrf_checks=True)
        self.login(self.manager, client)
        _, token = self.preview(change, client)
        url = self.url('catalog_history_undo', change)
        self.assertEqual(client.post(url, {'confirmation': token}).status_code, 403)
        change.refresh_from_db()
        self.assertIsNone(change.undone_at)
        response = client.post(url, {'confirmation': token,
                                    'csrfmiddlewaretoken': client.cookies['csrftoken'].value})
        self.assertEqual(response.status_code, 302)
        change.refresh_from_db()
        self.assertIsNotNone(change.undone_at)

    def test_token_from_another_operation_cannot_undo_the_selected_one(self):
        first_change = self.apply(product_ids=[self.first.pk])
        second_change = self.apply(product_ids=[self.second.pk])
        _, token = self.preview(first_change)
        response = self.client.post(self.url('catalog_history_undo', second_change), {'confirmation': token})
        self.assertContains(response, 'ne correspond pas à cette opération', status_code=409)
        self.assertNotContains(response, 'Confirmer l’annulation', status_code=409)
        self.assertFalse(CatalogChange.objects.filter(undone_at__isnull=False).exists())
        self.assertEqual(Product.objects.filter(brand='Peak Mountain').count(), 2)

    def test_missing_confirmation_returns_a_useful_error_without_reversing(self):
        change = self.apply()
        response = self.client.post(self.url('catalog_history_undo', change), {})
        self.assertContains(response, 'Reprenez l’aperçu', status_code=400)
        self.assertContains(response, 'Vérifier à nouveau', status_code=400)
        self.assertNotContains(response, 'Confirmer l’annulation', status_code=400)
        self.assertEqual(CatalogChange.objects.count(), 1)

    def test_concurrent_edit_returns_409_with_product_and_recovery_action(self):
        change = self.apply()
        _, token = self.preview(change)
        Product.objects.filter(pk=self.second.pk).update(brand='Correction récente')
        response = self.client.post(self.url('catalog_history_undo', change), {'confirmation': token})
        self.assertContains(response, self.second.name, status_code=409)
        self.assertContains(response, 'Aucun article n’a été modifié', status_code=409)
        self.assertContains(response, 'Retour au détail', status_code=409)
        self.assertContains(response, 'Vérifier à nouveau', status_code=409)
        self.assertNotContains(response, 'Confirmer l’annulation', status_code=409)
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertEqual(self.first.brand, 'Peak Mountain')
        self.assertEqual(self.second.brand, 'Correction récente')
        self.assertEqual(CatalogChange.objects.count(), 1)

    def test_archived_original_category_prevents_restore_and_explains_next_action(self):
        change = self.apply({'category': self.other_category.pk})
        _, token = self.preview(change)
        Category.objects.filter(pk=self.category.pk).update(archived_at=timezone.now(), show_in_menu=False)
        response = self.client.post(self.url('catalog_history_undo', change), {'confirmation': token})
        self.assertContains(response, 'Restaurez-la avant d’annuler', status_code=409)
        self.assertNotContains(response, 'Confirmer l’annulation', status_code=409)
        self.assertEqual(Product.objects.filter(category=self.other_category).count(), 2)
        self.assertEqual(CatalogChange.objects.count(), 1)

    def test_search_matches_product_actor_and_recorded_values(self):
        first_change = self.apply(product_ids=[self.first.pk])
        self.apply({'brand': 'Marque concurrente'}, product_ids=[self.second.pk])
        for query in ('rouge', 'Marque initiale', 'Peak Mountain'):
            with self.subTest(query=query):
                response = self.client.get(self.url('catalog_history'), {'q': query})
                self.assertEqual([item.pk for item in response.context['page_obj']], [first_change.pk])
        response = self.client.get(self.url('catalog_history'), {'q': self.manager.get_username()})
        self.assertEqual(response.context['page_obj'].paginator.count, 2)
        empty = self.client.get(self.url('catalog_history'), {'q': 'introuvable'})
        self.assertContains(empty, 'Aucune opération correspondante.')

    def test_state_filters_separate_active_reversed_and_restoration_records(self):
        undone = self.apply(product_ids=[self.first.pk])
        active = self.apply(product_ids=[self.second.pk])
        _, token = self.preview(undone)
        self.client.post(self.url('catalog_history_undo', undone), {'confirmation': token})
        inverse = CatalogChange.objects.get(kind='undo')
        for state, expected in [('active', active), ('undone', undone), ('undo', inverse)]:
            with self.subTest(state=state):
                response = self.client.get(self.url('catalog_history'), {'etat': state})
                self.assertEqual([change.pk for change in response.context['page_obj']], [expected.pk])
        response = self.client.get(self.url('catalog_history'), {'etat': 'unknown'})
        self.assertEqual(response.context['state'], '')
        self.assertEqual(response.context['page_obj'].paginator.count, 3)

    def test_history_pagination_keeps_filters_and_handles_invalid_page(self):
        template = self.apply()
        for _ in range(21):
            CatalogChange.objects.create(kind='bulk', actor=self.manager,
                actor_label=self.manager.get_username(), items=template.items)
        response = self.client.get(self.url('catalog_history'), {'q': 'Peak Mountain', 'etat': 'active'})
        self.assertEqual(len(response.context['page_obj']), 20)
        self.assertContains(response, '?q=Peak+Mountain&amp;etat=active&amp;page=2')
        last = self.client.get(self.url('catalog_history'), {'q': 'Peak Mountain', 'etat': 'active', 'page': '999'})
        self.assertEqual(last.context['page_obj'].number, 2)
        self.assertEqual(len(last.context['page_obj']), 2)
        self.assertContains(last, '?q=Peak+Mountain&amp;etat=active&amp;page=1')
        invalid = self.client.get(self.url('catalog_history'), {'page': 'not-a-number'})
        self.assertEqual(invalid.context['page_obj'].number, 1)

    def test_historical_values_are_escaped_and_survive_later_product_renaming(self):
        Product.objects.filter(pk=self.first.pk).update(name='<script>alert("name")</script>')
        change = self.apply({'brand': '<img src=x onerror=alert(1)>'}, product_ids=[self.first.pk])
        Product.objects.filter(pk=self.first.pk).update(name='Nom corrigé')
        response = self.client.get(self.url('catalog_history_detail', change))
        self.assertContains(response, '&lt;script&gt;')
        self.assertContains(response, '&lt;img src=x onerror=alert(1)&gt;')
        self.assertNotContains(response, '<script>alert(')
        self.assertNotContains(response, '<img src=x onerror=')
        self.assertNotContains(response, 'Nom corrigé')

    def test_journal_only_contains_catalogue_events_and_can_search_actions(self):
        self.apply()
        category_event = LogEntry.objects.create(user=self.manager,
            content_type=ContentType.objects.get_for_model(Category), object_id=str(self.category.pk),
            object_repr=self.category.name, action_flag=CHANGE, change_message='Rubrique archivée pour la saison.')
        LogEntry.objects.create(user=self.manager, content_type=ContentType.objects.get_for_model(get_user_model()),
            object_id=str(self.manager.pk), object_repr='Compte privé', action_flag=CHANGE,
            change_message='Information extérieure au catalogue.')
        response = self.client.get(self.url('catalog_activity'))
        self.assertEqual(response.context['page_obj'].paginator.count, 3)
        self.assertContains(response, 'Produit')
        self.assertContains(response, 'Rubrique')
        self.assertNotContains(response, 'Compte privé')
        search = self.client.get(self.url('catalog_activity'), {'q': 'saison'})
        self.assertEqual([entry.pk for entry in search.context['page_obj']], [category_event.pk])
        self.assertContains(search, 'Rubrique archivée pour la saison.')

    def test_journal_pagination_preserves_search_and_escapes_recorded_text(self):
        product_type = ContentType.objects.get_for_model(Product)
        for number in range(26):
            LogEntry.objects.create(user=self.manager, content_type=product_type, object_id=str(self.first.pk),
                object_repr=f'Polaire {number}', action_flag=CHANGE,
                change_message='Correction saison <script>alert(1)</script>')
        response = self.client.get(self.url('catalog_activity'), {'q': 'saison'})
        self.assertEqual(len(response.context['page_obj']), 25)
        self.assertContains(response, '?q=saison&amp;page=2')
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')
        second = self.client.get(self.url('catalog_activity'), {'q': 'saison', 'page': 2})
        self.assertEqual(len(second.context['page_obj']), 1)
        self.assertContains(second, '?q=saison&amp;page=1')

    def test_unknown_operation_and_inverse_undo_are_rejected(self):
        for name in ('catalog_history_detail', 'catalog_history_undo'):
            self.assertEqual(self.client.get(reverse('core:' + name, args=[999999])).status_code, 404)
        change = self.apply()
        _, token = self.preview(change)
        self.client.post(self.url('catalog_history_undo', change), {'confirmation': token})
        inverse = CatalogChange.objects.get(kind='undo')
        response = self.client.get(self.url('catalog_history_undo', inverse))
        self.assertContains(response, 'ne peut pas être annulée', status_code=409)
        self.assertNotContains(response, 'Confirmer l’annulation', status_code=409)
