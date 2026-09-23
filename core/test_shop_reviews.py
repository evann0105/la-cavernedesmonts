from django.test import TestCase
from django.template.loader import render_to_string
from django.utils.translation import override
from core.templatetags.shop_reviews import shop_reviews


class ShopReviewTests(TestCase):
    def test_full_selection_has_supplied_ratings_and_visit_dates(self):
        data = shop_reviews()
        self.assertEqual([r['rating'] for r in data['reviews']], [5,5,4])
        with override('fr'):
            html = render_to_string('core/partials/shop_reviews.html', data)
        self.assertIn('Visite en',html)
        self.assertIn('datetime="2023-02"',html)
        self.assertIn('target="_blank" rel="noopener noreferrer"',html)
        self.assertNotIn('aggregateRating',html)

    def test_compact_review_is_about_shop_not_product(self):
        with override('en'):
            html = render_to_string('core/partials/shop_reviews.html',shop_reviews(compact=True))
        self.assertIn('Selected reviews of our shop in Lélex.',html)
        self.assertIn('Clemence Guillaume',html)
        self.assertIn('4/5',html)
        self.assertIn('lang="fr"',html)
        self.assertIn('Très bon accueil ds ce magasin',html)
        self.assertNotIn('Nathalie',html)
        self.assertIn('original language: French.',html)
