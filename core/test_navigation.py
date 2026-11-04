from django.test import TestCase


class BackNavigationTests(TestCase):
    def test_interior_link_and_home_without_link(self):
        self.assertContains(self.client.get('/produits/'), 'aria-label="Retour à la page précédente"')
        self.assertNotContains(self.client.get('/'), 'aria-label="Retour à la page précédente"')

    def test_local_previous_page_keeps_filters(self):
        response = self.client.get('/magasins/', HTTP_REFERER='http://testserver/produits/?categorie=femmes&tri=prix')
        self.assertEqual(response.context['back_url'], '/produits/?categorie=femmes&tri=prix')
        self.assertTrue(response.context['back_use_history'])
        self.assertContains(response, 'href="/" data-back-history class="page-back-link"')

    def test_external_and_same_page_referers_use_home(self):
        for referer in ('https://example.com/', '//evil.example/', 'javascript:alert(1)', 'http://testserver/produits/', 'http://testserver/accounts/login/'):
            response = self.client.get('/produits/', HTTP_REFERER=referer)
            self.assertEqual(response.context['back_url'], '/')
            self.assertFalse(response.context['back_use_history'])
            self.assertNotContains(response, 'data-back-history')

    def test_old_location_address_redirects_to_current_shop_page(self):
        self.assertRedirects(self.client.get('/localisation/'), '/magasins/', status_code=301)

    def test_password_recovery_back_links_always_return_to_login(self):
        from django.urls import reverse
        paths = [reverse('connexion:' + name) for name in (
            'password_reset', 'password_reset_done', 'password_reset_complete',
        )]
        paths.append(reverse('connexion:password_reset_confirm', kwargs={'uidb64': 'MQ', 'token': 'invalid-token'}))
        for path in paths:
            with self.subTest(path=path):
                page = self.client.get(path, HTTP_REFERER='http://testserver/produits/')
                self.assertEqual(page.context['back_url'], reverse('connexion:login'))
                self.assertFalse(page.context['back_use_history'])
