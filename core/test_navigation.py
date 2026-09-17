from django.test import TestCase


class BackNavigationTests(TestCase):
    def test_interior_link_and_home_without_link(self):
        self.assertContains(self.client.get('/produits/'), 'aria-label="Retour à la page précédente"')
        self.assertNotContains(self.client.get('/'), 'aria-label="Retour à la page précédente"')

    def test_local_previous_page_keeps_filters(self):
        response = self.client.get('/localisation/', HTTP_REFERER='http://testserver/produits/?categorie=femmes&tri=prix')
        self.assertEqual(response.context['back_url'], '/produits/?categorie=femmes&tri=prix')

    def test_external_and_same_page_referers_use_home(self):
        for referer in ('https://example.com/', '//evil.example/', 'javascript:alert(1)', 'http://testserver/produits/', 'http://testserver/accounts/login/'):
            response = self.client.get('/produits/', HTTP_REFERER=referer)
            self.assertEqual(response.context['back_url'], '/')
