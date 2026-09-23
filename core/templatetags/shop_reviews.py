"""Selected shop reviews transcribed by the owner of this project on 2026-09-23.

Visit months are supplied visit dates, not publication dates. No live Google
rating, purchase verification or product-specific endorsement is inferred.
The Google link opens the shop's reviews search, not an individual review.
"""
from datetime import date
from django import template

register = template.Library()
REVIEWS = (
    {'author': 'Nathalie Largillet', 'rating': 5, 'visited': date(2021, 10, 1),
     'quote': 'Accueil très chaleureux et magasin super. Je trouve toujours mon bonheur. Je conseille fortement.'},
    {'author': 'Annette Morel', 'rating': 5, 'visited': date(2023, 2, 1),
     'quote': 'Prix - qualité : parfait.\nVendeuse: très sympathique.'},
    {'author': 'Clemence Guillaume', 'rating': 4, 'visited': date(2021, 2, 1),
     'quote': 'Très bon accueil ds ce magasin et de bons conseils sont donnés aux clients concernant leurs achats.'},
)


@register.inclusion_tag('core/partials/shop_reviews.html')
def shop_reviews(compact=False):
    return {
        'compact': compact,
        'reviews': REVIEWS[2:] if compact else REVIEWS,
        'reviews_url': 'https://www.google.com/search?q=LA+CAVERNE+DES+MONTS+L%C3%A9lex+avis',
    }
