from collections import Counter
from types import SimpleNamespace
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Permission
from django.test import TestCase, override_settings
from django.urls import URLPattern, URLResolver, get_resolver, resolve, reverse
from .context_processors import seller_help
from .registry import ARTICLES, CATEGORIES, MODULES, PAGE_HELP, PAGE_HELP_VARIANTS, available_articles, search_articles

BACKEND = 'connexion.backends.EmailOrUsernameBackend'


def management_routes(patterns=None, prefix='', namespace=''):
    for pattern in patterns or get_resolver().url_patterns:
        path = prefix + str(pattern.pattern)
        if isinstance(pattern, URLResolver):
            nested = namespace + (pattern.namespace + ':' if pattern.namespace else '')
            yield from management_routes(pattern.url_patterns, path, nested)
        elif isinstance(pattern, URLPattern) and path.startswith('gestion/') and pattern.name:
            yield namespace + pattern.name, path


@override_settings(DEBUG=True, LOCAL_SKIP_EMAIL_VERIFICATION=True, EMAIL_DELIVERY_ENABLED=False,
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class SellerHelpTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.seller = users.objects.create_superuser('help-seller', 'seller@example.test', 'test-only')
        cls.customer = users.objects.create_user('help-customer')
        cls.catalog = users.objects.create_user('help-catalog', is_staff=True)
        cls.catalog.user_permissions.add(Permission.objects.get(content_type__app_label='core', codename='manage_catalog'))
        cls.service = users.objects.create_user('help-service', is_staff=True)
        cls.service.user_permissions.add(Permission.objects.get(content_type__app_label='retours', codename='manage_returns'))

    def login(self, user=None):
        self.client.force_login(user or self.seller, backend=BACKEND)

    def test_every_management_route_has_help_or_an_explained_nonpage_exclusion(self):
        from .coverage import NON_PAGE_ROUTES
        routes = dict(management_routes())
        self.assertEqual(set(routes) - set(PAGE_HELP) - set(NON_PAGE_ROUTES), set())
        self.assertEqual(set(PAGE_HELP) - set(routes), set())
        self.assertEqual(set(NON_PAGE_ROUTES) - set(routes), set())
        self.assertTrue(all(NON_PAGE_ROUTES.values()))

    def test_registry_is_complete_and_links_resolve(self):
        articles = [article for module in MODULES for article in module.ARTICLES]
        self.assertEqual(len(articles), len(ARTICLES), 'Duplicate article IDs')
        names = [name for module in MODULES for name in module.PAGE_HELP]
        self.assertFalse([name for name, count in Counter(names).items() if count > 1])
        categories = dict(CATEGORIES)
        for article in articles:
            with self.subTest(article=article['id']):
                self.assertIn(article['category'], categories)
                self.assertIn(article['permission'], {'catalog', 'service', 'seller'})
                self.assertTrue(article['title'] and article['summary'] and article['steps'])
                self.assertLessEqual(len(article['steps']), 6)
                for action in article.get('actions', ()):
                    url = reverse(action['view'], kwargs=action.get('kwargs'))
                    self.assertTrue(url.startswith('/gestion/'), url)
                    self.assertEqual(resolve(url).view_name, action['view'])
        for name, ids in PAGE_HELP.items():
            with self.subTest(page=name):
                self.assertTrue(ids)
                self.assertFalse(set(ids) - set(ARTICLES))
        for variant in PAGE_HELP_VARIANTS:
            self.assertIn(variant['view'], PAGE_HELP)
            self.assertFalse(set(variant['articles']) - set(ARTICLES))

    def test_guide_and_all_articles_are_private_read_only_and_render(self):
        self.login()
        for url in [reverse('aide:index'), *[reverse('aide:detail', args=[slug]) for slug in ARTICLES]]:
            response = self.client.get(url)
            if response.status_code == 404:
                # A test-only article may be unavailable when EMAIL_TEST_MODE is off.
                self.assertTrue(ARTICLES[url.strip('/').split('/')[-1]].get('test_only'))
                continue
            self.assertEqual(response.status_code, 200, url)
            self.assertIn('private', response['Cache-Control'])
            self.assertIn('no-store', response['Cache-Control'])
            self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')
            self.assertNotContains(response, '<aside class="wrap help-context"')
        self.assertEqual(self.client.post(reverse('aide:index')).status_code, 405)
        self.assertEqual(self.client.post(reverse('aide:detail', args=['utiliser-aide'])).status_code, 405)

    def test_customers_anonymous_and_unverified_staff_cannot_enter(self):
        url = reverse('aide:index')
        self.assertEqual(self.client.get(url).status_code, 302)
        self.login(self.customer)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.login()
        with override_settings(LOCAL_SKIP_EMAIL_VERIFICATION=False):
            self.assertRedirects(self.client.get(url), reverse('connexion:verify_email'), fetch_redirect_response=False)

    def test_articles_and_actions_follow_existing_permissions(self):
        for user, permitted in ((self.catalog, {'seller', 'catalog'}), (self.service, {'seller', 'service'})):
            self.login(user)
            self.assertEqual(self.client.get(reverse('aide:index')).status_code, 200)
            available = available_articles(user)
            self.assertTrue(available)
            for article in available:
                self.assertIn(article['permission'], permitted)
                for action in article['actions']:
                    self.assertIn(action.get('permission', 'seller'), permitted)
            forbidden = next(article for article in ARTICLES.values() if article['permission'] not in permitted)
            self.assertEqual(self.client.get(reverse('aide:detail', args=[forbidden['id']])).status_code, 404)

    def test_search_finds_user_tasks_with_accents_or_punctuation(self):
        for query in ('ajouter une taille', 'corriger un stock', 'annuler une commande', 'livraison', 'facture'):
            with self.subTest(query=query):
                self.assertTrue(search_articles(self.seller, query), query)
        self.assertEqual([a['id'] for a in search_articles(self.seller, 'expédition')],
                         [a['id'] for a in search_articles(self.seller, 'EXPEDITION')])
        self.assertEqual(search_articles(self.seller, 'annuler une commande')[0]['id'], 'service-annuler-commande')
        self.login()
        response = self.client.get(reverse('aide:index'), {'q': 'annuler une commande'})
        self.assertEqual(response.context['help_groups'][0]['key'], 'commandes')
        self.assertContains(response, 'id="aide-commandes" open')
        response = self.client.get(reverse('aide:index'), {'q': '<script>alert(1)</script>', 'rubrique': 'inconnue'})
        self.assertContains(response, 'Aucune réponse trouvée')
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertEqual(response.context['selected_category'], '')

    def test_category_filter_and_unknown_article(self):
        self.login()
        response = self.client.get(reverse('aide:index'), {'rubrique': 'stocks'})
        self.assertTrue(response.context['help_groups'])
        self.assertTrue(all(group['key'] == 'stocks' for group in response.context['help_groups']))
        self.assertEqual(self.client.get(reverse('aide:detail', args=['absent'])).status_code, 404)

    def test_contextual_help_is_specific_and_excludes_customer_and_previews(self):
        for view_name in ('tableau:home', 'core:catalog_add', 'stocks:overview', 'expeditions:overview'):
            url = reverse(view_name)
            request = SimpleNamespace(path=url, resolver_match=resolve(url), user=self.seller)
            result = seller_help(request)
            self.assertTrue(result['seller_page_help'])
            self.assertEqual([a['id'] for a in result['seller_page_help']], list(PAGE_HELP[view_name]))
        for path in ('/mon-compte/', '/produits/', '/gestion/produits/1/apercu/', '/gestion/aide/'):
            self.assertEqual(seller_help(SimpleNamespace(path=path, resolver_match=resolve(path), user=self.seller)), {})
        self.assertEqual(seller_help(SimpleNamespace(path='/gestion/', resolver_match=resolve('/gestion/'), user=self.customer)), {})

    def test_contextual_answers_are_rendered_once_outside_page_forms(self):
        self.login()
        for name in ('tableau:home', 'tableau:tools', 'core:catalog_add'):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, '<aside class="wrap help-context"', count=1)
            for slug in PAGE_HELP[name]:
                self.assertContains(response, ARTICLES[slug]['title'])
            self.assertContains(response, 'Ouvrir le guide complet')

    @override_settings(DEBUG=False, EMAIL_TEST_MODE=False)
    def test_test_mail_help_is_hidden_outside_test_mode(self):
        from connexion.models import EmailVerification
        from django.utils import timezone
        EmailVerification.objects.update_or_create(user=self.seller, defaults={
            'verified_email': self.seller.email, 'verified_at': timezone.now(),
        })
        self.assertFalse(any(article.get('test_only') for article in available_articles(self.seller)))
        self.login()
        self.assertEqual(self.client.get(reverse('aide:detail', args=['consulter-mails-test'])).status_code, 404)
