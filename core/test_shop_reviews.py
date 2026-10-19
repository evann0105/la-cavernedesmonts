from django.test import SimpleTestCase
from unittest.mock import patch
from django.template.loader import render_to_string
from django.utils.translation import override
from core.templatetags.shop_reviews import shop_reviews, REVIEWS


class ShopReviewTests(SimpleTestCase):
    def test_full_selection_has_supplied_ratings_and_visit_dates(self):
        with patch('core.templatetags.shop_reviews.sample', return_value=list(REVIEWS[:3])):
            data = shop_reviews()
        self.assertEqual([r['rating'] for r in data['reviews']], [5,5,4])
        with override('fr'):
            html = render_to_string('core/partials/shop_reviews.html', data)
        self.assertIn('Visite en',html)
        self.assertIn('datetime="2023-02"',html)
        self.assertNotIn('Consulter les avis Google',html)
        self.assertIn('Comment ces avis sont présentés',html)
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


    def test_rotation_uses_only_written_four_and_five_star_reviews(self):
        extra = (
            {'author':'Below threshold', 'rating':3, 'quote':'Not eligible'},
            {'author':'No text', 'rating':5, 'quote':'  '},
            {'author':'Invalid rating', 'rating':6, 'quote':'Not eligible'},
        )
        with patch('core.templatetags.shop_reviews.REVIEWS', REVIEWS + extra), patch('core.templatetags.shop_reviews.sample', side_effect=lambda items, count: items[-count:]) as choose:
            data=shop_reviews()
        candidates, count=choose.call_args.args
        self.assertEqual(len(candidates),6)
        self.assertEqual(count,3)
        self.assertTrue(all(4 <= review['rating'] <= 5 for review in candidates))
        self.assertEqual(len({review['author'] for review in data['reviews']}),3)
        self.assertEqual(data['reviews'],list(REVIEWS[-3:]))

    def test_date_is_omitted_when_not_supplied(self):
        with override('fr'):
            html=render_to_string('core/partials/shop_reviews.html', {'reviews':[REVIEWS[3]]})
        self.assertNotIn('Visite en',html)
        self.assertNotIn('<time',html)
        self.assertIn('sophie Giradet',html)
        self.assertIn('Sélection d’avis sur la boutique de Lélex.',html)

    def test_small_or_empty_pool_is_supported(self):
        with patch('core.templatetags.shop_reviews.REVIEWS',REVIEWS[:1]):
            self.assertEqual(shop_reviews()['reviews'],list(REVIEWS[:1]))
        with patch('core.templatetags.shop_reviews.REVIEWS',()):
            data=shop_reviews()
            self.assertEqual(data['reviews'],[])
            self.assertNotIn('<section',render_to_string('core/partials/shop_reviews.html',data))
